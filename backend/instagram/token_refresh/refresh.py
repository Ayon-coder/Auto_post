import hashlib
import os
import time

import requests
from dotenv import load_dotenv

load_dotenv()

GRAPHQL_URL = os.getenv("GRAPHQL_URL", "https://api.buffer.com/graphql")
_TOKEN_CACHE_TTL = float(os.getenv("BUFFER_TOKEN_CACHE_TTL", "600"))
_valid_until: dict[str, float] = {}


def _token_cache_key(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _insta_buffer_token():
    # Now shared with X, renamed to X_INSTA_BUFFER_ACCESS_TOKEN
    return (os.getenv("X_INSTA_BUFFER_ACCESS_TOKEN") or os.getenv("X_BUFFER_ACCESS_TOKEN") or "").strip()


class TokenManager:
    """Loads and validates the Instagram Buffer API token from environment only."""

    def __init__(self):
        self.access_token = None

    def get_valid_token(self):
        self.access_token = _insta_buffer_token()
        if not self.access_token:
            raise ValueError(
                "Instagram Buffer token missing. Set X_INSTA_BUFFER_ACCESS_TOKEN in your .env "
                "(shared with X - Buffer API key tied to your Instagram channel)."
            )

        key = _token_cache_key(self.access_token)
        now = time.monotonic()
        if key in _valid_until and now < _valid_until[key]:
            return self.access_token

        if self._is_token_valid():
            _valid_until[key] = now + _TOKEN_CACHE_TTL
            return self.access_token

        _valid_until.pop(key, None)
        raise ValueError(
            "Instagram Buffer token is invalid or expired. Regenerate it at "
            "https://buffer.com/developers/api and update X_INSTA_BUFFER_ACCESS_TOKEN in your .env."
        )

    def _is_token_valid(self):
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }
        query = '{"query": "{ account { id } }"}'

        try:
            response = requests.post(
                GRAPHQL_URL,
                data=query,
                headers=headers,
                timeout=10,
            )

            if response.status_code in (401, 403):
                return False

            data = response.json()
            if "errors" in data:
                for error in data["errors"]:
                    msg = error.get("message", "").lower()
                    if (
                        "unauthorized" in msg
                        or "authentication" in msg
                        or "token" in msg
                    ):
                        return False

            if data.get("data", {}).get("account", {}).get("id"):
                return True

            return False

        except requests.RequestException as e:
            print(f"[WARN] Could not validate token (network error): {e}")
            return True
