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
    from .facebook.create_post import FacebookPoster
    from .cloudinary_client import upload_file_to_cloudinary
except ImportError:
    # If run as a package (on Vercel)
    from .linkedin.create_post import LinkedIn
    from .linkedin.imgbb_client import upload_image_to_imgbb
    from .X.create_post import XPoster
    from .instagram.create_post import InstagramPoster
    from .facebook.create_post import FacebookPoster
    from .cloudinary_client import upload_file_to_cloudinary

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

    def _upload_assets(files_list):
        """Helper to upload files to either ImgBB or Cloudinary based on type."""
        results = [] # List of {type: 'image'|'video'|'document', url: '...', thumbnail: '...'}
        if not files_list:
            return results
            
        def _upload_one(idx, filename, blob, mimetype):
            # Images -> ImgBB (legacy)
            if mimetype.startswith('image/'):
                ok, out = upload_image_to_imgbb(io.BytesIO(blob), filename)
                if not ok: raise Exception(f"ImgBB Failed: {out}")
                return idx, {"type": "image", "url": out, "thumbnail": out}
            
            # Videos/PDFs -> Cloudinary
            ok, url, res_type, thumb = upload_file_to_cloudinary(io.BytesIO(blob), filename)
            if not ok: raise Exception(f"Cloudinary Failed: {url}")
            
            # Map Cloudinary resource_type to Buffer types
            b_type = "video" if res_type == "video" else "document"
            return idx, {"type": b_type, "url": url, "thumbnail": thumb}

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
def check_link():
    import requests
    platform = request.args.get("platform")
    post_id = request.args.get("post_id")
    
    if not platform or not post_id:
        return jsonify({"success": False, "error": "Missing platform or post_id"}), 400
    
    # We use the LinkedIn token for checking any Buffer post status 
    # (assuming they share the same org/app permissions for status checks)
    token = os.getenv("LINKEDIN_FB_BUFFER_ACCESS_TOKEN")
    if platform in ["x", "instagram"]:
        token = os.getenv("X_INSTA_BUFFER_ACCESS_TOKEN")
        
    try:
        url = f"https://api.bufferapp.com/1/updates/{post_id}.json"
        res = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=5)
        data = res.json()
        
        service_link = data.get("service_link")
        update_id = data.get("service_update_id")
        
        # Proactive logic for LinkedIn if service_link is null but ID exists
        if not service_link and platform == "linkedin" and update_id and "urn:li:" in update_id:
            service_link = f"https://www.linkedin.com/feed/update/{update_id}"
            
        error_msg = None
        if data.get("status") in ["error", "failed"]:
            error_msg = data.get("client_error") or data.get("error_message") or data.get("error") or "Failed to publish post via Buffer."

        return jsonify({
            "success": True, 
            "link": service_link,
            "status": data.get("status"),
            "error_message": error_msg
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

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
