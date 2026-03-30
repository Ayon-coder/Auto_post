import os
import requests
from dotenv import load_dotenv

load_dotenv()

class LinkedIn:
    def __init__(self, content, image_urls=None):
        self.content = content
        self.image_urls = image_urls or []
        
        # We use the native LinkedIn token instead of Buffer
        self.access_token = os.getenv("ACCESS_TOKEN")
        self.organization_id = os.getenv("LINKEDIN_ORG_ID")  # Optional
        
        self.channel_id = "native-api"
        # We try to figure out the author
        self.author_urn = None
        self.channel_name = "LinkedIn (Native)"

    def get_user_id(self):
        url = "https://api.linkedin.com/v2/userinfo"
        headers = {
            "Authorization": f"Bearer {self.access_token}",
        }
        response = requests.get(url, headers=headers)
        data = response.json()
        if "sub" in data:
            return data["sub"]
        return None

    def create_post(self):
        if not self.access_token:
            raise Exception("No ACCESS_TOKEN found in environment. Please add it to your .env file or Vercel dashboard.")

        if self.organization_id:
            self.author_urn = f"urn:li:organization:{self.organization_id}"
            self.channel_name = f"Company ({self.organization_id})"
        else:
            user_id = self.get_user_id()
            if not user_id:
                raise Exception("Failed to fetch LinkedIn User ID with the provided ACCESS_TOKEN.")
            self.author_urn = f"urn:li:person:{user_id}"
            self.channel_name = "Personal Profile"

        url = "https://api.linkedin.com/rest/posts"
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
            "LinkedIn-Version": "202601",
            "X-Restli-Protocol-Version": "2.0.0",
        }

        # If there are images, we can append their URLs to the post so they form a link preview,
        # since native API requires special flow for direct image uploads that isn't fully implemented here.
        final_text = self.content
        if self.image_urls:
            final_text += "\n\n" + "\n".join(self.image_urls)

        payload = {
            "author": self.author_urn,
            "lifecycleState": "PUBLISHED",
            "visibility": "PUBLIC",
            "commentary": final_text,
            "distribution": {
                "feedDistribution": "MAIN_FEED",
                "targetEntities": [],
                "thirdPartyDistributionChannels": [],
            },
        }

        response = requests.post(url, headers=headers, json=payload)

        if response.status_code == 201:
            post_id = response.headers.get("x-restli-id", "N/A")
            if post_id != "N/A":
                # Return the actual link!
                return f"https://www.linkedin.com/feed/update/{post_id}"
            else:
                return "Posted (No ID returned)"
        else:
            try:
                error_data = response.json()
            except:
                error_data = response.text
            raise Exception(f"LinkedIn API Error ({response.status_code}): {error_data}")

if __name__ == "__main__":
    poster = LinkedIn("Hello from AutoPost Native Integration!")
    try:
        print("Link:", poster.create_post())
    except Exception as e:
        print("Error:", e)
