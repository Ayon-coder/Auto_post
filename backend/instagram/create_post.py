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

        variables = {
            "input": {
                "channelId": self.channel_id,
                "text": self.content,
                "mode": "shareNow",
                "schedulingType": "automatic",
                "metadata": {
                    "instagram": {
                        "type": "post",
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
            error_msgs = [e.get("message", "Unknown error") for e in data_post["errors"]]
            raise Exception("GraphQL Error: " + ", ".join(error_msgs))

        post_result = data_post.get("data", {}).get("createPost", {})
        if post_result.get("__typename") != "PostActionSuccess":
            error_msg = post_result.get("message", "Unknown error creating post")
            raise Exception(f"Buffer API Error: {error_msg}")

        post_data = post_result.get("post", {})
        post_id = post_data.get("id")

        if not post_id:
            return post_data.get("externalLink") or "Post created (No ID)"

        # Time-Aware Polling (Optimized for Vercel Free: 10s budget)
        elapsed = time.time() - start_time
        if elapsed > 7:
            if _VERBOSE:
                print(f"[SKIP] Skipping polling for Instagram link (Elapsed {elapsed:.1f}s)")
            return post_data.get("externalLink") or "Success (Processing: Link will generate shortly)"

        max_attempts = 2
        delay = 1.0
        for attempt in range(max_attempts):
            elapsed = time.time() - start_time
            if elapsed > 9.0:
                break
                
            if _VERBOSE:
                print(f"[POLL] Instagram post {post_id} - Attempt {attempt+1}/{max_attempts} (Elapsed {elapsed:.1f}s)")
            
            try:
                status_query = """
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
                payload = {"query": status_query, "variables": {"id": post_id}}
                res = self._http.post(self.graphql_url, json=payload, timeout=2.0)
                if res.status_code == 200:
                    s_data = res.json()
                    post_info = s_data.get("data", {}).get("node", {})
                    status = post_info.get("status", "").lower()
                    link = post_info.get("externalLink")
                    
                    if status in ["sent", "delivered", "shared", "success"] and link:
                        if _VERBOSE:
                            print(f"[OK] Instagram post {post_id} is {status}. Link: {link}")
                        return link
                    elif status in ["failed", "error"]:
                        raise Exception(f"Instagram: Post status reported as '{status}'")
                
            except Exception as e:
                if _VERBOSE:
                    print(f"[WARN] Error during Instagram polling: {e}")
            
            time.sleep(delay)

        # Final attempt to get the link
        link = post_data.get("externalLink")
        if not link:
            return (
                "Created (Check Buffer app for mobile notification - "
                "direct posting may be restricted for this account type)"
            )
        return link

if __name__ == "__main__":
    post_content = f"Hello! This is a test post from my custom Buffer API script! Time: {datetime.datetime.now()}"
    insta_poster = InstagramPoster(post_content)
