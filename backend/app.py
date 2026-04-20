import hashlib
import io
import json
import os
import secrets
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, Response, jsonify, request
from flask_cors import CORS
from werkzeug.middleware.proxy_fix import ProxyFix

try:
    # Package mode (Vercel)
    from .linkedin.create_post import LinkedIn
    from .cloudinary_client import upload_file_to_cloudinary
    from .X.create_post import XPoster
    from .instagram.create_post import InstagramPoster
    from .facebook.create_post import FacebookPoster
except (ImportError, ValueError):
    # Local script mode
    try:
        from linkedin.create_post import LinkedIn
        from cloudinary_client import upload_file_to_cloudinary
        from X.create_post import XPoster
        from instagram.create_post import InstagramPoster
        from facebook.create_post import FacebookPoster
    except ImportError:
        # Fallback for nested local run
        from .linkedin.create_post import LinkedIn
        from .cloudinary_client import upload_file_to_cloudinary
        from .X.create_post import XPoster
        from .instagram.create_post import InstagramPoster
        from .facebook.create_post import FacebookPoster

# Robust path detection for Vercel
_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent

def _find_frontend():
    possible_locations = [
        _REPO_ROOT / "frontend",
        Path.cwd() / "frontend",
        Path("/var/task") / "frontend",
        _HERE.parent.parent / "frontend"
    ]
    for loc in possible_locations:
        if loc.exists() and (loc / "index.html").exists():
            return loc
    return None

FRONTEND_DIR = _find_frontend()

print(f"[DEBUG] Started at: {Path(__file__).resolve()}")
print(f"[DEBUG] CWD: {Path.cwd()}")
print(f"[DEBUG] FRONTEND_DIR: {FRONTEND_DIR}")

# Load .env but don't crash if it's missing (Vercel uses system secrets)
env_path = _REPO_ROOT / ".env"
if env_path.exists():
    load_dotenv(env_path)
else:
    load_dotenv() # Fallback to standard search


def _backend_api_base_from_env() -> str:
    """BACKEND_API_BASE_URL (or legacy PUBLIC_API_BASE_URL): full API root including /api."""
    raw = (
        os.getenv("BACKEND_API_BASE_URL") or os.getenv("PUBLIC_API_BASE_URL") or ""
    ).strip().rstrip("/")
    if not raw:
        return ""
    if raw.endswith("/api"):
        return raw
    return f"{raw}/api"


def _backend_api_base_for_request() -> str:
    explicit = _backend_api_base_from_env()
    if explicit:
        return explicit
    if not request:
        return "/api"
    return request.host_url.rstrip("/") + "/api"


app = Flask(__name__)
# Respect X-Forwarded-* from Vercel / reverse proxies (correct URLs in /api/config)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
# Enable CORS so frontend (even if served separately) can make requests
CORS(app)

# Max upload size 10MB
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024

@app.route("/")
def serve_dashboard():
    """Inject BACKEND_API_BASE_URL into the page so the browser knows where to call the API."""
    if not FRONTEND_DIR or not (FRONTEND_DIR / "index.html").exists():
        # Prevent crash, return useful info instead
        return f"""
        <html><body>
            <h1>Frontend Not Found</h1>
            <p>CWD: {Path.cwd()}</p>
            <p>Repo Root: {_REPO_ROOT}</p>
            <p>Detected Frontend: {FRONTEND_DIR}</p>
            <p>Check "Logs" in Vercel Dashboard for details!</p>
        </body></html>
        """, 500

    try:
        html = (FRONTEND_DIR / "index.html").read_text(encoding="utf-8")
        base = _backend_api_base_for_request()
        inject = f"<script>window.__BACKEND_API_BASE__ = {json.dumps(base)};</script>"
        html = html.replace("<!-- BACKEND_CONFIG_INJECT -->", inject)
        return Response(html, mimetype="text/html; charset=utf-8")
    except Exception as e:
        return f"Error serving dashboard: {str(e)}", 500


@app.route('/api/status', methods=['GET'])
def get_status():
    return jsonify({"status": "Backend is running!"})


@app.route("/api/config", methods=["GET"])
def api_config():
    """Returns the same API base the dashboard uses (from BACKEND_API_BASE_URL or this request)."""
    return jsonify({"api_base_url": _backend_api_base_for_request()})


# ---------------------------------------------------------------------------
# Simple token-based authentication
# ---------------------------------------------------------------------------
_active_tokens = set()  # In-memory token store (resets on restart)


