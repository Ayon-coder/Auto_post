import os
import requests
import json
from dotenv import load_dotenv

load_dotenv()

_token = os.getenv("LINKEDIN_BUFFER_ACCESS_TOKEN", "")
headers = {
    "Authorization": f"Bearer {_token}",
    "Content-Type": "application/json",
}

query = """
{
  __type(name: "ImageAsset") {
    inputFields {
      name
      type {
        name
        kind
        ofType {
          name
        }
      }
    }
  }
}
"""

res = requests.post(
    os.getenv("GRAPHQL_URL", "https://api.buffer.com/graphql"),
    json={"query": query},
    headers=headers,
).json()
fields = res['data']['__type']['inputFields']
print("Fields on ImageAsset:")
for f in fields:
    print(f" - {f['name']}: {f['type']}")
