"""
WebhookHub - Self-hosted webhook receiver + dashboard with Pushover, Discord, and SMTP notifications.
"""

import os
import json
import asyncio
import smtplib
import httpx
import sqlite3
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Request, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

# ── Config ────────────────────────────────────────────────────────────────────

DATABASE_PATH      = os.getenv("WEBHOOKHUB_DB", "/data/webhookhub.db")
PUSHOVER_USER_KEY  = os.getenv("PUSHOVER_USER_KEY", "")
PUSHOVER_API_TOKEN = os.getenv("PUSHOVER_API_TOKEN", "")
API_KEY            = os.getenv("WEBHOOKHUB_API_KEY", "")
PUSHOVER_API_URL   = "https://api.pushover.net/1/messages.json"

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")

SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASS", "")
SMTP_FROM = os.getenv("SMTP_FROM", "")
SMTP_TO   = os.getenv("SMTP_TO", "")  # comma-separated recipients

# ── Database ──────────────────────────────────────────────────────────────────

def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


DEFAULT_SETTINGS = {
    "site_title":           "WebhookHub",
    "header_text":          "WebhookHub",
    "favicon_url":          "/static/favicon.svg",
    "font_family":          "DM Sans",
    "font_size":            "14",
    "color_accent":         "#3b82f6",
    "color_text_primary":   "#e2e8f0",
    "color_text_secondary": "#8896b0",
}


