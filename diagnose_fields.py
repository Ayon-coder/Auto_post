import os
import requests
import json
from dotenv import load_dotenv

# Load .env to get the token
load_dotenv()

token = os.getenv("LINKEDIN_BUFFER_ACCESS_TOKEN") or os.getenv("X_INSTA_BUFFER_ACCESS_TOKEN") or os.getenv("X_BUFFER_ACCESS_TOKEN")
if not token:
    print("Error: No access token found in .env (tried LINKEDIN_BUFFER_ACCESS_TOKEN and X_INSTA_BUFFER_ACCESS_TOKEN)")
    exit(1)

headers = {
    "Authorization": f"Bearer {token}",
    "Content-Type": "application/json",
}

# Introspection query to find all fields on the "Post" type
query = """
{
  __type(name: "Post") {
    fields {
      name
      type {
        name
        kind
        ofType {
          name
          kind
        }
      }
    }
  }
}
"""

url = "https://api.buffer.com/graphql"

try:
    print(f"Querying {url} with token: {token[:10]}...")
    res = requests.post(url, json={"query": query}, headers=headers)
    res.raise_for_status()
    data = res.json()
    
    fields = data.get("data", {}).get("__type", {}).get("fields", [])
    if not fields:
        print("No fields found for 'Post' type. Full response:")
        print(json.dumps(data, indent=2))
    else:
        print("Fields available on 'Post' type:")
        for f in sorted(fields, key=lambda x: x["name"]):
            field_name = f["name"]
            type_info = f["type"].get("name") or (f["type"].get("ofType", {}).get("name") if f["type"].get("ofType") else "Unknown")
            print(f" - {field_name}: {type_info}")

except Exception as e:
    print(f"Error: {e}")
