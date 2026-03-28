import os
import requests
import json
import datetime
from dotenv import load_dotenv

load_dotenv()

_VERBOSE = os.getenv("BUFFER_VERBOSE", "False").lower() == "true"

class FacebookPoster:
    def __init__(self, content, image_urls=None):
        self.content = content
        self.image_urls = image_urls
        self.token = os.getenv("LINKEDIN_FB_BUFFER_ACCESS_TOKEN") or os.getenv("LINKEDIN_BUFFER_ACCESS_TOKEN")
        self.graphql_url = os.getenv("GRAPHQL_URL", "https://api.buffer.com/graphql")
        
        if not self.token:
            raise Exception("Facebook/LinkedIn Buffer Access Token not found in environment variables.")
        
        self.channel_id = None
        self.channel_name = None
        self._get_facebook_channel()

    def graphql_query(self, query, variables=None):
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }
        payload = {"query": query}
        if variables:
            payload["variables"] = variables
            
        response = requests.post(self.graphql_url, json=payload, headers=headers)
        return response.status_code, response.json()

    def _get_facebook_channel(self):
        query = """
        query {
            user {
                channels {
                    id
                    name
                    service
                }
            }
        }
        """
        status, data = self.graphql_query(query)
        if status != 200:
            raise Exception(f"Failed to fetch Buffer channels: {status}")
            
        channels = data.get("data", {}).get("user", {}).get("channels", [])
        # Find Facebook channel (could be 'facebook' service)
        fb_channel = next((c for c in channels if c["service"] == "facebook"), None)
        
        if not fb_channel:
            # Fallback check for 'facebook_page' or similar if necessary
            fb_channel = next((c for c in channels if "facebook" in c["service"].lower()), None)

        if fb_channel:
            self.channel_id = fb_channel["id"]
            self.channel_name = fb_channel["name"]
            if _VERBOSE:
                print(f"[OK] Facebook: channel {self.channel_name} [{self.channel_id}]")

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
        
        if self.image_urls:
            variables["input"]["assets"] = {
                "images": [
                    {"url": url} for url in self.image_urls
                ]
            }

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
        return post_data.get("externalLink")

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