def init_db():
    os.makedirs(os.path.dirname(DATABASE_PATH), exist_ok=True)
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS channels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            slug TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            icon TEXT DEFAULT '📡',
            color TEXT DEFAULT '#6366f1',
            pushover_enabled INTEGER DEFAULT 1,
            pushover_priority INTEGER DEFAULT 0,
            pushover_sound TEXT DEFAULT 'pushover',
            created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
            updated_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
        );

        CREATE TABLE IF NOT EXISTS webhooks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_slug TEXT NOT NULL,
            title TEXT DEFAULT '',
            message TEXT DEFAULT '',
            priority TEXT DEFAULT 'normal',
            source_ip TEXT DEFAULT '',
            raw_headers TEXT DEFAULT '{}',
            raw_body TEXT DEFAULT '',
            parsed_data TEXT DEFAULT '{}',
            received_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
            pushover_sent INTEGER DEFAULT 0,
            FOREIGN KEY (channel_slug) REFERENCES channels(slug)
        );

        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL DEFAULT ''
        );

        CREATE INDEX IF NOT EXISTS idx_webhooks_channel  ON webhooks(channel_slug);
        CREATE INDEX IF NOT EXISTS idx_webhooks_received ON webhooks(received_at DESC);
        CREATE INDEX IF NOT EXISTS idx_webhooks_priority ON webhooks(priority);
    """)

    # Seed default channels
    defaults = [
        ("tautulli", "Tautulli", "Plex media server notifications", "🎬", "#e67e22"),
        ("security",  "Security",  "Host login and security alerts",   "🔒", "#e74c3c"),
        ("updates",   "Updates",   "Package and system update notifications", "📦", "#2ecc71"),
        ("general",   "General",   "General purpose notifications",    "📡", "#6366f1"),
    ]
    for slug, name, desc, icon, color in defaults:
        conn.execute(
            "INSERT OR IGNORE INTO channels (slug, name, description, icon, color) VALUES (?, ?, ?, ?, ?)",
            (slug, name, desc, icon, color),
        )

    # Seed default settings
    for key, value in DEFAULT_SETTINGS.items():
        conn.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, value)
        )

    conn.commit()
    conn.close()


# ── Pushover ──────────────────────────────────────────────────────────────────

async def send_pushover(title: str, message: str, priority: int = 0, sound: str = "pushover", url: str = ""):
    if not PUSHOVER_USER_KEY or not PUSHOVER_API_TOKEN:
        return False

    payload = {
        "token":    PUSHOVER_API_TOKEN,
        "user":     PUSHOVER_USER_KEY,
        "title":    title[:250],
        "message":  message[:1024],
        "priority": priority,
        "sound":    sound,
    }
    if url:
        payload["url"] = url
    if priority == 2:
        payload["retry"]  = 60
        payload["expire"] = 3600

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(PUSHOVER_API_URL, data=payload, timeout=10)
            return resp.status_code == 200
        except Exception as e:
            print(f"[pushover] Error: {e}")
            return False


# ── Discord ───────────────────────────────────────────────────────────────────

_DISCORD_COLORS = {
    "low":      0x556480,
    "normal":   0x3b82f6,
    "high":     0xf59e0b,
    "critical": 0xef4444,
}


async def send_discord(title: str, message: str, priority: str = "normal", channel_name: str = ""):
    if not DISCORD_WEBHOOK_URL:
        return False

    embed_title = f"[{channel_name}] {title}" if channel_name else title
    payload = {
        "embeds": [{
            "title":       embed_title[:256],
            "description": message[:2048],
            "color":       _DISCORD_COLORS.get(priority, 0x3b82f6),
        }]
    }

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
            return resp.status_code in (200, 204)
        except Exception as e:
            print(f"[discord] Error: {e}")
            return False


# ── SMTP ──────────────────────────────────────────────────────────────────────

def _smtp_send_sync(subject: str, body: str):
    """Blocking SMTP send — run via asyncio.to_thread()."""
    msg = MIMEMultipart()
    msg["Subject"] = subject
    msg["From"]    = SMTP_FROM
    msg["To"]      = SMTP_TO
    msg.attach(MIMEText(body, "plain"))
    recipients = [r.strip() for r in SMTP_TO.split(",")]

    if SMTP_PORT == 465:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=15) as server:
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(SMTP_FROM, recipients, msg.as_string())
    else:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
            server.ehlo()
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(SMTP_FROM, recipients, msg.as_string())


async def send_smtp(title: str, message: str, channel_name: str = ""):
    if not all([SMTP_HOST, SMTP_USER, SMTP_PASS, SMTP_FROM, SMTP_TO]):
        return False

    subject = f"[WebhookHub][{channel_name}] {title}" if channel_name else f"[WebhookHub] {title}"
    try:
        await asyncio.to_thread(_smtp_send_sync, subject, message)
        return True
    except Exception as e:
        print(f"[smtp] Error: {e}")
        return False


# ── Webhook Parsers ───────────────────────────────────────────────────────────

def parse_tautulli(data: dict) -> dict:
    subject  = data.get("subject", data.get("title", "Tautulli Notification"))
    body     = data.get("body",    data.get("message", ""))
    action   = data.get("action",  data.get("trigger", ""))
    title    = f"{action}: {subject}" if action else subject
    message  = body or json.dumps(data, indent=2)[:500]
    priority = "high" if action in ("buffer", "error") else "normal"
    return {"title": title, "message": message, "priority": priority}


def parse_generic(data: dict) -> dict:
    title = (
        data.get("title") or data.get("subject") or data.get("name") or
        data.get("event") or "Webhook Received"
    )
    message = (
        data.get("message") or data.get("body") or data.get("text") or
        data.get("description") or data.get("content") or
        json.dumps(data, indent=2)[:500]
    )
    priority = data.get("priority", "normal")
    if priority not in ("low", "normal", "high", "critical"):
        priority = "normal"
    return {"title": str(title), "message": str(message), "priority": priority}


PARSERS = {
    "tautulli": parse_tautulli,
}


# ── App ───────────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="WebhookHub", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
os.makedirs(STATIC_DIR, exist_ok=True)

PRIORITY_TO_PUSHOVER = {"low": -1, "normal": 0, "high": 1, "critical": 2}


# ── Webhook Ingest ────────────────────────────────────────────────────────────

@app.post("/webhook/{channel_slug}")
async def receive_webhook(channel_slug: str, request: Request):
    if API_KEY:
        if (request.headers.get("X-API-Key", "") != API_KEY and
                request.query_params.get("token", "") != API_KEY):
            raise HTTPException(status_code=401, detail="Invalid API key")

    db = get_db()
    channel = db.execute("SELECT * FROM channels WHERE slug = ?", (channel_slug,)).fetchone()
    if not channel:
        db.execute(
            "INSERT INTO channels (slug, name, description) VALUES (?, ?, ?)",
            (channel_slug,
             channel_slug.replace("-", " ").replace("_", " ").title(),
             f"Auto-created channel: {channel_slug}"),
        )
        db.commit()
        channel = db.execute("SELECT * FROM channels WHERE slug = ?", (channel_slug,)).fetchone()

    content_type = request.headers.get("content-type", "")
    raw_body     = await request.body()
    raw_body_str = raw_body.decode("utf-8", errors="replace")

    data = {}
    if "application/json" in content_type:
        try:
            data = json.loads(raw_body_str)
        except json.JSONDecodeError:
            data = {"raw": raw_body_str}
    elif "application/x-www-form-urlencoded" in content_type or "multipart/form-data" in content_type:
        form = await request.form()
        data = dict(form)
    else:
        data = {"raw": raw_body_str}

    parser = PARSERS.get(channel_slug, parse_generic)
    parsed = parser(data) if isinstance(data, dict) else parse_generic({"raw": str(data)})

    if request.query_params.get("title"):    parsed["title"]    = request.query_params["title"]
    if request.query_params.get("message"):  parsed["message"]  = request.query_params["message"]
    if request.query_params.get("priority"): parsed["priority"] = request.query_params["priority"]

    source_ip    = request.client.host if request.client else "unknown"
    headers_dict = dict(request.headers)

    db.execute(
        """INSERT INTO webhooks
           (channel_slug, title, message, priority, source_ip, raw_headers, raw_body, parsed_data)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            channel_slug, parsed["title"], parsed["message"], parsed["priority"],
            source_ip, json.dumps(headers_dict), raw_body_str,
            json.dumps(data) if isinstance(data, dict) else raw_body_str,
        ),
    )
    webhook_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.commit()

    # Pushover
    pushover_sent = False
    if channel["pushover_enabled"]:
        po_priority = PRIORITY_TO_PUSHOVER.get(parsed["priority"], 0)
        if channel["pushover_priority"] > po_priority:
            po_priority = channel["pushover_priority"]
        pushover_sent = await send_pushover(
            title=f"[{channel['name']}] {parsed['title']}",
            message=parsed["message"],
            priority=po_priority,
            sound=channel["pushover_sound"],
        )
        db.execute("UPDATE webhooks SET pushover_sent = ? WHERE id = ?",
                   (1 if pushover_sent else 0, webhook_id))
        db.commit()

    # Discord + SMTP (fire-and-forget, do not block the response)
    asyncio.create_task(send_discord(
        title=parsed["title"], message=parsed["message"],
        priority=parsed["priority"], channel_name=channel["name"],
    ))
    asyncio.create_task(send_smtp(
        title=parsed["title"], message=parsed["message"],
        channel_name=channel["name"],
    ))

    db.close()
    return JSONResponse(
        {"status": "ok", "id": webhook_id, "pushover_sent": pushover_sent},
        status_code=200,
    )


