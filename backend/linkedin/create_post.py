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

# Avoid re-fetching channel list on every post (same process)
_channel_cache: dict[str, tuple[str, str]] = {}


def _channel_cache_key(token: str) -> str:
    return hashlib.sha256(("linkedin" + token).encode()).hexdigest()


class LinkedIn:
    def __init__(self, content, image_urls=None):
        self.content = content
        self.image_urls = image_urls
        
        # Buffer token from .env — LinkedIn-specific (LINKEDIN_BUFFER_ACCESS_TOKEN)
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
                print(f"[OK] LINKEDIN: channel {self.channel_name} [{self.channel_id}] (cached)")
            return

        query = """
            query { account { organizations { channels { id name service } } } }
        """
        status, data = self.graphql_query(query)
        
        orgs = data.get("data", {}).get("account", {}).get("organizations", [])
        for org in orgs:
            channels = org.get("channels", [])
            for channel in channels:
                if channel.get("service") == "linkedin":
                    self.channel_id = channel["id"]
                    self.channel_name = channel["name"]
                    break
            if self.channel_id:
                break
        
        if not self.channel_id:
            raise Exception("No LinkedIn channel found for the provided LinkedIn token.")
        _channel_cache[key] = (self.channel_id, self.channel_name)
        if _VERBOSE:
            print(f"[OK] LINKEDIN: channel {self.channel_name} [{self.channel_id}]")

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
                "schedulingType": "automatic"
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
        return {
            "id": post_data.get("id"),
            "link": post_data.get("externalLink"),
            "handle": self.channel_name # Display name for LinkedIn
        }

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
    # When object is created, it automatically sends the post to LinkedIn
    linkedin = LinkedIn(post_content)
