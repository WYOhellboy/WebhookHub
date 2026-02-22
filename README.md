# WebhookHub

A self-hosted webhook receiver, dashboard, and push notification relay. Replace Discord webhooks with something you own.

![Stack: Python + FastAPI + SQLite + Pushover](https://img.shields.io/badge/stack-FastAPI%20%2B%20SQLite%20%2B%20Pushover-blue)

## Features

- **Universal webhook receiver** — accepts JSON or form-encoded POST from any source
- **Auto-channel creation** — POST to any slug and the channel is created automatically
- **Real-time web dashboard** — monitor, search, filter, and inspect all webhooks
- **Right-click context menu** — right-click any webhook or channel to delete it instantly
- **Settings page** — configure branding, typography, cleanup, and view notification backend status from the UI
- **Multiple notification backends** — Pushover, Discord, and SMTP email, all running in parallel
- **Rich notification content** — enrichment fields (player, user, IP, timestamp, image) forwarded to all backends
- **Inline images** — images render as thumbnails in the feed and full-size in the detail modal; forwarded to Pushover (attachment), Discord (embed image), and HTML email
- **Newline rendering** — `\n` in messages renders as line breaks in the dashboard
- **Push suppression** — add `"push": false` to any payload to skip Pushover for that webhook
- **Built-in parsers** — Tautulli parser included, generic parser handles everything else
- **Priority levels** — low / normal / high / critical with visual indicators
- **Search & filter** — by channel, priority, and free-text search
- **Automatic cleanup** — configurable background task to delete webhooks older than N days
- **Expanded font picker** — 20 fonts in 4 categories with live preview
- **Mobile-responsive** — bottom-sheet modals, horizontal sidebar, stacked controls on small screens
- **Dual-port design** — dashboard on 8080 (proxy-protected), ingest on 8181 (publicly reachable)
- **API key authentication** — all webhook POSTs require a pre-shared key
- **Docker-ready** — `docker compose up --build -d` to deploy

## Port Layout

| Port | Purpose | Exposure |
|------|---------|----------|
| `8080` | Dashboard + management API | Put behind an authenticated HTTPS reverse proxy |
| `8181` | Webhook ingest only (`POST /webhook/{slug}`) | Publicly reachable for services to POST to |

The ingest port only accepts `POST /webhook/{slug}` requests. All dashboard and API management endpoints are exclusively on port 8080.

## Quick Start

### 1. Get Pushover credentials

1. Create a [Pushover](https://pushover.net) account ($5 one-time per platform)
2. Install the Pushover app on your phone
3. From the Pushover dashboard, copy your **User Key**
4. Create a new Application/API Token — name it "WebhookHub"
5. Copy the **API Token**

### 2. Configure and deploy

Open `docker-compose.yml` and fill in your Pushover credentials. The `WEBHOOKHUB_API_KEY` is pre-generated — keep it or replace it with your own value.

```bash
docker compose up --build -d
```

> **Important:** Because the application code is baked into the Docker image at build time, you must use `--build` every time you deploy code changes. `docker compose restart` alone will not apply updates.

- Dashboard: `http://your-server:8080`
- Webhook ingest: `http://your-server:8181/webhook/{channel_slug}`

### 3. Set up your reverse proxy

Point your HTTPS reverse proxy at port `8080` and protect it with basic auth or another authentication method. Leave port `8181` open so your services can POST webhooks directly.

Example Nginx proxy block for the dashboard:

```nginx
location / {
    proxy_pass http://localhost:8080;
    auth_basic "WebhookHub";
    auth_basic_user_file /etc/nginx/.htpasswd;
}
```

### 4. Start sending webhooks

All webhook POST requests must include the API key via the `X-API-Key` header or a `?token=` query parameter.

```bash
# Simple notification
curl -X POST http://your-server:8181/webhook/general \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your_api_key" \
  -d '{"title": "Hello", "message": "WebhookHub is working!"}'

# Custom channel (auto-created if it doesn't exist)
curl -X POST http://your-server:8181/webhook/backup-alerts \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your_api_key" \
  -d '{"title": "Backup Complete", "message": "TrueNAS backup finished successfully", "priority": "low"}'

# High priority alert
curl -X POST http://your-server:8181/webhook/security \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your_api_key" \
  -d '{"title": "SSH Login", "message": "Root login from 10.0.0.5", "priority": "high"}'

# API key as query parameter (useful for tools that only support URLs)
curl -X POST "http://your-server:8181/webhook/updates?title=apt+upgrade&message=15+packages+updated&priority=low&token=your_api_key"
```

## Webhook Endpoint

```
POST /webhook/{channel_slug}
```

Available on both port `8080` and port `8181`. All requests require the API key.

### Standard fields (JSON body)

| Field     | Type   | Description                                    |
|-----------|--------|------------------------------------------------|
| title     | string | Notification title                             |
| message   | string | Notification body — `\n` renders as line breaks in the dashboard |
| priority  | string | `low`, `normal`, `high`, or `critical`         |
| push      | bool   | Set to `false` to suppress Pushover for this webhook only |

The parser also checks for common alternative field names: `subject`, `body`, `text`, `description`, `content`, `event`, `name`.

### Enrichment fields (optional)

These fields are stored alongside the webhook, displayed in the card footer and detail modal, and forwarded to all notification backends.

| Field           | Type   | Aliases accepted           | Description                          |
|-----------------|--------|----------------------------|--------------------------------------|
| player          | string | `player`                   | Player or entity name                |
| user            | string | `user`, `username`, `user_name` | User account name               |
| ipaddress       | string | `ipaddress`, `ip_address`, `ip`, `source_ip` | Source IP address   |
| timestamp       | string | `timestamp`, `event_timestamp`, `time`, `date` | Event timestamp    |
| image           | string | `image`, `image_url`, `imageurl`, `thumbnail` | URL of an image to display |

**Dashboard:** Images appear as 100px thumbnails on the card and at full resolution in the detail modal.

**Pushover:** The image is attached as an inline photo (`attachment_url`).

**Discord:** Enrichment fields appear as inline embed fields; the image is displayed as the embed's full-width image.

**Email:** Enrichment fields appear in a formatted table; the image is embedded inline in the HTML body.

### Query parameter overrides

Append `?title=...&message=...&priority=...&push=false` to override parsed values. All enrichment fields also accept query parameters using their primary name (`?player=...&image=...` etc.). Useful for simple shell scripts that cannot set a JSON body.

### Push suppression

Add `"push": false` to the JSON payload (or `?push=false` as a query parameter) to skip Pushover for that specific webhook. Discord and email notifications are unaffected. If the field is omitted, Pushover sends as normal.

```bash
# Store webhook and send Discord/email — skip Pushover
curl -X POST http://your-server:8181/webhook/verbose-logs \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your_api_key" \
  -d '{"title": "Log dump", "message": "...", "push": false}'
```

### API Key authentication

All webhook POSTs are protected by `WEBHOOKHUB_API_KEY`. Include it with every request:

```bash
# Header (preferred)
-H "X-API-Key: your_api_key"

# Query parameter
?token=your_api_key
```

The key is set in `docker-compose.yml` under `WEBHOOKHUB_API_KEY`. To rotate the key, update the value and run `docker compose up --build -d`.

## Dashboard

Open `http://your-server:8080` (or your proxy URL) to access the dashboard.

- **Left sidebar** — channel list; click to filter the feed
- **Right-click any webhook card** — context menu with a Delete option
- **Right-click any channel card** — context menu with a Delete option (also deletes all webhooks in that channel)
- **Click a webhook card** — opens the detail modal with all fields, raw payload, and headers
- **Filter tabs** — filter by priority (All / Critical / High / Normal / Low)
- **Search bar** — full-text search across title and message (`/` to focus)
- **Send Test button** — sends a test notification to all configured backends
- **⚙ Settings button** — opens the settings panel
- **Auto-refresh** — feed refreshes every 10 seconds without flickering
- **Footer** — displays the app name and version; updates with your Header Text setting
- **Mobile layout** — sidebar scrolls horizontally, modals open as bottom sheets, controls stack vertically on narrow screens

## Settings

Click **⚙ Settings** in the top-right corner. Changes are saved to the database and persist across restarts and devices.

### Branding

| Setting | Description |
|---------|-------------|
| Page Title | Text shown in the browser tab |
| Header Text | Text shown in the top-left of the dashboard and in the footer |
| Favicon URL | URL to a `.svg`, `.png`, or `.ico` file — defaults to the built-in blue WH icon |

### Typography

| Setting | Options |
|---------|---------|
| Font Family | 20 fonts across 4 groups: Sans-serif (DM Sans, Inter, Roboto, Nunito, Lato, Poppins, Montserrat, Raleway, Oswald, Ubuntu), Serif (Georgia, Times New Roman), Monospace (JetBrains Mono, Fira Code), Fun/Decorative (Comic Sans MS, Pacifico, Permanent Marker, Press Start 2P, Bangers, System UI) — live preview on change |
| Font Size | Small (12px), Compact (13px), Normal (14px), Large (15px), Extra Large (16px) |
| Accent Color | Color picker — affects buttons, links, active states, and stat highlights |
| Primary Text | Color picker — main content text |
| Secondary Text | Color picker — metadata and subdued text |

### Automatic Cleanup

| Setting | Description |
|---------|-------------|
| Enable automatic cleanup | Toggle the background cleanup task on or off |
| Delete webhooks older than | Number of days — webhooks older than this are deleted automatically |
| Last cleanup run | Read-only timestamp of the most recent cleanup run |

The cleanup task runs once per hour and removes all webhooks older than the configured threshold. Run **Cleanup Now** to trigger it immediately. All cleanup settings persist in the database.

### Notification Status

Displays a live green/grey indicator for each notification backend (Pushover, Discord, SMTP). Read-only — configure backends via environment variables in `docker-compose.yml`.

The **Reset Defaults** button restores all settings to their original values.

## Notification Backends

Each incoming webhook triggers all configured backends simultaneously. Backends are enabled by setting their environment variables in `docker-compose.yml` and running `docker compose up --build -d`.

### Pushover

Required: `PUSHOVER_USER_KEY` and `PUSHOVER_API_TOKEN`. Per-channel settings (priority, sound, enable/disable) are configured from the channel list in the dashboard.

If the webhook payload includes an `image` URL, it is sent to Pushover as an inline photo attachment. If `"push": false` is set in the payload, Pushover is skipped entirely for that webhook.

### Discord

Set `DISCORD_WEBHOOK_URL` to a Discord channel webhook URL. Each notification is sent as a colour-coded embed:

| Priority | Embed color |
|----------|-------------|
| low      | Grey        |
| normal   | Blue        |
| high     | Amber       |
| critical | Red         |

Enrichment fields (player, user, IP, timestamp) appear as inline embed fields. If an image URL is provided it is displayed as the embed's full-width image.

```yaml
- DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/your/webhook
```

### SMTP Email

Set all six SMTP variables. Port `465` uses implicit SSL; all other ports use STARTTLS. `SMTP_TO` accepts a comma-separated list of recipients.

```yaml
- SMTP_HOST=smtp.gmail.com
- SMTP_PORT=587
- SMTP_USER=you@gmail.com
- SMTP_PASS=your_app_password
- SMTP_FROM=you@gmail.com
- SMTP_TO=you@gmail.com,other@gmail.com
```

Emails are sent as `multipart/alternative` with both plain text and HTML parts. The HTML part includes a formatted enrichment field table and, if an image URL is provided, an inline image.

For Gmail, generate an [App Password](https://myaccount.google.com/apppasswords) rather than using your account password.

## Integration Examples

### Tautulli

1. In Tautulli, go to **Settings → Notification Agents → Add**
2. Select **Webhook**
3. Set the Webhook URL to: `http://your-server:8181/webhook/tautulli`
4. Add a custom header: `X-API-Key: your_api_key`
5. Set the method to **POST** and content type to **JSON**
6. Configure which events trigger notifications

### SSH Login Alerts

```bash
#!/bin/bash
# /etc/ssh/sshrc
WEBHOOK_URL="http://your-server:8181/webhook/security"
USER_INFO="$USER from ${SSH_CONNECTION%% *}"
HOSTNAME=$(hostname)

curl -sS -X POST "$WEBHOOK_URL" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your_api_key" \
  -d "{\"title\":\"SSH Login: $HOSTNAME\",\"message\":\"$USER_INFO logged in at $(date)\",\"priority\":\"high\",\"user\":\"$USER\",\"ipaddress\":\"${SSH_CONNECTION%% *}\"}" &
```

### apt/dnf Update Notifications

For apt, add to `/etc/apt/apt.conf.d/99webhook`:

```
DPkg::Post-Invoke {
  "curl -sS -X POST http://your-server:8181/webhook/updates -H 'Content-Type: application/json' -H 'X-API-Key: your_api_key' -d '{\"title\":\"apt upgrade\",\"message\":\"Packages updated on '$(hostname)'\",\"priority\":\"low\"}' &";
};
```

### Cron job / Script notifications

```bash
#!/bin/bash
RESULT=$(rsync -a /data /backup 2>&1)
STATUS=$?

if [ $STATUS -eq 0 ]; then
  PRIORITY="low"
  TITLE="Backup Success"
else
  PRIORITY="critical"
  TITLE="Backup FAILED"
fi

curl -sS -X POST http://your-server:8181/webhook/backups \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your_api_key" \
  -d "{\"title\":\"$TITLE\",\"message\":\"$(echo $RESULT | head -c 500)\",\"priority\":\"$PRIORITY\"}"
```

### Game server event (with enrichment fields)

```bash
curl -X POST http://your-server:8181/webhook/gameserver \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your_api_key" \
  -d '{
    "title": "Player Joined",
    "message": "Steve connected to the server.",
    "priority": "low",
    "player": "Steve",
    "ipaddress": "203.0.113.42",
    "timestamp": "2025-01-15T14:32:00Z"
  }'
```

### CheckMK (notification script)

Create `/omd/sites/yoursite/local/share/check_mk/notifications/webhookhub`:

```bash
#!/bin/bash
WEBHOOK_URL="http://your-server:8181/webhook/checkmk"
API_KEY="your_api_key"

if [ "$NOTIFY_WHAT" = "HOST" ]; then
  TITLE="Host $NOTIFY_HOSTNAME is $NOTIFY_HOSTSTATE"
  MSG="$NOTIFY_HOSTOUTPUT"
else
  TITLE="$NOTIFY_HOSTNAME/$NOTIFY_SERVICEDESC is $NOTIFY_SERVICESTATE"
  MSG="$NOTIFY_SERVICEOUTPUT"
fi

PRIO="normal"
[ "$NOTIFY_HOSTSTATE" = "DOWN" ] || [ "$NOTIFY_SERVICESTATE" = "CRITICAL" ] && PRIO="critical"
[ "$NOTIFY_SERVICESTATE" = "WARNING" ] && PRIO="high"

curl -sS -X POST "$WEBHOOK_URL" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_KEY" \
  -d "{\"title\":\"$TITLE\",\"message\":\"$MSG\",\"priority\":\"$PRIO\"}"
```

## API Reference

All management endpoints are on port `8080` only. The ingest endpoint is available on both ports.

| Method | Endpoint                       | Port        | Description                        |
|--------|--------------------------------|-------------|-------------------------------------|
| POST   | `/webhook/{slug}`              | 8080 + 8181 | Receive a webhook                   |
| GET    | `/api/channels`                | 8080        | List all channels                   |
| POST   | `/api/channels`                | 8080        | Create a channel                    |
| PUT    | `/api/channels/{slug}`         | 8080        | Update a channel                    |
| DELETE | `/api/channels/{slug}`         | 8080        | Delete channel + its webhooks       |
| GET    | `/api/webhooks`                | 8080        | List webhooks (with filters)        |
| GET    | `/api/webhooks/{id}`           | 8080        | Get webhook detail                  |
| DELETE | `/api/webhooks/{id}`           | 8080        | Delete a webhook                    |
| DELETE | `/api/webhooks`                | 8080        | Bulk delete (by channel/age)        |
| GET    | `/api/stats`                   | 8080        | Dashboard statistics                |
| GET    | `/api/settings`                | 8080        | Get current settings                |
| POST   | `/api/settings`                | 8080        | Update settings                     |
| GET    | `/api/notification-status`     | 8080        | Check which backends are configured |
| POST   | `/api/test`                    | 8080        | Send a test notification            |

### Query parameters for GET /api/webhooks

| Param    | Description                    |
|----------|--------------------------------|
| channel  | Filter by channel slug         |
| priority | Filter by priority level       |
| search   | Free-text search in title/msg  |
| limit    | Results per page (max 500)     |
| offset   | Pagination offset              |

## Configuration

All configuration is via environment variables in `docker-compose.yml`:

| Variable               | Required | Description                                             |
|------------------------|----------|---------------------------------------------------------|
| `PUSHOVER_USER_KEY`    | Yes      | Your Pushover user key                                  |
| `PUSHOVER_API_TOKEN`   | Yes      | Your Pushover application token                         |
| `WEBHOOKHUB_API_KEY`   | Yes      | Pre-shared key required on all webhook POSTs            |
| `WEBHOOKHUB_DB`        | No       | Database path (default: `/data/webhookhub.db`)          |
| `DISCORD_WEBHOOK_URL`  | No       | Discord channel webhook URL                             |
| `SMTP_HOST`            | No       | SMTP server hostname                                    |
| `SMTP_PORT`            | No       | SMTP port — `465` for SSL, anything else uses STARTTLS  |
| `SMTP_USER`            | No       | SMTP login username                                     |
| `SMTP_PASS`            | No       | SMTP login password or app password                     |
| `SMTP_FROM`            | No       | Sender address                                          |
| `SMTP_TO`              | No       | Recipient address(es), comma-separated                  |

## Adding Custom Parsers

Edit `app/main.py` and add a parser function to the `PARSERS` dict:

```python
def parse_uptime_kuma(data: dict) -> dict:
    """Custom parser for Uptime Kuma webhooks."""
    monitor   = data.get("monitor", {})
    heartbeat = data.get("heartbeat", {})

    title    = f"{monitor.get('name', 'Monitor')}: {heartbeat.get('status', 'unknown')}"
    message  = heartbeat.get("msg", "")
    priority = "critical" if heartbeat.get("status") == 0 else "normal"

    return {"title": title, "message": message, "priority": priority}

PARSERS["uptime-kuma"] = parse_uptime_kuma
```

The parser receives the decoded JSON body and must return a dict with `title`, `message`, and `priority` keys. Rebuild the container after any code changes:

```bash
docker compose up --build -d
```

## License

MIT
