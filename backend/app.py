import io
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, Response, jsonify, request
from flask_cors import CORS
from werkzeug.middleware.proxy_fix import ProxyFix

from linkedin.create_post import LinkedIn
from linkedin.imgbb_client import upload_image_to_imgbb
from X.create_post import XPoster

# Load repo-root .env (works when cwd is backend/ or project root)
_REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_REPO_ROOT / ".env")

FRONTEND_DIR = _REPO_ROOT / "frontend"


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
    html = (FRONTEND_DIR / "index.html").read_text(encoding="utf-8")
    base = _backend_api_base_for_request()
    inject = f"<script>window.__BACKEND_API_BASE__ = {json.dumps(base)};</script>"
    html = html.replace("<!-- BACKEND_CONFIG_INJECT -->", inject)
    return Response(html, mimetype="text/html; charset=utf-8")


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
    
    # Handle retrieving platforms
    platforms_str = request.form.get('platforms', '')
    platforms = [p.strip() for p in platforms_str.split(',')] if platforms_str else []
    
    if not content:
        # Fallback to json (if no image is attached)
        if request.is_json:
            content = request.json.get('content', '').strip()
            platforms = request.json.get('platforms', [])
            
    if not content:
        return jsonify({"success": False, "message": "Post content is required."}), 400

    if not platforms:
        return jsonify({"success": False, "message": "At least one platform must be selected."}), 400

    tasks = []
    for image in images:
        if image and image.filename:
            tasks.append((image.filename, image.read()))

    image_urls = []
    if tasks:

        def _upload_one(idx_fn_blob):
            idx, filename, blob = idx_fn_blob
            ok, out = upload_image_to_imgbb(io.BytesIO(blob), filename)
            return idx, ok, out, filename

        max_workers = min(8, len(tasks))
        ordered = [None] * len(tasks)
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futs = [
                ex.submit(_upload_one, (i, fn, blob))
                for i, (fn, blob) in enumerate(tasks)
            ]
            for fut in as_completed(futs):
                idx, ok, out, filename = fut.result()
                if not ok:
                    return jsonify(
                        {
                            "success": False,
                            "message": f"Image upload failed for {filename}: {out}",
                        }
                    ), 500
                ordered[idx] = out
        image_urls = ordered

    img_urls_arg = image_urls if image_urls else None

    results = []
    success_count = 0

    def _linkedin_job():
        try:
            poster = LinkedIn(content, image_urls=img_urls_arg)
            if not poster.channel_id:
                return (0, "LinkedIn: Failed (No valid channel)", False)
            poster.create_post()
            return (0, f"LinkedIn: Success ({poster.channel_name})", True)
        except Exception as e:
            return (0, f"LinkedIn: Error ({str(e)})", False)

    def _x_job():
        try:
            poster = XPoster(content, image_urls=img_urls_arg)
            if not poster.channel_id:
                return (1, "X: Failed (No valid channel)", False)
            poster.create_post()
            return (1, f"X: Success ({poster.channel_name})", True)
        except Exception as e:
            return (1, f"X: Error ({str(e)})", False)

    try:
        jobs = []
        if "linkedin" in platforms:
            jobs.append(_linkedin_job)
        if "x" in platforms:
            jobs.append(_x_job)

        if len(jobs) == 1:
            prio, msg, ok = jobs[0]()
            results.append(msg)
            if ok:
                success_count += 1
        elif len(jobs) == 2:
            with ThreadPoolExecutor(max_workers=2) as ex:
                futs = [ex.submit(j) for j in jobs]
                batch = [f.result() for f in futs]
            batch.sort(key=lambda x: x[0])
            for _, msg, ok in batch:
                results.append(msg)
                if ok:
                    success_count += 1

        if success_count > 0:
            return jsonify({"success": True, "message": " | ".join(results)})
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
