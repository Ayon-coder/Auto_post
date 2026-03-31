import hashlib
import os
import datetime
import json
import time

import requests
from requests.exceptions import Timeout, ConnectionError, RequestException
from dotenv import load_dotenv
from .token_refresh import TokenManager

load_dotenv()

_REQUEST_TIMEOUT = 45
_VERBOSE = os.getenv("VERBOSE_BUFFER_LOGS", "").lower() in ("1", "true", "yes")

_channel_cache: dict[str, tuple[str, str]] = {}


# ---------------------------------------------------------------------------
# Error classification helpers
# ---------------------------------------------------------------------------

class InstagramPostError(Exception):
    """Raised when posting to Instagram fails. Always has a user-friendly message."""
    def __init__(self, message: str, error_code: str = "UNKNOWN", raw: str = ""):
        super().__init__(message)
        self.error_code = error_code
        self.raw = raw  # original error string for debug logging


def _classify_buffer_error(msg: str) -> InstagramPostError:
    """
    Inspect a raw Buffer/GraphQL error message and return a typed
    InstagramPostError with a clear, actionable description.
    """
    m = msg.lower()

    # --- Media / format errors ---
    if any(k in m for k in ("aspect ratio", "media", "delete media", "image size", "video")):
        return InstagramPostError(
            "Instagram rejected the media file. Common causes:\n"
            "  • Wrong aspect ratio — Instagram requires 4:5 to 1.91:1 for feed posts.\n"
            "  • Unsupported file format or codec.\n"
            "  • Image resolution too low or too high.\n"
            "Please re-crop or re-export your file and try again.",
            error_code="MEDIA_REJECTED",
            raw=msg,
        )

    # --- Authentication / token errors ---
    if any(k in m for k in ("token", "unauthorized", "authentication", "oauth", "permission", "scope")):
        return InstagramPostError(
            "Authentication failed. Your Buffer access token may have expired or "
            "lacks the required Instagram permissions. "
            "Please reconnect your Instagram account in Buffer and try again.",
            error_code="AUTH_FAILED",
            raw=msg,
        )

    # --- Rate limiting ---
    if any(k in m for k in ("rate limit", "too many requests", "quota", "throttl")):
        return InstagramPostError(
            "Instagram or Buffer rate limit reached. "
            "You have made too many requests in a short period. "
            "Please wait a few minutes and try again.",
            error_code="RATE_LIMITED",
            raw=msg,
        )

    # --- Caption / text errors ---
    if any(k in m for k in ("caption", "text", "character", "2200")):
        return InstagramPostError(
            "The post caption exceeds Instagram's 2200-character limit. "
            "Please shorten the text and try again.",
            error_code="CAPTION_TOO_LONG",
            raw=msg,
        )

    # --- Account / channel errors ---
    if any(k in m for k in ("account", "channel", "profile", "business", "creator", "not found")):
        return InstagramPostError(
            "The Instagram account could not be found or is not connected to Buffer. "
            "Make sure you have a Business or Creator account and it is linked in Buffer.",
            error_code="ACCOUNT_NOT_FOUND",
            raw=msg,
        )

    # --- Scheduling / timing errors ---
    if any(k in m for k in ("schedule", "time", "slot", "past")):
        return InstagramPostError(
            "The scheduled time is invalid — it may be in the past or conflict with "
            "another scheduled post. Please choose a different time.",
            error_code="SCHEDULE_INVALID",
            raw=msg,
        )

    # --- Network / server errors from Buffer side ---
    if any(k in m for k in ("server error", "internal", "500", "503", "unavailable")):
        return InstagramPostError(
            "Buffer's servers returned an internal error. "
            "This is usually temporary — please wait a moment and try again. "
            "If the problem persists, check Buffer's status page.",
            error_code="SERVER_ERROR",
            raw=msg,
        )

    # --- Unknown fallback ---
    return InstagramPostError(
        f"An unexpected error was returned while publishing your post.\n"
        f"Details: {msg}\n"
        "If this keeps happening, check the Buffer dashboard for more information.",
        error_code="UNKNOWN",
        raw=msg,
    )


# ---------------------------------------------------------------------------
# Channel cache key
# ---------------------------------------------------------------------------

def _channel_cache_key(token: str) -> str:
    return hashlib.sha256(("insta" + token).encode()).hexdigest()


# ---------------------------------------------------------------------------
# InstagramPoster
# ---------------------------------------------------------------------------

