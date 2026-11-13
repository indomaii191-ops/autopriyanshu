import logging
import sqlite3
import requests
from flask import Flask, Response, abort

import config
import database

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_API = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}"


def get_page_by_name(page_name):
    conn = sqlite3.connect(database.DB_NAME)
    cur = conn.cursor()
    cur.execute(
        "SELECT page_name, apk_name, telegram_file_id, expires_at FROM pages WHERE page_name = ?",
        (page_name,)
    )
    row = cur.fetchone()
    conn.close()
    return row


def telegram_get_file_url(file_id):
    try:
        r = requests.get(f"{TELEGRAM_API}/getFile", params={"file_id": file_id}, timeout=15)
        if r.status_code != 200:
            return None
        data = r.json()
        if not data.get("ok"):
            return None
        return f"https://api.telegram.org/file/bot{config.TELEGRAM_BOT_TOKEN}/{data['result']['file_path']}"
    except Exception:
        return None


@app.route("/dl/<page_name>")
def download(page_name):
    page = get_page_by_name(page_name)
    if not page:
        abort(404, "Page not found")

    pn, apk_name, file_id, expires_at = page
    if not file_id:
        abort(404, "APK not available")

    tg_url = telegram_get_file_url(file_id)
    if not tg_url:
        abort(502, "Could not fetch from Telegram")

    r = requests.get(tg_url, stream=True, timeout=60)
    if r.status_code != 200:
        abort(502, "Telegram returned an error")

    total_size = int(r.headers.get("Content-Length", 0))
    filename = apk_name or "app.apk"

    def generate():
        for chunk in r.iter_content(chunk_size=64 * 1024):
            if chunk:
                yield chunk

    headers = {
        "Content-Type": "application/vnd.android.package-archive",
        "Content-Disposition": f'attachment; filename="{filename}"',
        "Content-Length": str(total_size),
        "Cache-Control": "public, max-age=3600",
    }
    return Response(generate(), headers=headers, status=200)


@app.route("/health")
def health():
    return "OK", 200


@app.route("/")
def index():
    return "Download server is running.", 200


if __name__ == "__main__":
    print(f"Download server starting on port {config.DOWNLOAD_SERVER_PORT}...")
    app.run(host="0.0.0.0", port=config.DOWNLOAD_SERVER_PORT, debug=False)