@app.route("/api/login", methods=["POST"])
def login():
    """Validate username/password against .env and return a session token."""
    data = request.get_json(silent=True) or {}
    username = data.get("username", "").strip()
    password = data.get("password", "").strip()

    expected_user = (os.getenv("AUTH_USERNAME") or "").strip()
    expected_pass = (os.getenv("AUTH_PASSWORD") or "").strip()

    if not expected_user or not expected_pass:
        return jsonify({"success": False, "message": "Auth not configured on server."}), 500

    if username == expected_user and password == expected_pass:
        token = secrets.token_hex(32)
        _active_tokens.add(token)
        return jsonify({"success": True, "token": token})

    return jsonify({"success": False, "message": "Invalid username or password."}), 401


@app.route("/api/logout", methods=["POST"])
def logout():
    """Invalidate the current session token."""
    auth = request.headers.get("Authorization", "")
    token = auth.replace("Bearer ", "").strip()
    _active_tokens.discard(token)
    return jsonify({"success": True})


def _require_auth():
    """Check for a valid Bearer token. Returns an error response or None."""
    auth = request.headers.get("Authorization", "")
    token = auth.replace("Bearer ", "").strip()
    if not token or token not in _active_tokens:
        return jsonify({"success": False, "message": "Unauthorized"}), 401
    return None


@app.route('/api/post', methods=['POST'])
def create_post():
    # Auth guard
    auth_err = _require_auth()
    if auth_err:
        return auth_err

    content = request.form.get('content', '').strip()
    images = request.files.getlist('images')
    
    mode = request.form.get('mode', 'same')
    
    # Platform list
    platforms_str = request.form.get('platforms', '')
    platforms = [p.strip() for p in platforms_str.split(',')] if platforms_str else []
    
    if not platforms:
        return jsonify({"success": False, "message": "At least one platform must be selected."}), 400

    def _upload_assets(files_list):
        """Upload files to Cloudinary. Returns a list of asset dicts."""
        results = []
        if not files_list:
            return results
            
        def _upload_one(idx, filename, blob, mimetype):
            ok, url, res_type, thumbnail_url = upload_file_to_cloudinary(io.BytesIO(blob), filename)
            if not ok: raise Exception(f"Cloudinary upload failed: {url}")
            asset_type = "video" if res_type == "video" else "image"
            return idx, {"type": asset_type, "url": url, "thumbnail": thumbnail_url or url}

        tasks = []
        for img in files_list:
            if img and img.filename:
                tasks.append((img.filename, img.read(), img.content_type))
        
        if not tasks:
            return results

        max_workers = min(8, len(tasks))
        ordered = [None] * len(tasks)
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futs = [ex.submit(_upload_one, i, fn, blob, mime) for i, (fn, blob, mime) in enumerate(tasks)]
            for fut in as_completed(futs):
                idx, out = fut.result()
                ordered[idx] = out
        return ordered

    # Prepare data for each platform
    platform_data = {}
    try:
        if mode == 'custom':
            for p in platforms:
                p_content = request.form.get(f'{p}_content', '').strip()
                p_files = request.files.getlist(f'{p}_images') # still named 'images' in form for now
                if not p_content:
                    return jsonify({"success": False, "message": f"Content for {p} is required in custom mode."}), 400
                
                assets = _upload_assets(p_files)
                platform_data[p] = {"content": p_content, "assets": assets}
        else:
            # Same mode
            if not content:
                return jsonify({"success": False, "message": "Post content is required."}), 400
            
            assets = _upload_assets(images)
            for p in platforms:
                platform_data[p] = {"content": content, "assets": assets}
    except Exception as e:
        return jsonify({"success": False, "message": f"Upload error: {str(e)}"}), 500

    results = []
    success_count = 0

    def _linkedin_job():
        try:
            data = platform_data.get('linkedin')
            if not data: return (0, "LinkedIn: Skipped", False, None)
            poster = LinkedIn(data['content'], assets=data['assets'])
            if not poster.channel_id:
                return (0, "LinkedIn: Failed (No valid channel)", False, None)
            res = poster.create_post()
            return (0, f"LinkedIn: Success ({poster.channel_name})", True, res)
        except Exception as e:
            return (0, f"LinkedIn: Error ({str(e)})", False, None)

    def _x_job():
        try:
            data = platform_data.get('x')
            if not data: return (1, "X: Skipped", False, None)
            poster = XPoster(data['content'], assets=data['assets'])
            if not poster.channel_id:
                return (1, "X: Failed (No valid channel)", False, None)
            res = poster.create_post()
            return (1, f"X: Success ({poster.channel_name})", True, res)
        except Exception as e:
            return (1, f"X: Error ({str(e)})", False, None)

    def _instagram_job():
        try:
            data = platform_data.get('instagram')
            if not data: return (2, "Instagram: Skipped", False, None)
            poster = InstagramPoster(data['content'], assets=data['assets'])
            if not poster.channel_id:
                return (2, "Instagram: Failed (No valid channel)", False, None)
            res = poster.create_post()
            return (2, f"Instagram: Success ({poster.channel_name})", True, res)
        except Exception as e:
            return (2, f"Instagram: Error ({str(e)})", False, None)

    def _facebook_job():
        try:
            data = platform_data.get('facebook')
            if not data: return (3, "Facebook: Skipped", False, None)
            poster = FacebookPoster(data['content'], assets=data['assets'])
            if not poster.channel_id:
                return (3, "Facebook: Failed (No valid channel)", False, None)
            res = poster.create_post()
            return (3, f"Facebook: Success ({poster.channel_name})", True, res)
        except Exception as e:
            return (3, f"Facebook: Error ({str(e)})", False, None)

    try:
        jobs = []
        if "linkedin" in platforms:
            jobs.append(_linkedin_job)
        if "x" in platforms:
            jobs.append(_x_job)
        if "instagram" in platforms:
            jobs.append(_instagram_job)
        if "facebook" in platforms:
            jobs.append(_facebook_job)

        results_dict = {}
        if jobs:
            max_workers = min(len(jobs), 4)
            with ThreadPoolExecutor(max_workers=max_workers) as ex:
                futs = [ex.submit(j) for j in jobs]
                batch = [f.result() for f in futs]
            
            batch.sort(key=lambda x: x[0])
            for prio, msg, ok, res in batch:
                results.append(msg)
                if ok:
                    success_count += 1
                    if res:
                        platform_map = {0: "linkedin", 1: "x", 2: "instagram", 3: "facebook"}
                        platform_name = platform_map.get(prio, "unknown")
                        results_dict[platform_name] = res

        if success_count > 0:
            return jsonify({"success": True, "message": " | ".join(results), "platforms": results_dict})
        else:
            return jsonify({"success": False, "message": "Failed to post: " + " | ".join(results)}), 400

    except Exception as e:
        return jsonify({"success": False, "message": f"Critical error: {str(e)}"}), 500

