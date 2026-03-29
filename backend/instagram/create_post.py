import hashlib
import os
import datetime
import json

import requests
import time
from dotenv import load_dotenv
from .token_refresh import TokenManager

load_dotenv()

_REQUEST_TIMEOUT = 8
_VERBOSE = os.getenv("VERBOSE_BUFFER_LOGS", "").lower() in ("1", "true", "yes")

_channel_cache: dict[str, tuple[str, str]] = {}


def _channel_cache_key(token: str) -> str:
    return hashlib.sha256(("insta" + token).encode()).hexdigest()


class InstagramPoster:
    def __init__(self, content, image_urls=None):
        self.content = content
        self.image_urls = image_urls
        
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
                    self.channel_name = channel["name"]
                    break
            if self.channel_id:
                break
        
        if not self.channel_id:
            raise Exception("No Instagram channel found for the provided token.")
        _channel_cache[key] = (self.channel_id, self.channel_name)
        if _VERBOSE:
            print(f"[OK] Instagram: channel {self.channel_name} [{self.channel_id}]")

    def create_post(self):
        if not self.image_urls:
            raise ValueError(
                "Instagram requires at least one image/video for publication. "
                "Please upload an image and try again."
            )

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

        # Determine if it's a single post or carousel
        num_images = len(self.image_urls) if self.image_urls else 0
        is_carousel = num_images > 1
        
        variables = {
            "input": {
                "channelId": self.channel_id,
                "text": self.content,
                "mode": "shareNow",
                "schedulingType": "automatic",
                "metadata": {
                    "instagram": {
                        "type": "carousel" if is_carousel else "post",
                        "shouldShareToFeed": True
                    }
                }
            }
        }
        
        if self.image_urls:
            variables["input"]["assets"] = {
                "images": [
                    {"url": url} for url in self.image_urls
                ]
            }

        start_time = time.time()
        status_post, data_post = None, {}
        try:
            status_post, data_post = self.graphql_query(mutation, variables)
        except requests.Timeout:
            return "Success (Processing: Mutation timed out, but post likely created)"

        if _VERBOSE:
            print(f"Instagram createPost HTTP {status_post}")
            print(json.dumps(data_post, indent=2, ensure_ascii=True))

        if "errors" in data_post:
            msgs = [e.get("message", "Unknown GraphQL error") for e in data_post["errors"]]
            # Include specific reason if present
            for e in data_post["errors"]:
                if "extensions" in e and "error" in e["extensions"]:
                    msgs.append(f"Reason: {e['extensions']['error']}")
            raise Exception(" | ".join(msgs))

        post_result = data_post.get("data", {}).get("createPost", {})
        typename = post_result.get("__typename")
        
        if typename == "PostActionSuccess":
            post_data = post_result.get("post", {})
            return {
                "id": post_data.get("id"),
                "link": post_data.get("externalLink"),
                "handle": self.channel_name
            }
        else:
            # Handle various error typenames (InvalidInputError, UnexpectedError, etc.)
            error_msg = post_result.get("message") or "Unknown Buffer API error"
            error_code = post_result.get("code") or "N/A"
            raise Exception(f"Buffer API Error ({typename}): {error_msg} (Code: {error_code})")

    def get_post_status(self, post_id: str):
        query = """
        query GetPostStatus($id: ID!) {
          node(id: $id) {
            ... on Post {
              id
              status
              externalLink
            }
          }
        }
        """
        status_code, data = self.graphql_query(query, {"id": post_id})
        if status_code != 200:
            raise Exception(f"Failed to fetch post status: {status_code}")
            
        post_info = data.get("data", {}).get("node")
        if not post_info:
            return {"status": "pending", "link": None}
            
        return {
            "status": post_info.get("status", "").lower(),
            "link": post_info.get("externalLink")
        }

if __name__ == "__main__":
    post_content = f"Hello! This is a test post from my custom Buffer API script! Time: {datetime.datetime.now()}"
    insta_poster = InstagramPoster(post_content)
