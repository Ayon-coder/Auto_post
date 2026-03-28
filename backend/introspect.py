import os
import requests
import json
from dotenv import load_dotenv

load_dotenv()

token = os.getenv("X_INSTA_BUFFER_ACCESS_TOKEN") or os.getenv("X_BUFFER_ACCESS_TOKEN")
url = os.getenv("GRAPHQL_URL", "https://api.buffer.com/graphql")

query = """
query {
  __type(name: "PostType") {
    enumValues {
      name
    }
  }
}
"""

headers = {
    "Authorization": f"Bearer {token}",
    "Content-Type": "application/json"
}

try:
    resp = requests.post(url, json={"query": query}, headers=headers, timeout=10)
    data = resp.json()
    values = [v["name"] for v in data["data"]["__type"]["enumValues"]]
    print("Enum values for PostType:")
    for v in values:
        print(f"- {v}")
except Exception as e:
    print(f"Error: {e}")
