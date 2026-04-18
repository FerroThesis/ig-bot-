from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterator

from ig2tel.models import LinkOperationResult, LinkRecord


def _utcnow() -> datetime:
    return datetime.now(tz=UTC)


def _to_db(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat()


def _from_db(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


class Repository:
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._lock = threading.RLock()

    def init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                PRAGMA foreign_keys = ON;

                CREATE TABLE IF NOT EXISTS admins (
                    telegram_user_id INTEGER PRIMARY KEY,
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
                );

                CREATE TABLE IF NOT EXISTS links (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ig_username TEXT NOT NULL,
                    chat_id INTEGER NOT NULL,
                    stories_enabled INTEGER NOT NULL DEFAULT 0,
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(ig_username, chat_id)
                );

                CREATE TABLE IF NOT EXISTS checkpoints (
                    link_id INTEGER PRIMARY KEY,
                    last_post_id TEXT,
                    last_post_timestamp TEXT,
                    last_story_check_at TEXT,
                    fail_count INTEGER NOT NULL DEFAULT 0,
                    next_retry_at TEXT,
                    last_error TEXT,
                    last_success_at TEXT,
                    FOREIGN KEY(link_id) REFERENCES links(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS sent_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    link_id INTEGER NOT NULL,
                    ig_item_id TEXT NOT NULL,
                    item_type TEXT NOT NULL,
                    telegram_message_id TEXT,
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL,
                    sent_at TEXT,
                    UNIQUE(link_id, ig_item_id),
                    FOREIGN KEY(link_id) REFERENCES links(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS seen_stories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    link_id INTEGER NOT NULL,
                    story_item_id TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    UNIQUE(link_id, story_item_id),
                    FOREIGN KEY(link_id) REFERENCES links(id) ON DELETE CASCADE
                );
                """
            )

    def seed_admins(self, admin_ids: set[int]) -> None:
        if not admin_ids:
            return
        with self._connect() as conn:
            now = _to_db(_utcnow())
            conn.executemany(
                "INSERT OR IGNORE INTO admins (telegram_user_id, created_at) VALUES (?, ?)",
                [(admin_id, now) for admin_id in admin_ids],
            )

    def is_admin(self, user_id: int) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM admins WHERE telegram_user_id = ?",
                (user_id,),
            ).fetchone()
            return row is not None

    def create_or_reactivate_link(
        self,
        ig_username: str,
        chat_id: int,
        stories_enabled: bool,
    ) -> LinkOperationResult:
        now = _utcnow()
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT id, active FROM links WHERE ig_username = ? AND chat_id = ?",
                (ig_username, chat_id),
            ).fetchone()

            if existing:
                link_id = int(existing["id"])
                is_active = bool(existing["active"])
                if is_active:
                    raise ValueError("Link already active")

                conn.execute(
                    """
                    UPDATE links
                    SET active = 1,
                        stories_enabled = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (1 if stories_enabled else 0, _to_db(now), link_id),
                )
                self._ensure_checkpoint(conn, link_id)
                return LinkOperationResult(
                    link=self._get_link_by_id(conn, link_id),
                    created=False,
                    reactivated=True,
                )

            cursor = conn.execute(
                """
                INSERT INTO links (
                    ig_username,
                    chat_id,
                    stories_enabled,
                    active,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, 1, ?, ?)
                """,
                (ig_username, chat_id, 1 if stories_enabled else 0, _to_db(now), _to_db(now)),
            )
            link_id = int(cursor.lastrowid)
            self._ensure_checkpoint(conn, link_id)
            return LinkOperationResult(
                link=self._get_link_by_id(conn, link_id),
                created=True,
                reactivated=False,
            )

    def remove_link(self, ig_username: str, chat_id: int) -> bool:
        with self._connect() as conn:
            result = conn.execute(
                "DELETE FROM links WHERE ig_username = ? AND chat_id = ?",
                (ig_username, chat_id),
            )
            return result.rowcount > 0

    def pause_link(self, ig_username: str, chat_id: int) -> bool:
        return self._set_active_flag(ig_username, chat_id, False)

    def resume_link(self, ig_username: str, chat_id: int) -> bool:
        return self._set_active_flag(ig_username, chat_id, True)

    def list_links(self) -> list[LinkRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    l.id,
                    l.ig_username,
                    l.chat_id,
                    l.stories_enabled,
                    l.active,
                    c.fail_count,
                    c.next_retry_at,
                    c.last_error,
                    c.last_success_at,
                    c.last_post_id,
                    c.last_post_timestamp
                FROM links l
                LEFT JOIN checkpoints c ON c.link_id = l.id
                ORDER BY l.ig_username ASC, l.chat_id ASC
                """
            ).fetchall()
            return [self._row_to_link(row) for row in rows]

    def get_active_links(self, now: datetime) -> list[LinkRecord]:
        now_str = _to_db(now)
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    l.id,
                    l.ig_username,
                    l.chat_id,
                    l.stories_enabled,
                    l.active,
                    c.fail_count,
                    c.next_retry_at,
                    c.last_error,
                    c.last_success_at,
                    c.last_post_id,
                    c.last_post_timestamp
                FROM links l
                LEFT JOIN checkpoints c ON c.link_id = l.id
                WHERE l.active = 1
                  AND (c.next_retry_at IS NULL OR c.next_retry_at <= ?)
                ORDER BY l.id ASC
                """,
                (now_str,),
            ).fetchall()
            return [self._row_to_link(row) for row in rows]

    def mark_link_success(
        self,
        link_id: int,
        last_post_id: str | None,
        last_post_timestamp: datetime | None,
    ) -> None:
        now = _utcnow()
        with self._connect() as conn:
            self._ensure_checkpoint(conn, link_id)
            conn.execute(
                """
                UPDATE checkpoints
                SET
                    last_post_id = ?,
                    last_post_timestamp = ?,
                    fail_count = 0,
                    next_retry_at = NULL,
                    last_error = NULL,
                    last_success_at = ?
                WHERE link_id = ?
                """,
                (
                    last_post_id,
                    _to_db(last_post_timestamp),
                    _to_db(now),
                    link_id,
                ),
            )

    def mark_link_failure(self, link_id: int, error_message: str, next_retry_at: datetime) -> None:
        with self._connect() as conn:
            self._ensure_checkpoint(conn, link_id)
            conn.execute(
                """
                UPDATE checkpoints
                SET
                    fail_count = fail_count + 1,
                    next_retry_at = ?,
                    last_error = ?
                WHERE link_id = ?
                """,
                (_to_db(next_retry_at), error_message[:500], link_id),
            )

    def mark_story_warning(self, link_id: int, warning: str) -> None:
        with self._connect() as conn:
            self._ensure_checkpoint(conn, link_id)
            conn.execute(
                """
                UPDATE checkpoints
                SET
                    last_story_check_at = ?,
                    last_error = ?
                WHERE link_id = ?
                """,
                (_to_db(_utcnow()), warning[:500], link_id),
            )

    def mark_story_check_success(self, link_id: int) -> None:
        with self._connect() as conn:
            self._ensure_checkpoint(conn, link_id)
            conn.execute(
                """
                UPDATE checkpoints
                SET
                    last_story_check_at = ?
                WHERE link_id = ?
                """,
                (_to_db(_utcnow()), link_id),
            )

    def reserve_item(self, link_id: int, ig_item_id: str, item_type: str) -> bool:
        with self._connect() as conn:
            try:
                conn.execute(
                    """
                    INSERT INTO sent_items (
                        link_id,
                        ig_item_id,
                        item_type,
                        status,
                        created_at
                    ) VALUES (?, ?, ?, 'pending', ?)
                    """,
                    (link_id, ig_item_id, item_type, _to_db(_utcnow())),
                )
                return True
            except sqlite3.IntegrityError:
                return False

    def mark_item_sent(self, link_id: int, ig_item_id: str, telegram_message_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE sent_items
                SET
                    status = 'sent',
                    telegram_message_id = ?,
                    sent_at = ?
                WHERE link_id = ? AND ig_item_id = ?
                """,
                (telegram_message_id, _to_db(_utcnow()), link_id, ig_item_id),
            )

    def release_item(self, link_id: int, ig_item_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                DELETE FROM sent_items
                WHERE link_id = ? AND ig_item_id = ? AND status = 'pending'
                """,
                (link_id, ig_item_id),
            )

    def has_seen_story(self, link_id: int, story_item_id: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM seen_stories WHERE link_id = ? AND story_item_id = ?",
                (link_id, story_item_id),
            ).fetchone()
            return row is not None

    def mark_seen_story(self, link_id: int, story_item_id: str, expires_at: datetime) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO seen_stories (link_id, story_item_id, expires_at)
                VALUES (?, ?, ?)
                """,
                (link_id, story_item_id, _to_db(expires_at)),
            )

    def purge_expired_stories(self, now: datetime) -> None:
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM seen_stories WHERE expires_at < ?",
                (_to_db(now),),
            )

    def _set_active_flag(self, ig_username: str, chat_id: int, active: bool) -> bool:
        with self._connect() as conn:
            result = conn.execute(
                """
                UPDATE links
                SET active = ?, updated_at = ?
                WHERE ig_username = ? AND chat_id = ?
                """,
                (1 if active else 0, _to_db(_utcnow()), ig_username, chat_id),
            )
            return result.rowcount > 0

    def _ensure_checkpoint(self, conn: sqlite3.Connection, link_id: int) -> None:
        conn.execute(
            "INSERT OR IGNORE INTO checkpoints (link_id) VALUES (?)",
            (link_id,),
        )

    def _get_link_by_id(self, conn: sqlite3.Connection, link_id: int) -> LinkRecord:
        row = conn.execute(
            """
            SELECT
                l.id,
                l.ig_username,
                l.chat_id,
                l.stories_enabled,
                l.active,
                c.fail_count,
                c.next_retry_at,
                c.last_error,
                c.last_success_at,
                c.last_post_id,
                c.last_post_timestamp
            FROM links l
            LEFT JOIN checkpoints c ON c.link_id = l.id
            WHERE l.id = ?
            """,
            (link_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"Link {link_id} not found")
        return self._row_to_link(row)

    def _row_to_link(self, row: sqlite3.Row) -> LinkRecord:
        return LinkRecord(
            id=int(row["id"]),
            ig_username=str(row["ig_username"]),
            chat_id=int(row["chat_id"]),
            stories_enabled=bool(row["stories_enabled"]),
            active=bool(row["active"]),
            fail_count=int(row["fail_count"] or 0),
            next_retry_at=_from_db(row["next_retry_at"]),
            last_error=row["last_error"],
            last_success_at=_from_db(row["last_success_at"]),
            last_post_id=row["last_post_id"],
            last_post_timestamp=_from_db(row["last_post_timestamp"]),
        )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            conn = sqlite3.connect(self._db_path, timeout=30)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()