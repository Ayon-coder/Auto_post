import io
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, Response, jsonify, request
from flask_cors import CORS
from werkzeug.middleware.proxy_fix import ProxyFix

try:
    from linkedin.create_post import LinkedIn
    from linkedin.imgbb_client import upload_image_to_imgbb
    from X.create_post import XPoster
    from instagram.create_post import InstagramPoster
    from facebook.create_post import FacebookPoster
except ImportError:
    # If run as a package (on Vercel)
    from .linkedin.create_post import LinkedIn
    from .linkedin.imgbb_client import upload_image_to_imgbb
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


@app.route('/api/post', methods=['POST'])
def create_post():
    content = request.form.get('content', '').strip()
    images = request.files.getlist('images')
    
    mode = request.form.get('mode', 'same')
    
    # Platform list
    platforms_str = request.form.get('platforms', '')
    platforms = [p.strip() for p in platforms_str.split(',')] if platforms_str else []
    
    if not platforms:
        return jsonify({"success": False, "message": "At least one platform must be selected."}), 400

    def _upload_all_images(image_files):
        """Helper to upload a list of file objects to ImgBB and return URLs."""
        urls = []
        if not image_files:
            return urls
            
        def _upload_one(idx_fn_blob):
            idx, filename, blob = idx_fn_blob
            ok, out = upload_image_to_imgbb(io.BytesIO(blob), filename)
            return idx, ok, out, filename

        tasks = []
        for img in image_files:
            if img and img.filename:
                tasks.append((img.filename, img.read()))
        
        if not tasks:
            return urls

        max_workers = min(8, len(tasks))
        ordered = [None] * len(tasks)
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futs = [ex.submit(_upload_one, (i, fn, blob)) for i, (fn, blob) in enumerate(tasks)]
            for fut in as_completed(futs):
                idx, ok, out, filename = fut.result()
                if not ok:
                    raise Exception(f"Image upload failed for {filename}: {out}")
                ordered[idx] = out
        return ordered

    # Prepare data for each platform
    platform_data = {}
    try:
        if mode == 'custom':
            for p in platforms:
                p_content = request.form.get(f'{p}_content', '').strip()
                p_images = request.files.getlist(f'{p}_images')
                if not p_content:
                    return jsonify({"success": False, "message": f"Content for {p} is required in custom mode."}), 400
                
                urls = _upload_all_images(p_images)
                platform_data[p] = {"content": p_content, "image_urls": urls if urls else None}
        else:
            # Same mode
            if not content:
                return jsonify({"success": False, "message": "Post content is required."}), 400
            
            urls = _upload_all_images(images)
            for p in platforms:
                platform_data[p] = {"content": content, "image_urls": urls if urls else None}
    except Exception as e:
        return jsonify({"success": False, "message": f"Upload error: {str(e)}"}), 500

    results = []
    success_count = 0

    def _linkedin_job():
        try:
            data = platform_data.get('linkedin')
            if not data: return (0, "LinkedIn: Skipped", False, None)
            poster = LinkedIn(data['content'], image_urls=data['image_urls'])
            if not poster.channel_id:
                return (0, "LinkedIn: Failed (No valid channel)", False, None)
            link = poster.create_post()
            return (0, f"LinkedIn: Success ({poster.channel_name})", True, link)
        except Exception as e:
            return (0, f"LinkedIn: Error ({str(e)})", False, None)

    def _x_job():
        try:
            data = platform_data.get('x')
            if not data: return (1, "X: Skipped", False, None)
            poster = XPoster(data['content'], image_urls=data['image_urls'])
            if not poster.channel_id:
                return (1, "X: Failed (No valid channel)", False, None)
            link = poster.create_post()
            return (1, f"X: Success ({poster.channel_name})", True, link)
        except Exception as e:
            return (1, f"X: Error ({str(e)})", False, None)

    def _instagram_job():
        try:
            data = platform_data.get('instagram')
            if not data: return (2, "Instagram: Skipped", False, None)
            poster = InstagramPoster(data['content'], image_urls=data['image_urls'])
            if not poster.channel_id:
                return (2, "Instagram: Failed (No valid channel)", False, None)
            link = poster.create_post()
            return (2, f"Instagram: Success ({poster.channel_name})", True, link)
        except Exception as e:
            return (2, f"Instagram: Error ({str(e)})", False, None)

    def _facebook_job():
        try:
            data = platform_data.get('facebook')
            if not data: return (3, "Facebook: Skipped", False, None)
            poster = FacebookPoster(data['content'], image_urls=data['image_urls'])
            if not poster.channel_id:
                return (3, "Facebook: Failed (No valid channel)", False, None)
            link = poster.create_post()
            return (3, f"Facebook: Success ({poster.channel_name})", True, link)
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

        links = {}
        if jobs:
            max_workers = min(len(jobs), 4)
            with ThreadPoolExecutor(max_workers=max_workers) as ex:
                futs = [ex.submit(j) for j in jobs]
                batch = [f.result() for f in futs]
            
            batch.sort(key=lambda x: x[0])
            for prio, msg, ok, link in batch:
                results.append(msg)
                if ok:
                    success_count += 1
                    if link:
                        platform_map = {0: "linkedin", 1: "x", 2: "instagram", 3: "facebook"}
                        platform_name = platform_map.get(prio, "unknown")
                        links[platform_name] = link

        if success_count > 0:
            return jsonify({"success": True, "message": " | ".join(results), "links": links})
        else:
            return jsonify({"success": False, "message": "Failed to post: " + " | ".join(results)}), 400

    except Exception as e:
        return jsonify({"success": False, "message": f"Critical error: {str(e)}"}), 500

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
