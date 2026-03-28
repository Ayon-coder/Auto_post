import os
import webbrowser
import requests
from flask import Flask, request
from dotenv import load_dotenv, set_key

load_dotenv()

app = Flask(__name__)

CLIENT_ID = os.getenv("LINKEDIN_CLIENT_ID")
CLIENT_SECRET = os.getenv("LINKEDIN_CLIENT_SECRET")
REDIRECT_URI = os.getenv("LINKEDIN_REDIRECT_URI")
SCOPE = "openid profile email w_member_social w_organization_social"
# SCOPE = "r_liteprofile r_emailaddress w_member_social"

# Step 1: Generate the LinkedIn OAuth URL
def get_auth_url():
    auth_url = (
        f"https://www.linkedin.com/oauth/v2/authorization"
        f"?response_type=code"
        f"&client_id={CLIENT_ID}"
        f"&redirect_uri={REDIRECT_URI}"
        f"&scope={SCOPE.replace(' ', '%20')}"
    )
    return auth_url

# Step 2: Exchange auth code for access token
def exchange_code_for_token(code):
    token_url = "https://www.linkedin.com/oauth/v2/accessToken"
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
    }
    response = requests.post(token_url, data=data)
    token_data = response.json()

    if "access_token" in token_data:
        access_token = token_data["access_token"]
        expires_in = token_data.get("expires_in", "unknown")

        # Save token to .env file
        set_key(".env", "ACCESS_TOKEN", access_token)
        print(f"\n✅ Access Token saved to .env file!")
        print(f"⏳ Expires in: {expires_in} seconds (~60 days)")
        print(f"\n🔑 Your Access Token:\n{access_token}\n")
        return access_token
    else:
        print(f"\n❌ Error getting token: {token_data}")
        return None

# Step 3: Flask callback route to capture the auth code
@app.route("/callback")
def callback():
    code = request.args.get("code")
    error = request.args.get("error")

    if error:
        return f"<h2>❌ Error: {error}</h2>"

    if code:
        token = exchange_code_for_token(code)
        if token:
            return """
                <h2>✅ Authorization Successful!</h2>
                <p>Your access token has been saved to the <strong>.env</strong> file.</p>
                <p>You can close this tab and go back to your terminal.</p>
            """
    return "<h2>❌ No code received from LinkedIn.</h2>"


if __name__ == "__main__":
    print("🚀 Starting LinkedIn OAuth Flow...")
    print("📌 Opening LinkedIn login in your browser...\n")

    auth_url = get_auth_url()
    webbrowser.open(auth_url)

    print("⏳ Waiting for LinkedIn to redirect back...")
    print("👉 If browser didn't open, go to this URL manually:")
    print(f"\n{auth_url}\n")

    # Run Flask on port 3000 to capture the callback
    app.run(port=3000, debug=False)
