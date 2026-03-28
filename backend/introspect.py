import os
import requests
import json
from dotenv import load_dotenv

load_dotenv()

token = os.getenv("X_INSTA_BUFFER_ACCESS_TOKEN") or os.getenv("X_BUFFER_ACCESS_TOKEN")
url = os.getenv("GRAPHQL_URL", "https://api.buffer.com/graphql")

query = """
query {
  __type(name: "InstagramPostMetadataInput") {
    inputFields {
      name
      type {
        kind
        name
        ofType {
          kind
          name
        }
      }
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
    fields = data["data"]["__type"]["inputFields"]
    with open("insta_fields.txt", "w") as f:
        f.write("ALL fields for InstagramPostMetadataInput:\n")
        for field in fields:
            is_required = field["type"]["kind"] == "NON_NULL"
            type_info = field["type"]["name"] if field["type"]["name"] else field["type"]["ofType"]["name"]
            f.write(f" - {field['name']}: {type_info} ({'MANDATORY' if is_required else 'OPTIONAL'})\n")
    print("Results saved to insta_fields.txt")
except Exception as e:
    print(f"Error: {e}")
