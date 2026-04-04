import hashlib
import os
import datetime
import json

import requests
from dotenv import load_dotenv

try:
    from linkedin.token_refresh import TokenManager
except ImportError:
    from ..linkedin.token_refresh import TokenManager

load_dotenv()

_REQUEST_TIMEOUT = 45
_VERBOSE = os.getenv("VERBOSE_BUFFER_LOGS", "").lower() in ("1", "true", "yes")

_channel_cache: dict[str, tuple[str, str]] = {}


def _channel_cache_key(token: str) -> str:
    return hashlib.sha256(("facebook" + token).encode()).hexdigest()


class FacebookPoster:
    def __init__(self, content, assets=None):
        self.content = content
        self.assets = assets or []
        
        # Buffer token from .env — Shared with LinkedIn (LINKEDIN_FB_BUFFER_ACCESS_TOKEN)
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
                print(f"[OK] FACEBOOK: channel {self.channel_name} [{self.channel_id}] (cached)")
            return

        # Optional: target a specific Facebook channel by name
        target_name = (os.getenv("FACEBOOK_CHANNEL_NAME") or "").strip().lower()

        query = """
            query { account { organizations { channels { id name service } } } }
        """
        status, data = self.graphql_query(query)
        if status != 200:
            raise Exception(f"Failed to fetch Buffer channels: {status}")
            
        orgs = data.get("data", {}).get("account", {}).get("organizations", [])
        fallback_channel = None  # First Facebook channel we find (not Fixfield)
        
        for org in orgs:
            channels = org.get("channels", [])
            for channel in channels:
                if "facebook" not in channel.get("service", "").lower():
                    continue
                
                ch_name = channel.get("name", "")
                
                # If a target name is set, match exactly
                if target_name and target_name in ch_name.lower():
                    self.channel_id = channel["id"]
                    self.channel_name = f"{ch_name} ({channel['service']})"
                    break
                
                # Otherwise, prefer non-Fixfield channels
                if not target_name:
                    if "fixfield" in ch_name.lower():
                        # Keep as last resort, but keep looking
                        if not fallback_channel:
                            fallback_channel = channel
                        continue
                    # This is the one we want
                    self.channel_id = channel["id"]
                    self.channel_name = f"{ch_name} ({channel['service']})"
                    break
            if self.channel_id:
                break
        
        # If we didn't find a preferred channel, use fallback (Fixfield)
        if not self.channel_id and fallback_channel:
            self.channel_id = fallback_channel["id"]
            self.channel_name = f"{fallback_channel['name']} ({fallback_channel['service']})"
        
        if not self.channel_id:
            raise Exception("No Facebook channel found for the provided token.")
        _channel_cache[key] = (self.channel_id, self.channel_name)
        if _VERBOSE:
            print(f"[OK] FACEBOOK: channel {self.channel_name} [{self.channel_id}]")

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
                "metadata": {
                    "facebook": {
                        "type": "post"
                    }
                }
            }
        }
        
        if self.assets:
            variables["input"]["assets"] = {}
            
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
                variables["input"]["metadata"]["facebook"]["type"] = "video"

        status_post, data_post = self.graphql_query(mutation, variables)

        if _VERBOSE:
            print(f"Facebook createPost HTTP {status_post}")
            print(json.dumps(data_post, indent=2, ensure_ascii=True))

        if "errors" in data_post:
            error_msgs = [e.get("message", "Unknown error") for e in data_post["errors"]]
            raise Exception("GraphQL Error: " + ", ".join(error_msgs))

        post_result = data_post.get("data", {}).get("createPost", {})
        if post_result.get("__typename") != "PostActionSuccess":
            error_msg = post_result.get("message", "Unknown error creating post")
            raise Exception(f"Buffer API Error: {error_msg}")

        post_data = post_result.get("post", {})
        link = post_data.get("externalLink")
        post_id = post_data.get("id")
        
        # We no longer poll here to avoid Vercel 10s timeouts.
        # The frontend will now call a dedicated check-link endpoint.
        if not link:
            # Prepare a fallback in case polling never succeeds
            username = self.channel_name.split(' ')[0] if self.channel_name else ""
            return {"link": None, "post_id": post_id, "fallback": f"https://www.facebook.com/{username}"}
            
        return {"link": link, "post_id": post_id, "fallback": None}

if __name__ == "__main__":
    post_content = f"Hello! This is a test post from my custom Buffer API script! Time: {datetime.datetime.now()}"
    try:
        poster = FacebookPoster(post_content)
        if poster.channel_id:
            link = poster.create_post()
            print(f"Success! Post created: {link}")
        else:
            print("No Facebook channel found.")
    except Exception as e:
        print(f"Error: {e}")
