import hashlib
import os
import datetime
import json

import requests
from dotenv import load_dotenv
from .token_refresh import TokenManager

load_dotenv()

_REQUEST_TIMEOUT = 45
_VERBOSE = os.getenv("VERBOSE_BUFFER_LOGS", "").lower() in ("1", "true", "yes")

_channel_cache: dict[str, tuple[str, str]] = {}


def _channel_cache_key(token: str) -> str:
    return hashlib.sha256(("insta" + token).encode()).hexdigest()


class InstagramPoster:
    def __init__(self, content, assets=None):
        self.content = content
        self.assets = assets or []
        
        # Buffer token from .env — Renamed to X_INSTA_BUFFER_ACCESS_TOKEN
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
        
        orgs = data.get("data", {}).get("account", {}).get("organizations", [])
        for org in orgs:
            channels = org.get("channels", [])
            for channel in channels:
                # Buffer identifies Instagram as 'instagram'
                if channel.get("service") == "instagram":
                    self.channel_id = channel["id"]
                    self.channel_name = f"{channel['name']} ({channel['service']})"
                    break
            if self.channel_id:
                break
        
        if not self.channel_id:
            raise Exception("No Instagram channel found for the provided token.")
        _channel_cache[key] = (self.channel_id, self.channel_name)
        if _VERBOSE:
            print(f"[OK] Instagram: channel {self.channel_name} [{self.channel_id}]")

    def create_post(self):
        if not self.assets:
            raise ValueError(
                "Instagram requires at least one image/video for publication. "
                "Please upload a media file and try again."
            )

        # Buffer's GraphQL character limit for Instagram is 2200.
        if len(self.content) > 2200:
            raise ValueError(
                f"Instagram captions are capped at 2200 characters. "
                f"Your post is {len(self.content)} characters. "
                "Please shorten your content."
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
                        "shouldShareToFeed": True
                    }
                }
            }
        }

        # Group assets
        images = [a["url"] for a in self.assets if a["type"] == "image"]
        videos = [a for a in self.assets if a["type"] == "video"]

        if images:
            variables["input"]["assets"]["images"] = [{"url": url} for url in images]
        
        if videos:
            variables["input"]["assets"]["video"] = {
                "url": videos[0]["url"],
                "title": "Video Post",
                "thumbnailUrl": videos[0]["thumbnail"]
            }
            # Update metadata for video
            variables["input"]["metadata"]["instagram"]["type"] = "video"
        
        status_post, data_post = self.graphql_query(mutation, variables)

        if _VERBOSE:
            print(f"Instagram createPost HTTP {status_post}")
            print(json.dumps(data_post, indent=2, ensure_ascii=True))

        if "errors" in data_post:
            error_msgs = [e.get("message", "Unknown error") for e in data_post["errors"]]
            # Deeply analyze the error message to provide helpful hints.
            full_msg = ", ".join(error_msgs)
            if "media" in full_msg.lower() or "aspect ratio" in full_msg.lower():
                raise Exception(f"Instagram rejected the media format. Check aspect ratio (must be 4:5 to 1.91:1) and try again. Full Error: {full_msg}")
            raise Exception("GraphQL Error: " + full_msg)

        post_result = data_post.get("data", {}).get("createPost", {})
        if post_result.get("__typename") != "PostActionSuccess":
            error_msg = post_result.get("message", "Unknown error creating post")
            # If the error message from Buffer mentions 'delete media', it's usually an aspect ratio issue.
            if "delete media" in error_msg.lower():
                raise Exception(
                    "Instagram rejected the media. This is usually due to an incompatible "
                    "aspect ratio (e.g. 9:16 vertical instead of 4:5). "
                    "Please crop the photo and try again."
                )
            raise Exception(f"Buffer API Error: {error_msg}")

        post_data = post_result.get("post", {})
        link = post_data.get("externalLink")
        post_id = post_data.get("id")
        
        # We no longer poll here to avoid Vercel 10s timeouts.
        # The frontend will now call a dedicated check-link endpoint.
        if not link:
            # Prepare a fallback in case polling never succeeds
            username = self.channel_name.split(' ')[0] if self.channel_name else ""
            return {"link": None, "post_id": post_id, "fallback": f"https://www.instagram.com/{username}"}
            
        return {"link": link, "post_id": post_id, "fallback": None}

if __name__ == "__main__":
    post_content = f"Hello! This is a test post from my custom Buffer API script! Time: {datetime.now()}"
    insta_poster = InstagramPoster(post_content)
