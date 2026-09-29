# Preet Sports

Files:
- server.py = Flask API
- scraper.py = scraper
- index.html = live HTML scoreboard
- requirements.txt = Python dependencies
- render.yaml = Render configuration

API:
GET /api/health
GET /api/scrape?url=CREX_MATCH_URL

The HTML scoreboard polls /api/scrape every 10 seconds.
