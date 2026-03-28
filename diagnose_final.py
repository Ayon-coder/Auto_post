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

query = """
{
  __type(name: "Post") {
    fields {
      name
    }
  }
}
"""

res = requests.post("https://api.buffer.com/graphql", json={"query": query}, headers=headers)
data = res.json()
fields = sorted([f['name'] for f in data['data']['__type']['fields']])
print("\n".join(fields))
