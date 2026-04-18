# Instagram -> Telegram Relay Bot

Single-process Python service that relays new posts/reels from public Instagram accounts to Telegram chats.

## Features

- Telegram bot control via BotFather token (long polling)
- Commands:
  - `/addlink <ig_username> <chat_id> [--stories]`
  - `/removelink <ig_username> <chat_id>`
  - `/listlinks`
  - `/pause <ig_username> <chat_id>`
  - `/resume <ig_username> <chat_id>`
  - `/help`
- Many-to-many links between Instagram accounts and Telegram chats
- SQLite persistence
- Initial backfill of last 3 posts on `/addlink`
- Dedupe across restarts
- Retry/cooldown on Instagram fetch failures
- Best-effort story forwarding when `--stories` is enabled
- Optional third-party fallback via Apify when the VPS IP is rate-limited by Instagram

## Important notes

- Only public Instagram accounts are supported.
- Stories without login are unstable by nature and may fail.
- Instagram can change anti-bot behavior at any time; this design includes retries but cannot guarantee zero interruptions.
- For fallback scraping via Apify you need your own API token and actor configuration.

## Quick start

1. Create venv and install dependencies:

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

2. Configure environment:

```bash
cp .env.example .env
# edit .env
```

3. Run:

```bash
python main.py
```

## Environment variables

- `TELEGRAM_BOT_TOKEN` (required)
- `ADMIN_USER_IDS` (required, comma-separated Telegram user IDs)
- `DB_PATH` (default: `./ig2tel.db`)
- `POLL_INTERVAL_SECONDS` (default: `180`)
- `MAX_FETCH_ITEMS` (default: `8`)
- `TMP_DIR` (default: `./tmp`)
- `LOG_LEVEL` (default: `INFO`)

Optional fallback:
- `APIFY_TOKEN` (empty means disabled)
- `APIFY_ACTOR_ID` (default: `instagram-scraper/instagram-profile-posts-scraper`)
- `APIFY_TIMEOUT_SECONDS` (default: `60`)

## VPS (systemd)

A service file is included at `deploy/ig2tel.service`.

Typical install:

```bash
sudo cp deploy/ig2tel.service /etc/systemd/system/ig2tel.service
sudo systemctl daemon-reload
sudo systemctl enable --now ig2tel.service
sudo systemctl status ig2tel.service
```

## Data model

- `admins`
- `links`
- `checkpoints`
- `sent_items`
- `seen_stories`

## Tests

```bash
python -m unittest discover -s tests -p "test_*.py"
```