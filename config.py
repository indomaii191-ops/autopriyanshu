import os
from dotenv import load_dotenv

load_dotenv()

# ============================================================
#  PASTE YOUR REAL VALUES HERE
# ============================================================
TELEGRAM_BOT_TOKEN = "PASTE_YOUR_BOT_TOKEN_HERE"
ROOT_ADMIN_ID = 123456789
PURCHASE_USERNAME = "@nemogotmad"

VERCEL_API_TOKEN = "PASTE_YOUR_VERCEL_TOKEN_HERE"
VERCEL_TEAM_ID = ""

# On Railway: paste YOUR OWN Railway public URL here (no VPS!).
# Example: https://nemo-production.up.railway.app
# The download server runs in the same service and serves /dl/<page_name>.
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")

# Railway injects PORT automatically
DOWNLOAD_SERVER_PORT = int(os.getenv("PORT", "8080"))

# Persistent volume directory (Railway: mount at /data, set DATA_DIR=/data)
DATA_DIR = os.getenv("DATA_DIR", ".")
