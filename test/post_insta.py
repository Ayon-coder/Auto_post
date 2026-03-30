import os
import requests
import json
from dotenv import load_dotenv
from pathlib import Path

# Load environment variables from root .env
load_dotenv(Path(__file__).parent.parent / ".env")

IMGBB_API_KEY = os.getenv("IMGBB_API_KEY")
INSTA_TOKEN = os.getenv("X_INSTA_BUFFER_ACCESS_TOKEN")
GRAPHQL_URL = os.getenv("GRAPHQL_URL", "https://api.buffer.com/graphql")

# Image is in the same folder as this script
IMAGE_PATH = Path(__file__).parent / "test.jpeg"

def upload_to_imgbb(path):
    print(f"[*] Uploading {path.name} to ImgBB...")
    url = "https://api.imgbb.com/1/upload"
    with open(path, "rb") as f:
        res = requests.post(url, data={"key": IMGBB_API_KEY}, files={"image": f})
    data = res.json()
    if res.status_code == 200 and data.get("success"):
        return data["data"]["url"]
    else:
        raise Exception(f"ImgBB Upload Failed: {data.get('error', {}).get('message', 'Unknown error')}")

def get_insta_channel(token):
    print("[*] Fetching Instagram channel ID from Buffer...")
    query = """
    query { account { organizations { channels { id name service } } } }
    """
    res = requests.post(GRAPHQL_URL, json={"query": query}, headers={"Authorization": f"Bearer {token}"})
    data = res.json()
    if "errors" in data:
        print(f"[!] GraphQL Errors: {data['errors']}")
        return None, None
        
    orgs = data.get("data", {}).get("account", {}).get("organizations", [])
    for org in orgs:
        for channel in org.get("channels", []):
            if channel.get("service") == "instagram":
                return channel["id"], channel["name"]
    return None, None

def post_to_insta(token, channel_id, image_url):
    print(f"[*] Creating Instagram post (Channel: {channel_id})...")
    mutation = """
    mutation CreatePost($input: CreatePostInput!) {
        createPost(input: $input) {
            __typename
            ... on PostActionSuccess {
                post { id externalLink }
            }
            ... on UnexpectedError { message }
            ... on InvalidInputError { message }
        }
    }
    """
    variables = {
        "input": {
            "channelId": channel_id,
            "text": "Automated Instagram Test Post",
            "mode": "shareNow",
            "schedulingType": "automatic",
            "assets": {
                "images": [{"url": image_url}]
            },
            "metadata": {
                "instagram": {
                    "type": "post",
                    "shouldShareToFeed": True
                }
            }
        }
    }
    res = requests.post(GRAPHQL_URL, json={"query": mutation, "variables": variables}, headers={"Authorization": f"Bearer {token}"})
    return res.status_code, res.json()

if __name__ == "__main__":
    print("--- Instagram Post Diagnostic ---")
    if not IMAGE_PATH.exists():
        print(f"[!] Error: {IMAGE_PATH} not found in the test folder.")
        exit(1)
    
    if not IMGBB_API_KEY or not INSTA_TOKEN:
        print("[!] Error: Keys missing in .env.")
        exit(1)

    try:
        # 1. Upload to ImgBB
        img_url = upload_to_imgbb(IMAGE_PATH)
        print(f"[+] Direct Image URL: {img_url}")

        # 2. Get Instagram Channel
        cid, cname = get_insta_channel(INSTA_TOKEN)
        if not cid:
            print("[!] Could not find an Instagram channel in your Buffer account.")
            exit(1)
        print(f"[+] Targeting Channel: {cname} ({cid})")

        # 3. Create the Post
        status, result = post_to_insta(INSTA_TOKEN, cid, img_url)
        print(f"\n[HTTP {status}] Response Body:")
        print(json.dumps(result, indent=2))

        post_res = result.get("data", {}).get("createPost", {})
        if post_res.get("__typename") == "PostActionSuccess":
            print("\n✅ SUCCESS: Post created successfully!")
            print(f"🔗 Instagram Link: {post_res.get('post', {}).get('externalLink')}")
        else:
            print("\n❌ FAILURE: Buffer/Instagram rejected the post.")
            print(f"⚠️ Error Message: {post_res.get('message', 'No specific message returned')}")

    except Exception as e:
        print(f"\n[!] ERROR: {e}")