@app.route("/api/check-link", methods=["GET"])
@app.route("/check-link", methods=["GET"])  # Alias for simplicity
def check_link():
    import requests as req_lib
    platform = request.args.get("platform")
    post_id = request.args.get("post_id")
    
    if not post_id:
        return jsonify({"success": False, "error": "Missing post_id"}), 400

    # ── LinkedIn: use the GraphQL-based lookup (REST API can't resolve GraphQL IDs) ──
    if platform and platform.lower() == "linkedin":
        try:
            access_token = (
                os.getenv("LINKEDIN_FB_BUFFER_ACCESS_TOKEN")
                or os.getenv("LINKEDIN_BUFFER_ACCESS_TOKEN")
                or ""
            ).strip()
            if not access_token:
                return jsonify({"success": False, "error": "LinkedIn Buffer token not configured"}), 500

            graphql_url = os.getenv("GRAPHQL_URL", "https://api.buffer.com/graphql")
            http_session = req_lib.Session()
            http_session.headers.update({
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            })

            # Query the post via GraphQL
            gql_query = """
                query GetPost($input: PostInput!) {
                    post(input: $input) {
                        id
                        externalLink
                        status
                    }
                }
            """
            gql_res = http_session.post(
                graphql_url,
                json={"query": gql_query, "variables": {"input": {"id": post_id}}},
                timeout=10,
            )
            gql_data = gql_res.json()

            if "errors" in gql_data:
                error_msgs = [e.get("message", "Unknown") for e in gql_data["errors"]]
                return jsonify({"success": False, "error": "GraphQL: " + ", ".join(error_msgs)}), 500

            post_obj    = gql_data.get("data", {}).get("post") or {}
            link        = post_obj.get("externalLink")
            post_status = post_obj.get("status", "unknown")

            error_msg = None
            if post_status in ("failed", "error"):
                error_msg = f"Buffer post failed with status: {post_status}"

            return jsonify({
                "success": True,
                "ready": bool(link),
                "link": link,
                "status": post_status,
                "error_message": error_msg,
            })

        except Exception as e:
            return jsonify({"success": False, "error": f"LinkedIn check error: {str(e)}"}), 500

    # ── Instagram: use GraphQL (same as LinkedIn — REST v1 doesn't recognise GraphQL IDs) ──
    if platform and platform.lower() == "instagram":
        try:
            access_token = (os.getenv("X_INSTA_BUFFER_ACCESS_TOKEN") or "").strip()
            if not access_token:
                return jsonify({"success": False, "error": "Instagram Buffer token not configured"}), 500

            graphql_url = os.getenv("GRAPHQL_URL", "https://api.buffer.com/graphql")
            http_session = req_lib.Session()
            http_session.headers.update({
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            })

            gql_query = """
                query GetPost($input: PostInput!) {
                    post(input: $input) {
                        id
                        externalLink
                        status
                    }
                }
            """
            gql_res = http_session.post(
                graphql_url,
                json={"query": gql_query, "variables": {"input": {"id": post_id}}},
                timeout=10,
            )
            gql_data = gql_res.json()

            if "errors" in gql_data:
                error_msgs = [e.get("message", "Unknown") for e in gql_data["errors"]]
                return jsonify({"success": False, "error": "GraphQL: " + ", ".join(error_msgs)}), 500

            post_obj    = gql_data.get("data", {}).get("post") or {}
            link        = post_obj.get("externalLink")
            post_status = post_obj.get("status", "unknown")

            error_msg = None
            if post_status in ("failed", "error"):
                error_msg = f"Buffer post failed with status: {post_status}"

            return jsonify({
                "success": True,
                "ready": bool(link),
                "link": link,
                "status": post_status,
                "error_message": error_msg,
            })

        except Exception as e:
            return jsonify({"success": False, "error": f"Instagram check error: {str(e)}"}), 500

    # ── Facebook: use GraphQL (same pattern — Buffer GraphQL IDs) ──
    if platform and platform.lower() == "facebook":
        try:
            access_token = (
                os.getenv("LINKEDIN_FB_BUFFER_ACCESS_TOKEN")
                or os.getenv("LINKEDIN_BUFFER_ACCESS_TOKEN")
                or ""
            ).strip()
            if not access_token:
                return jsonify({"success": False, "error": "Facebook Buffer token not configured"}), 500

            graphql_url = os.getenv("GRAPHQL_URL", "https://api.buffer.com/graphql")
            http_session = req_lib.Session()
            http_session.headers.update({
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            })

            gql_query = """
                query GetPost($input: PostInput!) {
                    post(input: $input) {
                        id
                        externalLink
                        status
                    }
                }
            """
            gql_res = http_session.post(
                graphql_url,
                json={"query": gql_query, "variables": {"input": {"id": post_id}}},
                timeout=10,
            )
            gql_data = gql_res.json()

            if "errors" in gql_data:
                error_msgs = [e.get("message", "Unknown") for e in gql_data["errors"]]
                return jsonify({"success": False, "error": "GraphQL: " + ", ".join(error_msgs)}), 500

            post_obj    = gql_data.get("data", {}).get("post") or {}
            link        = post_obj.get("externalLink")
            post_status = post_obj.get("status", "unknown")

            error_msg = None
            if post_status in ("failed", "error"):
                error_msg = f"Buffer post failed with status: {post_status}"

            return jsonify({
                "success": True,
                "ready": bool(link),
                "link": link,
                "status": post_status,
                "error_message": error_msg,
            })

        except Exception as e:
            return jsonify({"success": False, "error": f"Facebook check error: {str(e)}"}), 500

    # ── X / Twitter: use Buffer REST API v1 (REST IDs work for X) ──
    if platform and platform.lower() in ["x", "twitter"]:
        try:
            access_token = (os.getenv("X_INSTA_BUFFER_ACCESS_TOKEN") or "").strip()
            if not access_token:
                return jsonify({"success": False, "error": "X Buffer token not configured"}), 500

            graphql_url = os.getenv("GRAPHQL_URL", "https://api.buffer.com/graphql")
            http_session = req_lib.Session()
            http_session.headers.update({
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            })

            gql_query = """
                query GetPost($input: PostInput!) {
                    post(input: $input) {
                        id
                        externalLink
                        status
                    }
                }
            """
            gql_res = http_session.post(
                graphql_url,
                json={"query": gql_query, "variables": {"input": {"id": post_id}}},
                timeout=10,
            )
            gql_data = gql_res.json()

            if "errors" in gql_data:
                error_msgs = [e.get("message", "Unknown") for e in gql_data["errors"]]
                return jsonify({"success": False, "error": "GraphQL: " + ", ".join(error_msgs)}), 500

            post_obj    = gql_data.get("data", {}).get("post") or {}
            link        = post_obj.get("externalLink")
            post_status = post_obj.get("status", "unknown")

            error_msg = None
            if post_status in ("failed", "error"):
                error_msg = f"Buffer post failed with status: {post_status}"

            return jsonify({
                "success": True,
                "ready": bool(link),
                "link": link,
                "status": post_status,
                "error_message": error_msg,
            })

        except Exception as e:
            return jsonify({"success": False, "error": f"X check error: {str(e)}"}), 500

    # ── Unknown platform fallback ──
    return jsonify({"success": False, "error": f"Unknown platform: {platform}"}), 400

# This is required for Vercel Serverless Functions
app_callable = app

if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    host = os.getenv("FLASK_RUN_HOST", "127.0.0.1")
    print(f"Dashboard + API (local): PORT={port} HOST={host}")
    _debug = os.getenv("FLASK_DEBUG", "1").lower() in ("1", "true", "yes")
    app.run(
        host=host,
        port=port,
        debug=_debug,
        use_reloader=os.getenv("FLASK_USE_RELOADER", "0").lower() in ("1", "true", "yes"),
        threaded=True,
    )
