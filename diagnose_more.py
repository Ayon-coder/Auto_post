import os
import requests
import json
from dotenv import load_dotenv

load_dotenv()

token = os.getenv("LINKEDIN_BUFFER_ACCESS_TOKEN") or os.getenv("X_INSTA_BUFFER_ACCESS_TOKEN") or os.getenv("X_BUFFER_ACCESS_TOKEN")
headers = {
    "Authorization": f"Bearer {token}",
    "Content-Type": "application/json",
}

def introspect_type(type_name):
    query = f"""
    {{
      __type(name: "{type_name}") {{
        name
        fields {{
          name
          type {{
            name
            kind
          }}
        }}
      }}
    }}
    """
    res = requests.post("https://api.buffer.com/graphql", json={"query": query}, headers=headers)
    return res.json()

try:
    print("Introspecting 'Post' type...")
    post_info = introspect_type("Post")
    fields = post_info.get("data", {}).get("__type", {}).get("fields", [])
    print(f"Fields on 'Post': {[f['name'] for f in fields]}")

    print("\nIntrospecting 'PostActionSuccess' type...")
    success_info = introspect_type("PostActionSuccess")
    fields = success_info.get("data", {}).get("__type", {}).get("fields", [])
    print(f"Fields on 'PostActionSuccess': {[f['name'] for f in fields]}")

except Exception as e:
    print(f"Error: {e}")
