import hashlib
import os
import datetime
import json
import time

import requests
from dotenv import load_dotenv
from .token_refresh import TokenManager

load_dotenv()

_REQUEST_TIMEOUT = 45
_VERBOSE = os.getenv("VERBOSE_BUFFER_LOGS", "").lower() in ("1", "true", "yes")

# Avoid re-fetching channel list on every post (same process)
_channel_cache: dict[str, tuple[str, str]] = {}


def _channel_cache_key(token: str) -> str:
    return hashlib.sha256(("linkedin" + token).encode()).hexdigest()


class LinkedIn:
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
    # Core GraphQL helper
    # ------------------------------------------------------------------

    def graphql_query(self, query, variables=None):
        payload = {"query": query}
        if variables:
            payload["variables"] = variables
        res = self._http.post(
            self.graphql_url,
            json=payload,
            timeout=_REQUEST_TIMEOUT,
        )
        return res.status_code, res.json()

    # ------------------------------------------------------------------
    # Channel resolution
    # ------------------------------------------------------------------

    def fetch_channel_id(self):
        key = _channel_cache_key(self.access_token)
        if key in _channel_cache:
            self.channel_id, self.channel_name = _channel_cache[key]
            if _VERBOSE:
                print(f"[OK] LINKEDIN: channel {self.channel_name} [{self.channel_id}] (cached)")
            return

        query = """
            query { account { organizations { channels { id name service } } } }
        """
        status, data = self.graphql_query(query)

        orgs = data.get("data", {}).get("account", {}).get("organizations", [])
        for org in orgs:
            for channel in org.get("channels", []):
                if channel.get("service") == "linkedin":
                    self.channel_id = channel["id"]
                    self.channel_name = f"{channel['name']} ({channel['service']})"
                    break
            if self.channel_id:
                break

        if not self.channel_id:
            raise Exception("No LinkedIn channel found for the provided LinkedIn token.")

        _channel_cache[key] = (self.channel_id, self.channel_name)
        if _VERBOSE:
            print(f"[OK] LINKEDIN: channel {self.channel_name} [{self.channel_id}]")

    # ------------------------------------------------------------------
    # Fallback URL helper
    # ------------------------------------------------------------------

    def _fallback_url(self) -> str:
        username = self.channel_name.split(" ")[0] if self.channel_name else ""
        if self.channel_name and "Fixfield" in self.channel_name:
            return f"https://www.linkedin.com/company/{username}"
        return "https://www.linkedin.com/feed/"

    # ------------------------------------------------------------------
    # Create post
    # ------------------------------------------------------------------

    def create_post(self):
        mutation = """
        mutation CreateTestPost($input: CreatePostInput!) {
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
            }
        }

        if self.assets:
            variables["input"]["assets"] = {}

            images = [a["url"] for a in self.assets if a["type"] == "image"]
            videos = [a for a in self.assets if a["type"] == "video"]
            docs   = [a for a in self.assets if a["type"] == "document"]

            if images:
                variables["input"]["assets"]["images"] = [{"url": url} for url in images]

            if videos:
                variables["input"]["assets"]["video"] = {
                    "url": videos[0]["url"],
                    "title": "Video Post",
                    "thumbnailUrl": videos[0]["thumbnail"],
                }

            if docs:
                variables["input"]["assets"]["documents"] = [
                    {
                        "url": docs[0]["url"],
                        "title": docs[0].get("title", "Document"),
                        "thumbnailUrl": docs[0]["thumbnail"],
                    }
                ]

        status_post, data_post = self.graphql_query(mutation, variables)

        if _VERBOSE:
            print(f"LinkedIn createPost HTTP {status_post}")
            print(json.dumps(data_post, indent=2, ensure_ascii=True))

        if "errors" in data_post:
            error_msgs = [e.get("message", "Unknown error") for e in data_post["errors"]]
            raise Exception("GraphQL Error: " + ", ".join(error_msgs))

        post_result = data_post.get("data", {}).get("createPost", {})
        if post_result.get("__typename") != "PostActionSuccess":
            error_msg = post_result.get("message", "Unknown error creating post")
            raise Exception(f"Buffer API Error: {error_msg}")

        post_data = post_result.get("post", {})
        link      = post_data.get("externalLink")
        post_id   = post_data.get("id")

        if _VERBOSE:
            print(f"[OK] Post created — id={post_id}, immediate link={link!r}")

        return {"link": link, "post_id": post_id, "fallback": self._fallback_url()}

    # ------------------------------------------------------------------
    # Single-shot link check — NO sleep, NO loop.
    # Your /check-link route calls this once and returns immediately.
    # The FRONTEND polls every 3-4 seconds.
    # ------------------------------------------------------------------

    def get_post_link(self, post_id: str) -> dict:
        """
        One attempt to read externalLink from Buffer. Returns immediately.

        Returns:
            {
                "link":     str | None,
                "post_id":  str,
                "status":   str,   # Buffer post status
                "fallback": str,
                "ready":    bool,  # True only when link is non-null
            }
        """
        query = """
            query GetPost($id: String!) {
                post(id: $id) {
                    id
                    externalLink
                    status
                }
            }
        """

        status_code, data = self.graphql_query(query, {"id": post_id})

        if _VERBOSE:
            print(f"[get_post_link] HTTP {status_code}")
            print(json.dumps(data, indent=2, ensure_ascii=True))

        if "errors" in data:
            error_msgs = [e.get("message", "Unknown") for e in data["errors"]]
            raise Exception("GraphQL error fetching post: " + ", ".join(error_msgs))

        post        = data.get("data", {}).get("post") or {}
        link        = post.get("externalLink")
        post_status = post.get("status", "unknown")

        # Terminal failure — raise so the frontend stops polling immediately
        if post_status in ("failed", "error"):
            raise Exception(f"Buffer post failed with status: {post_status!r}")

        return {
            "link":     link,
            "post_id":  post_id,
            "status":   post_status,
            "fallback": self._fallback_url(),
            "ready":    bool(link),
        }


# ---------------------------------------------------------------------------
# Quick smoke-test (python -m yourmodule.linkedin)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    post_content = (
        f"Hello! This is a test post from my custom Buffer API script! "
        f"Time: {datetime.datetime.now()}"
    )
    li = LinkedIn(post_content)

    result = li.create_post()
    print("create_post result:", result)

    if result["post_id"] and not result["link"]:
        print("Link not ready yet — simulating frontend polling...")
        for attempt in range(1, 9):
            time.sleep(3)
            poll = li.get_post_link(result["post_id"])
            print(f"  Attempt {attempt}: ready={poll['ready']}, link={poll['link']!r}")
            if poll["ready"]:
                break
        else:
            print("  Gave up. Fallback:", result["fallback"])