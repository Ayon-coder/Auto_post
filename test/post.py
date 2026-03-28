import os
import requests
from dotenv import load_dotenv

load_dotenv()

ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")
ORGANIZATION_ID = os.getenv("LINKEDIN_ORG_ID")  # Your company page ID

# -----------------------------------------------
# Step 1: Get your LinkedIn User ID (URN)
# -----------------------------------------------
def get_user_id():
    url = "https://api.linkedin.com/v2/userinfo"
    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
    }
    response = requests.get(url, headers=headers)
    data = response.json()

    if "sub" in data:
        user_id = data["sub"]
        print(f"✅ Your LinkedIn User ID: {user_id}")
        return user_id
    else:
        print(f"❌ Error fetching user ID: {data}")
        return None


# -----------------------------------------------
# Step 2: Post text content to LinkedIn
# -----------------------------------------------
def post_to_linkedin(text, as_company=False):
    """
    Post to LinkedIn.
    - as_company=False → posts to your personal profile
    - as_company=True  → posts to your company page (needs LINKEDIN_ORG_ID in .env)
    """
    if as_company:
        if not ORGANIZATION_ID:
            print("❌ LINKEDIN_ORG_ID not set in .env file.")
            print("💡 Find it from your company page URL: linkedin.com/company/<ID>/")
            return
        author = f"urn:li:organization:{ORGANIZATION_ID}"
        print(f"📢 Posting as company (Org ID: {ORGANIZATION_ID})...")
    else:
        user_id = get_user_id()
        if not user_id:
            print("❌ Cannot post without user ID.")
            return
        author = f"urn:li:person:{user_id}"
        print("👤 Posting as personal profile...")

    url = "https://api.linkedin.com/rest/posts"
    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type": "application/json",
        "LinkedIn-Version": "202601",
        "X-Restli-Protocol-Version": "2.0.0",
    }
    payload = {
        "author": author,
        "lifecycleState": "PUBLISHED",
        "visibility": "PUBLIC",
        "commentary": text,
        "distribution": {
            "feedDistribution": "MAIN_FEED",
            "targetEntities": [],
            "thirdPartyDistributionChannels": [],
        },
    }

    response = requests.post(url, headers=headers, json=payload)

    if response.status_code == 201:
        print("✅ Post published successfully on LinkedIn!")
        post_id = response.headers.get("x-restli-id", "N/A")
        print(f"📄 Post ID: {post_id}")
    else:
        print(f"❌ Failed to post. Status: {response.status_code}")
        print(f"Response: {response.json()}")


# -----------------------------------------------
# Run
# -----------------------------------------------
if __name__ == "__main__":
    print("📝 LinkedIn Post Publisher\n")

    # Edit your post content here
    post_text = """🚀 This is my first automated LinkedIn post using the LinkedIn API!

Built with Python + LinkedIn OAuth 2.0.

#Python #LinkedInAPI #Automation"""

    print(f"📤 Posting:\n{post_text}\n")

    # Set as_company=True to post as your company page
    post_to_linkedin(post_text, as_company=True)
