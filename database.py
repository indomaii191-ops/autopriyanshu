import sqlite3
import datetime
import random
import string
import os
import json

import config

DATA_DIR = getattr(config, "DATA_DIR", ".")
os.makedirs(DATA_DIR, exist_ok=True)

DB_NAME = os.path.join(DATA_DIR, "bot_database.db")
CONFIG_FILE = os.path.join(DATA_DIR, "bot_config.json")


def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            is_blocked INTEGER DEFAULT 0,
            is_root_admin INTEGER DEFAULT 0,
            active_key TEXT DEFAULT NULL
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS keys (
            key_code TEXT PRIMARY KEY,
            tier INTEGER,
            max_pages INTEGER,
            created_at TEXT,
            expires_at TEXT,
            is_active INTEGER DEFAULT 1,
            created_by INTEGER
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS pages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            key_code TEXT,
            page_name TEXT,
            theme TEXT,
            apk_name TEXT,
            telegram_file_id TEXT,
            vercel_url TEXT,
            created_at TEXT,
            expires_at TEXT
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS url_changes (
            user_id INTEGER,
            change_date TEXT,
            change_count INTEGER DEFAULT 0,
            PRIMARY KEY (user_id, change_date)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS trials (
            user_id INTEGER PRIMARY KEY,
            requested_at TEXT,
            status TEXT DEFAULT 'pending',
            key_granted TEXT DEFAULT NULL
        )
    ''')

    conn.commit()

    cursor.execute("PRAGMA table_info(users)")
    existing_columns = [row[1] for row in cursor.fetchall()]
    migrations_users = {
        "active_key": "ALTER TABLE users ADD COLUMN active_key TEXT DEFAULT NULL",
        "is_root_admin": "ALTER TABLE users ADD COLUMN is_root_admin INTEGER DEFAULT 0",
        "is_blocked": "ALTER TABLE users ADD COLUMN is_blocked INTEGER DEFAULT 0"
    }
    for col, sql in migrations_users.items():
        if col not in existing_columns:
            try:
                cursor.execute(sql)
            except Exception:
                pass

    cursor.execute("PRAGMA table_info(pages)")
    page_columns = [row[1] for row in cursor.fetchall()]
    if "telegram_file_id" not in page_columns:
        try:
            cursor.execute("ALTER TABLE pages ADD COLUMN telegram_file_id TEXT")
        except Exception:
            pass

    conn.commit()
    conn.close()


def add_user(user_id, username):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)", (user_id, username))
    cursor.execute("UPDATE users SET username = ? WHERE user_id = ?", (username, user_id))
    conn.commit()
    conn.close()


def get_user(user_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return row


def block_user(user_id, block_status):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET is_blocked = ? WHERE user_id = ?", (block_status, user_id))
    conn.commit()
    conn.close()


def set_user_key(user_id, key_code):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET active_key = ? WHERE user_id = ?", (key_code, user_id))
    conn.commit()
    conn.close()


def get_user_key(user_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT active_key FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else None


def generate_key(tier, days):
    key = ''.join(random.choices(string.ascii_uppercase + string.digits, k=16))
    key = f"NEMO-{key}"
    max_pages = {1: 1, 2: 3, 3: 99999}.get(tier, 1)
    created_at = datetime.datetime.now()
    expires_at = created_at + datetime.timedelta(days=days)

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO keys (key_code, tier, max_pages, created_at, expires_at, created_by)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (key, tier, max_pages, created_at.isoformat(), expires_at.isoformat(), 0))
    conn.commit()
    conn.close()
    return key


def validate_key(key_code):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM keys WHERE key_code = ? AND is_active = 1", (key_code,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None, "Invalid or canceled key."
    expires_at = datetime.datetime.fromisoformat(row[4])
    if datetime.datetime.now() > expires_at:
        return None, "Key expired."
    return row, None


def cancel_key(key_code):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("UPDATE keys SET is_active = 0 WHERE key_code = ?", (key_code,))
    conn.commit()
    conn.close()


def get_page_count(user_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM pages WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else 0


def get_user_pages(user_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, page_name, theme, vercel_url, created_at, expires_at FROM pages WHERE user_id = ? ORDER BY id DESC",
        (user_id,)
    )
    rows = cursor.fetchall()
    conn.close()
    return rows


def get_page_by_id(page_id, user_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, page_name, theme, vercel_url, apk_name, created_at, expires_at, telegram_file_id FROM pages WHERE id = ? AND user_id = ?",
        (page_id, user_id)
    )
    row = cursor.fetchone()
    conn.close()
    return row


def get_page_by_name(page_name):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT page_name, apk_name, telegram_file_id, expires_at FROM pages WHERE page_name = ?",
        (page_name,)
    )
    row = cursor.fetchone()
    conn.close()
    return row


def delete_page(page_id, user_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM pages WHERE id = ? AND user_id = ?", (page_id, user_id))
    conn.commit()
    affected = cursor.rowcount
    conn.close()
    return affected > 0


def update_page(page_id, user_id, new_page_name=None, new_theme=None, new_vercel_url=None):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    fields, values = [], []
    if new_page_name is not None:
        fields.append("page_name = ?")
        values.append(new_page_name)
    if new_theme is not None:
        fields.append("theme = ?")
        values.append(new_theme)
    if new_vercel_url is not None:
        fields.append("vercel_url = ?")
        values.append(new_vercel_url)
    if not fields:
        conn.close()
        return False
    values.extend([page_id, user_id])
    query = f"UPDATE pages SET {', '.join(fields)} WHERE id = ? AND user_id = ?"
    cursor.execute(query, values)
    conn.commit()
    affected = cursor.rowcount
    conn.close()
    return affected > 0


def update_page_apk(page_id, user_id, new_apk_name, new_vercel_url, new_file_id=None):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    if new_file_id is not None:
        cursor.execute(
            "UPDATE pages SET apk_name = ?, vercel_url = ?, telegram_file_id = ? WHERE id = ? AND user_id = ?",
            (new_apk_name, new_vercel_url, new_file_id, page_id, user_id)
        )
    else:
        cursor.execute(
            "UPDATE pages SET apk_name = ?, vercel_url = ? WHERE id = ? AND user_id = ?",
            (new_apk_name, new_vercel_url, page_id, user_id)
        )
    conn.commit()
    affected = cursor.rowcount
    conn.close()
    return affected > 0


def get_url_change_count_today(user_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    cursor.execute("SELECT change_count FROM url_changes WHERE user_id = ? AND change_date = ?", (user_id, today))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else 0


def can_change_url_today(user_id, tier):
    limits = {1: 1, 2: 3, 3: 999999}
    limit = limits.get(tier, 0)
    used = get_url_change_count_today(user_id)
    remaining = max(0, limit - used)
    return used < limit, remaining


def record_url_change(user_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    cursor.execute('''
        INSERT INTO url_changes (user_id, change_date, change_count)
        VALUES (?, ?, 1)
        ON CONFLICT(user_id, change_date) DO UPDATE SET change_count = change_count + 1
    ''', (user_id, today))
    conn.commit()
    conn.close()


def has_used_trial(user_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM trials WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return row is not None


def create_trial_request(user_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR IGNORE INTO trials (user_id, requested_at, status)
        VALUES (?, ?, 'pending')
    ''', (user_id, datetime.datetime.now().isoformat()))
    conn.commit()
    conn.close()


def get_trial(user_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM trials WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return row


def update_trial_status(user_id, status, key_granted=None):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("UPDATE trials SET status = ?, key_granted = ? WHERE user_id = ?", (status, key_granted, user_id))
    conn.commit()
    conn.close()


def load_bot_config():
    default = {"start_media": None, "start_text": None, "start1_media": None, "start1_text": None}
    if not os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'w') as f:
            json.dump(default, f, indent=2)
        return default
    try:
        with open(CONFIG_FILE, 'r') as f:
            data = json.load(f)
            for k, v in default.items():
                data.setdefault(k, v)
            return data
    except Exception:
        with open(CONFIG_FILE, 'w') as f:
            json.dump(default, f, indent=2)
        return default


def save_bot_config(cfg):
    with open(CONFIG_FILE, 'w') as f:
        json.dump(cfg, f, indent=2)
