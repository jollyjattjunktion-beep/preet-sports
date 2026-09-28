import os
import hashlib
import logging
from flask import Flask, request, jsonify, send_from_directory
from scraper import scrape_crex_match

app = Flask(__name__, static_folder=".")
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("cricket_server")

# ── Match Registry: maps short_id → crex_url ──────────────────────────────
# IDs are deterministic: first 8 hex chars of SHA256(url)
_match_registry = {}  # { match_id: crex_url }
_last_used_url = ""

def _url_to_id(url: str) -> str:
    """Generate an 8-char deterministic ID from a URL."""
    return hashlib.sha256(url.encode()).hexdigest()[:8]

# ──────────────────────────────────────────────────────────────────────────

@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return response

@app.route("/")
def index():
    return send_from_directory(".", "index.html")

# Serve the overlay page for a shared match link, e.g. /match/a1b2c3d4
@app.route("/match/<match_id>")
def match_page(match_id):
    return send_from_directory(".", "index.html")

@app.route("/<path:path>")
def static_proxy(path):
    if os.path.exists(path):
        return send_from_directory(".", path)
    return send_from_directory(".", "index.html")

@app.route("/api/default-url", methods=["GET", "POST"])
def api_default_url():
    global _last_used_url
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        new_url = data.get("url") or request.form.get("url") or request.args.get("url")
        if new_url and new_url.strip():
            _last_used_url = new_url.strip()
            return jsonify({"success": True, "url": _last_used_url})
        return jsonify({"success": False, "error": "No URL provided"}), 400
    return jsonify({"url": _last_used_url})

# ── Register a match URL → get shareable ID ──────────────────────────────
@app.route("/api/match", methods=["POST"])
def api_register_match():
    """Register a Crex match URL and return a unique shareable ID."""
    global _last_used_url
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or request.args.get("url") or "").strip()
    if not url:
        return jsonify({"success": False, "error": "No URL provided"}), 400
    match_id = _url_to_id(url)
    _match_registry[match_id] = url
    _last_used_url = url
    logger.info(f"Registered match {match_id} → {url}")
    return jsonify({"success": True, "match_id": match_id, "url": url})

# ── Resolve a match ID → URL ─────────────────────────────────────────────
@app.route("/api/match/<match_id>", methods=["GET"])
def api_get_match(match_id):
    """Resolve a match ID to its Crex URL."""
    url = _match_registry.get(match_id)
    if url:
        return jsonify({"success": True, "match_id": match_id, "url": url})
    return jsonify({"success": False, "error": "Match ID not found"}), 404

# ── Scrape by match ID ───────────────────────────────────────────────────
@app.route("/api/match/<match_id>/scrape", methods=["GET"])
def api_scrape_by_id(match_id):
    """Scrape live data for a match identified by its short ID."""
    url = _match_registry.get(match_id)
    if not url:
        return jsonify({"success": False, "error": "Match ID not found. Please provide a valid Crex URL."}), 404
    logger.info(f"Scraping match {match_id} → {url}")
    data = scrape_crex_match(url)
    status_code = 200 if data.get("success") else 400
    return jsonify(data), status_code

# ── Scrape by query URL ──────────────────────────────────────────────────
@app.route("/api/scrape", methods=["GET", "POST"])
def api_scrape():
    global _last_used_url
    url = request.args.get("url")
    if not url and request.is_json:
        data = request.get_json(silent=True) or {}
        url = data.get("url")
    if not url or not str(url).strip():
        return jsonify({
            "success": False,
            "error": "No Crex match URL provided. Please provide a Crex match link in the ?url= parameter."
        }), 400

    url = str(url).strip()
    _last_used_url = url
    logger.info(f"API Request to scrape real data from: {url}")
    data = scrape_crex_match(url)
    status_code = 200 if data.get("success") else 400
    return jsonify(data), status_code

@app.route("/api/health", methods=["GET"])
def api_health():
    return jsonify({"status": "ok", "service": "cricket-broadcast-scraper"})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5050))
    print(f"======================================================")
    print(f"🏏 Cricket Live Broadcast Server running on:")
    print(f"👉 http://localhost:{port}")
    print(f"Ready to scrape real live data when given a Crex link.")
    print(f"======================================================")
    app.run(host="0.0.0.0", port=port, debug=False)
