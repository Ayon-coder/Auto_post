import requests
import json

ACCESS_TOKEN = "5twiGeBamtwRg_dHyA7izjogUAxi5Ykle4EupcXpoMV"
GRAPHQL_URL = "https://api.buffer.com/graphql"

headers = {
    "Authorization": f"Bearer {ACCESS_TOKEN}",
    "Content-Type": "application/json"
}


def graphql_query(query):
    """Run a GraphQL query and return (status_code, parsed_json)."""
    res = requests.post(GRAPHQL_URL, json={"query": query}, headers=headers)
    return res.status_code, res.json()


# Step 1: Get account info & organization IDs
print("=== Account Info ===")
status, data = graphql_query("""
    query {
        account {
            id
            email
            organizations {
                id
                name
            }
        }
    }
""")
print(f"Status: {status}")
print(json.dumps(data, indent=2))

# Step 2: Fetch channels (social profiles) for each organization
# Buffer's GraphQL uses "channels" instead of "profiles"
# The channels query takes an "input" argument of type ChannelsInput
if "data" in data and data["data"].get("account"):
    orgs = data["data"]["account"].get("organizations", [])
    for org in orgs:
        org_id = org["id"]
        org_name = org["name"]
        print(f"\n=== Channels for '{org_name}' ===")

        status2, channels_data = graphql_query(f"""
            query {{
                channels(input: {{ organizationId: "{org_id}" }}) {{
                    id
                    name
                    service
                }}
            }}
        """)
        print(f"Status: {status2}")
        print(json.dumps(channels_data, indent=2))