# ── API: Channels ─────────────────────────────────────────────────────────────

@app.get("/api/channels")
async def list_channels():
    db = get_db()
    channels = db.execute("""
        SELECT c.*,
               COUNT(w.id) as webhook_count,
               MAX(w.received_at) as last_received
        FROM channels c
        LEFT JOIN webhooks w ON w.channel_slug = c.slug
        GROUP BY c.id
        ORDER BY c.name
    """).fetchall()
    db.close()
    return [dict(c) for c in channels]


@app.post("/api/channels")
async def create_channel(request: Request):
    data = await request.json()
    slug = data.get("slug", "").strip().lower().replace(" ", "-")
    name = data.get("name", slug.title())
    if not slug:
        raise HTTPException(400, "slug is required")

    db = get_db()
    try:
        db.execute(
            """INSERT INTO channels (slug, name, description, icon, color,
               pushover_enabled, pushover_priority, pushover_sound)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                slug, name,
                data.get("description", ""),
                data.get("icon", "📡"),
                data.get("color", "#6366f1"),
                1 if data.get("pushover_enabled", True) else 0,
                data.get("pushover_priority", 0),
                data.get("pushover_sound", "pushover"),
            ),
        )
        db.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(409, "Channel already exists")
    finally:
        db.close()
    return {"status": "created", "slug": slug}


@app.put("/api/channels/{slug}")
async def update_channel(slug: str, request: Request):
    data    = await request.json()
    db      = get_db()
    channel = db.execute("SELECT * FROM channels WHERE slug = ?", (slug,)).fetchone()
    if not channel:
        raise HTTPException(404, "Channel not found")

    db.execute(
        """UPDATE channels SET
           name=?, description=?, icon=?, color=?,
           pushover_enabled=?, pushover_priority=?, pushover_sound=?,
           updated_at=strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
           WHERE slug=?""",
        (
            data.get("name",              channel["name"]),
            data.get("description",       channel["description"]),
            data.get("icon",              channel["icon"]),
            data.get("color",             channel["color"]),
            1 if data.get("pushover_enabled", channel["pushover_enabled"]) else 0,
            data.get("pushover_priority", channel["pushover_priority"]),
            data.get("pushover_sound",    channel["pushover_sound"]),
            slug,
        ),
    )
    db.commit()
    db.close()
    return {"status": "updated"}


@app.delete("/api/channels/{slug}")
async def delete_channel(slug: str):
    db = get_db()
    db.execute("DELETE FROM webhooks WHERE channel_slug = ?", (slug,))
    db.execute("DELETE FROM channels WHERE slug = ?", (slug,))
    db.commit()
    db.close()
    return {"status": "deleted"}


# ── API: Webhooks ─────────────────────────────────────────────────────────────

@app.get("/api/webhooks")
async def list_webhooks(
    channel:  Optional[str] = None,
    priority: Optional[str] = None,
    limit:    int = Query(default=50, le=500),
    offset:   int = 0,
    search:   Optional[str] = None,
):
    db     = get_db()
    query  = "SELECT * FROM webhooks WHERE 1=1"
    params = []

    if channel:  query += " AND channel_slug = ?"; params.append(channel)
    if priority: query += " AND priority = ?";     params.append(priority)
    if search:
        query += " AND (title LIKE ? OR message LIKE ?)"
        params.extend([f"%{search}%", f"%{search}%"])

    total = db.execute(query.replace("SELECT *", "SELECT COUNT(*)"), params).fetchone()[0]
    query += " ORDER BY received_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    webhooks = db.execute(query, params).fetchall()
    db.close()
    return {"total": total, "limit": limit, "offset": offset, "webhooks": [dict(w) for w in webhooks]}


@app.get("/api/webhooks/{webhook_id}")
async def get_webhook(webhook_id: int):
    db      = get_db()
    webhook = db.execute("SELECT * FROM webhooks WHERE id = ?", (webhook_id,)).fetchone()
    db.close()
    if not webhook:
        raise HTTPException(404, "Webhook not found")
    return dict(webhook)


@app.delete("/api/webhooks/{webhook_id}")
async def delete_webhook(webhook_id: int):
    db = get_db()
    db.execute("DELETE FROM webhooks WHERE id = ?", (webhook_id,))
    db.commit()
    db.close()
    return {"status": "deleted"}


@app.delete("/api/webhooks")
async def clear_webhooks(channel: Optional[str] = None, older_than: Optional[str] = None):
    db     = get_db()
    query  = "DELETE FROM webhooks WHERE 1=1"
    params = []
    if channel:    query += " AND channel_slug = ?"; params.append(channel)
    if older_than: query += " AND received_at < ?";  params.append(older_than)
    db.execute(query, params)
    db.commit()
    count = db.execute("SELECT changes()").fetchone()[0]
    db.close()
    return {"status": "cleared", "deleted": count}


# ── API: Stats ────────────────────────────────────────────────────────────────

@app.get("/api/stats")
async def get_stats():
    db    = get_db()
    stats = {
        "total_webhooks": db.execute("SELECT COUNT(*) FROM webhooks").fetchone()[0],
        "total_channels": db.execute("SELECT COUNT(*) FROM channels").fetchone()[0],
        "today_count":    db.execute(
            "SELECT COUNT(*) FROM webhooks WHERE date(received_at) = date('now')"
        ).fetchone()[0],
        "pushover_sent":  db.execute(
            "SELECT COUNT(*) FROM webhooks WHERE pushover_sent = 1"
        ).fetchone()[0],
        "by_priority": {},
        "by_channel":  {},
    }
    for row in db.execute("SELECT priority, COUNT(*) as cnt FROM webhooks GROUP BY priority"):
        stats["by_priority"][row["priority"]] = row["cnt"]
    for row in db.execute("""
        SELECT c.name, c.icon, c.color, COUNT(w.id) as cnt
        FROM channels c LEFT JOIN webhooks w ON w.channel_slug = c.slug
        GROUP BY c.slug ORDER BY cnt DESC
    """):
        stats["by_channel"][row["name"]] = {
            "count": row["cnt"], "icon": row["icon"], "color": row["color"],
        }
    db.close()
    return stats


# ── API: Settings ─────────────────────────────────────────────────────────────

@app.get("/api/settings")
async def get_settings():
    db   = get_db()
    rows = db.execute("SELECT key, value FROM settings").fetchall()
    db.close()
    result = dict(DEFAULT_SETTINGS)
    for row in rows:
        result[row["key"]] = row["value"]
    return result


@app.post("/api/settings")
async def update_settings(request: Request):
    data = await request.json()
    db   = get_db()
    for key, value in data.items():
        if key in DEFAULT_SETTINGS:
            db.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value))
            )
    db.commit()
    db.close()
    return {"status": "saved"}


# ── API: Notification status ──────────────────────────────────────────────────

@app.get("/api/notification-status")
async def notification_status():
    return {
        "pushover": bool(PUSHOVER_USER_KEY and PUSHOVER_API_TOKEN),
        "discord":  bool(DISCORD_WEBHOOK_URL),
        "smtp":     bool(SMTP_HOST and SMTP_USER and SMTP_PASS and SMTP_FROM and SMTP_TO),
    }


# ── API: Test ─────────────────────────────────────────────────────────────────

@app.post("/api/test")
async def send_test(request: Request):
    data         = await request.json()
    channel_slug = data.get("channel", "general")
    title        = data.get("title",   "Test Notification")
    message      = data.get("message", "This is a test from WebhookHub!")

    db      = get_db()
    channel = db.execute("SELECT * FROM channels WHERE slug = ?", (channel_slug,)).fetchone()

    pushover_sent = False
    if channel and channel["pushover_enabled"]:
        pushover_sent = await send_pushover(
            title=f"[{channel['name']}] {title}",
            message=message,
            priority=0,
            sound=channel["pushover_sound"] if channel else "pushover",
        )

    db.execute(
        """INSERT INTO webhooks
           (channel_slug, title, message, priority, source_ip, raw_body, pushover_sent)
           VALUES (?, ?, ?, 'normal', 'dashboard', '{}', ?)""",
        (channel_slug, title, message, 1 if pushover_sent else 0),
    )
    db.commit()
    db.close()

    channel_name = channel["name"] if channel else channel_slug
    asyncio.create_task(send_discord(title=title, message=message, channel_name=channel_name))
    asyncio.create_task(send_smtp(title=title, message=message, channel_name=channel_name))

    return {"status": "sent", "pushover_sent": pushover_sent}


# ── Dashboard ─────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def dashboard():
    index_path = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(index_path):
        with open(index_path, "r") as f:
            return HTMLResponse(f.read())
    return HTMLResponse("<h1>WebhookHub</h1><p>Dashboard not found. Place index.html in static/</p>")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
