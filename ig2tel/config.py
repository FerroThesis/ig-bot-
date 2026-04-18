from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _parse_admin_ids(raw: str) -> set[int]:
    values: set[int] = set()
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        normalized = token
        if normalized.lower().startswith("id:"):
            normalized = normalized.split(":", 1)[1].strip()
        values.add(int(normalized))
    return values


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    admin_user_ids: set[int]
    db_path: Path
    poll_interval_seconds: int = 180
    tmp_dir: Path = Path("tmp")
    log_level: str = "INFO"
    request_timeout_seconds: int = 30
    max_fetch_items: int = 8
    scheduler_jitter_seconds: int = 5
    telegram_api_base: str = "https://api.telegram.org"
    apify_token: str | None = None
    apify_actor_id: str = "instagram-scraper/instagram-profile-posts-scraper"
    apify_timeout_seconds: int = 60

    @classmethod
    def from_env(cls) -> "Settings":
        token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        if not token:
            raise ValueError("Missing TELEGRAM_BOT_TOKEN")

        admin_ids_raw = os.getenv("ADMIN_USER_IDS", "")
        admin_user_ids = _parse_admin_ids(admin_ids_raw)
        if not admin_user_ids:
            raise ValueError("Missing ADMIN_USER_IDS")

        db_path = Path(os.getenv("DB_PATH", "ig2tel.db")).expanduser().resolve()
        tmp_dir = Path(os.getenv("TMP_DIR", "tmp")).expanduser().resolve()

        poll_interval_seconds = int(os.getenv("POLL_INTERVAL_SECONDS", "180"))
        max_fetch_items = int(os.getenv("MAX_FETCH_ITEMS", "8"))
        log_level = os.getenv("LOG_LEVEL", "INFO").upper().strip() or "INFO"

        apify_token = os.getenv("APIFY_TOKEN", "").strip() or None
        apify_actor_id = os.getenv(
            "APIFY_ACTOR_ID",
            "instagram-scraper/instagram-profile-posts-scraper",
        ).strip()
        apify_timeout_seconds = int(os.getenv("APIFY_TIMEOUT_SECONDS", "60"))

        return cls(
            telegram_bot_token=token,
            admin_user_ids=admin_user_ids,
            db_path=db_path,
            poll_interval_seconds=poll_interval_seconds,
            tmp_dir=tmp_dir,
            log_level=log_level,
            max_fetch_items=max_fetch_items,
            apify_token=apify_token,
            apify_actor_id=apify_actor_id,
            apify_timeout_seconds=apify_timeout_seconds,
        )