class InstagramPoster:
    def __init__(self, content, assets=None):
        self.content = content
        self.assets = assets or []

        token_manager = TokenManager()
        self.access_token = token_manager.get_valid_token()

        self.graphql_url = os.getenv("GRAPHQL_URL", "https://api.buffer.com/graphql")
        self._http = requests.Session()
        self._http.headers.update(
            {
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json",
            }
        )
        self.channel_id = None
        self.channel_name = None

        self.fetch_channel_id()

    # ------------------------------------------------------------------
    # Core request helper — surfaces network/timeout issues immediately
    # ------------------------------------------------------------------

    def graphql_query(self, query, variables=None):
        payload = {"query": query}
        if variables:
            payload["variables"] = variables

        try:
            res = self._http.post(
                self.graphql_url,
                json=payload,
                timeout=_REQUEST_TIMEOUT,
            )
        except Timeout:
            raise InstagramPostError(
                f"The request to Buffer timed out after {_REQUEST_TIMEOUT} seconds. "
                "Buffer may be experiencing delays — please try again shortly.",
                error_code="TIMEOUT",
            )
        except ConnectionError:
            raise InstagramPostError(
                "Could not connect to Buffer's API. "
                "Check your internet connection and try again.",
                error_code="CONNECTION_ERROR",
            )
        except RequestException as exc:
            raise InstagramPostError(
                f"A network error occurred while contacting Buffer: {exc}",
                error_code="NETWORK_ERROR",
                raw=str(exc),
            )

        # Surface non-2xx HTTP responses immediately
        if res.status_code == 401:
            raise InstagramPostError(
                "Buffer returned HTTP 401 — your access token is invalid or expired. "
                "Please reconnect your Instagram account in Buffer.",
                error_code="AUTH_FAILED",
                raw=f"HTTP 401",
            )
        if res.status_code == 429:
            raise InstagramPostError(
                "Buffer returned HTTP 429 — you have been rate limited. "
                "Please wait a few minutes before trying again.",
                error_code="RATE_LIMITED",
                raw="HTTP 429",
            )
        if res.status_code >= 500:
            raise InstagramPostError(
                f"Buffer's servers returned HTTP {res.status_code}. "
                "This is a temporary server-side issue — please try again shortly.",
                error_code="SERVER_ERROR",
                raw=f"HTTP {res.status_code}",
            )

        try:
            data = res.json()
        except ValueError:
            raise InstagramPostError(
                "Buffer returned an unreadable response (not valid JSON). "
                "This is unexpected — please try again or contact Buffer support.",
                error_code="BAD_RESPONSE",
                raw=res.text[:300],
            )

        return res.status_code, data

    # ------------------------------------------------------------------
    # Channel discovery
    # ------------------------------------------------------------------

    def fetch_channel_id(self):
        key = _channel_cache_key(self.access_token)
        if key in _channel_cache:
            self.channel_id, self.channel_name = _channel_cache[key]
            if _VERBOSE:
                print(f"[OK] Instagram: channel {self.channel_name} [{self.channel_id}] (cached)")
            return

        query = """
            query { account { organizations { channels { id name service } } } }
        """
        status, data = self.graphql_query(query)

        # Check for GraphQL-level errors in the channel fetch itself
        if "errors" in data:
            msgs = ", ".join(e.get("message", "unknown") for e in data["errors"])
            raise _classify_buffer_error(msgs)

        orgs = data.get("data", {}).get("account", {}).get("organizations", [])
        for org in orgs:
            for channel in org.get("channels", []):
                if channel.get("service") == "instagram":
                    self.channel_id = channel["id"]
                    self.channel_name = f"{channel['name']} ({channel['service']})"
                    break
            if self.channel_id:
                break

        if not self.channel_id:
            raise InstagramPostError(
                "No Instagram channel was found for this Buffer account. "
                "Please connect an Instagram Business or Creator account in Buffer "
                "and make sure the account has the correct permissions.",
                error_code="ACCOUNT_NOT_FOUND",
            )

        _channel_cache[key] = (self.channel_id, self.channel_name)
        if _VERBOSE:
            print(f"[OK] Instagram: channel {self.channel_name} [{self.channel_id}]")

    # ------------------------------------------------------------------
    # Post creation
    # ------------------------------------------------------------------

    def create_post(self):
        if not self.assets:
            raise InstagramPostError(
                "Instagram requires at least one image or video. "
                "Please attach a media file and try again.",
                error_code="NO_MEDIA",
            )

        if len(self.content) > 2200:
            raise InstagramPostError(
                f"Instagram captions cannot exceed 2200 characters. "
                f"Your caption is {len(self.content)} characters — "
                f"please shorten it by at least {len(self.content) - 2200} characters.",
                error_code="CAPTION_TOO_LONG",
            )

        mutation = """
        mutation CreatePost($input: CreatePostInput!) {
            createPost(input: $input) {
                __typename
                ... on PostActionSuccess {
                    post {
                        id
                        externalLink
                    }
                }
                ... on UnexpectedError {
                    message
                }
                ... on InvalidInputError {
                    message
                }
            }
        }
        """

        variables = {
            "input": {
                "channelId": self.channel_id,
                "text": self.content,
                "mode": "shareNow",
                "schedulingType": "automatic",
                "assets": {},
                "metadata": {
                    "instagram": {
                        "type": "post",
                        "shouldShareToFeed": True,
                    }
                },
            }
        }

        images = [a["url"] for a in self.assets if a["type"] == "image"]
        videos = [a for a in self.assets if a["type"] == "video"]

        if images:
            variables["input"]["assets"]["images"] = [{"url": url} for url in images]

        if videos:
            variables["input"]["assets"]["video"] = {
                "url": videos[0]["url"],
                "title": "Video Post",
                "thumbnailUrl": videos[0]["thumbnail"],
            }
            variables["input"]["metadata"]["instagram"]["type"] = "video"

        status_post, data_post = self.graphql_query(mutation, variables)

        if _VERBOSE:
            print(f"Instagram createPost HTTP {status_post}")
            print(json.dumps(data_post, indent=2, ensure_ascii=True))

        # Top-level GraphQL errors
        if "errors" in data_post:
            error_msgs = ", ".join(e.get("message", "Unknown error") for e in data_post["errors"])
            raise _classify_buffer_error(error_msgs)

        post_result = data_post.get("data", {}).get("createPost", {})
        result_type = post_result.get("__typename")

        if result_type != "PostActionSuccess":
            raw_msg = post_result.get("message", "Unknown error creating post")
            raise _classify_buffer_error(raw_msg)

        post_data = post_result.get("post", {})
        link = post_data.get("externalLink")
        post_id = post_data.get("id")

        if not link:
            link = self._wait_for_link(post_id)

        if link:
            return {"link": link, "post_id": post_id, "fallback": None}

        # Link never arrived — ask the user to check manually
        username = self.channel_name.split(" ")[0] if self.channel_name else ""
        profile_url = f"https://www.instagram.com/{username}"
        return {
            "link": None,
            "post_id": post_id,
            "fallback": profile_url,
            "user_message": (
                "Your post was submitted successfully, but Instagram is taking longer "
                "than usual to confirm the link.\n"
                f"Please check your Instagram profile to verify it went live: {profile_url}"
            ),
        }

    # ------------------------------------------------------------------
    # Poll for the post link (5–7 s window, ~1 s intervals)
    # ------------------------------------------------------------------

    def _wait_for_link(self, post_id: str, timeout: int = 7, interval: float = 1.0) -> str | None:
        """
        Poll Buffer for the externalLink of a just-created post.
        Waits up to `timeout` seconds, checking every `interval` seconds.
        Returns the link string if found, or None if the window expires.
        """
        if not post_id:
            return None

        query = """
        query GetPost($id: String!) {
            post(id: $id) {
                id
                externalLink
            }
        }
        """

        deadline = time.monotonic() + timeout
        attempt = 0

        while time.monotonic() < deadline:
            attempt += 1
            try:
                _, data = self.graphql_query(query, {"id": post_id})
            except InstagramPostError:
                # Don't surface polling errors — just keep waiting
                time.sleep(interval)
                continue

            post = data.get("data", {}).get("post", {})
            link = post.get("externalLink")

            if _VERBOSE:
                elapsed = round(time.monotonic() - (deadline - timeout), 1)
                print(f"[poll {attempt}] +{elapsed}s → link={link!r}")

            if link:
                return link

            time.sleep(interval)

        return None


# ---------------------------------------------------------------------------
# Quick smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    post_content = (
        f"Hello! This is a test post from my custom Buffer API script! "
        f"Time: {datetime.datetime.now()}"
    )
    insta_poster = InstagramPoster(post_content)