from __future__ import annotations

import json
import shutil
import subprocess
from datetime import UTC, datetime
from typing import Any

from ig2tel.models import IGMedia, IGPost
from ig2tel.providers.instagram.base import ProviderError, ProviderUnavailableError


class GalleryDlInstagramProvider:
    def __init__(self, command: str = "gallery-dl", timeout_seconds: int = 40) -> None:
        resolved = shutil.which(command)
        if not resolved:
            raise ProviderUnavailableError(f"gallery-dl command not found: {command}")

        self._command = resolved
        self._timeout_seconds = timeout_seconds

    def fetch_recent_posts(self, username: str, limit: int = 20) -> list[IGPost]:
        url = f"https://www.instagram.com/{username}/"
        args = [
            self._command,
            "--dump-json",
            "--simulate",
            "-o",
            "extractor.instagram.include=posts,reels",
            "-o",
            f"extractor.instagram.max-posts={max(1, limit)}",
            url,
        ]

        try:
            result = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=self._timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ProviderError("gallery-dl timeout while fetching Instagram posts") from exc

        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            if "429" in stderr or "Too Many Requests" in stderr:
                raise ProviderError("gallery-dl hit Instagram rate-limit (429)")
            raise ProviderError(f"gallery-dl failed: {stderr[:500]}")

        posts = self._parse_entries(username, result.stdout)
        if not posts:
            raise ProviderError("gallery-dl returned no parseable posts")

        posts.sort(key=lambda x: x.taken_at, reverse=True)
        return posts[:limit]

    def _parse_entries(self, username: str, stdout: str) -> list[IGPost]:
        grouped: dict[str, dict[str, Any]] = {}

        for line in stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(entry, dict):
                continue

            post_key = self._extract_post_key(entry)
            if not post_key:
                continue

            post_bucket = grouped.setdefault(
                post_key,
                {
                    "caption": self._pick(entry, ["caption", "description", "content", "text"]) or "",
                    "taken_at": self._parse_time(
                        self._pick(entry, ["date", "date_utc", "timestamp", "taken_at", "takenAt"])
                    ),
                    "shortcode": self._pick(entry, ["shortcode", "code", "shortCode"]),
                    "permalink": self._pick(entry, ["post_url", "postUrl", "permalink"]),
                    "media": [],
                    "seen_urls": set(),
                },
            )

            media = self._extract_media(entry)
            for media_item in media:
                if media_item.url in post_bucket["seen_urls"]:
                    continue
                post_bucket["seen_urls"].add(media_item.url)
                post_bucket["media"].append(media_item)

        posts: list[IGPost] = []
        for post_key, data in grouped.items():
            media = data["media"]
            if not media:
                continue

            shortcode = data.get("shortcode")
            permalink = data.get("permalink")
            if not permalink:
                if shortcode:
                    permalink = f"https://www.instagram.com/p/{shortcode}/"
                else:
                    permalink = f"https://www.instagram.com/{username}/"

            item_type = "carousel" if len(media) > 1 else ("reel" if media[0].media_type == "video" else "post")

            posts.append(
                IGPost(
                    item_id=str(post_key),
                    username=username,
                    item_type=item_type,
                    caption=str(data.get("caption") or "").strip(),
                    permalink=str(permalink),
                    taken_at=data["taken_at"],
                    media=media,
                )
            )

        return posts

    def _extract_post_key(self, entry: dict[str, Any]) -> str | None:
        for key in [
            "post_id",
            "media_id",
            "id",
            "shortcode",
            "code",
            "shortCode",
            "post_shortcode",
        ]:
            value = entry.get(key)
            if value not in (None, ""):
                return str(value)

        post_url = self._pick(entry, ["post_url", "postUrl", "permalink"])
        if post_url:
            return str(post_url)

        return None

    def _extract_media(self, entry: dict[str, Any]) -> list[IGMedia]:
        media: list[IGMedia] = []

        video_url = self._pick(entry, ["video_url", "videoUrl"])
        image_url = self._pick(entry, ["display_url", "displayUrl", "image_url", "url"])

        if video_url:
            media.append(IGMedia(media_type="video", url=str(video_url)))
        elif image_url:
            media.append(IGMedia(media_type="photo", url=str(image_url)))

        return media

    @staticmethod
    def _pick(source: dict[str, Any], keys: list[str]) -> Any:
        for key in keys:
            if key in source and source[key] not in (None, ""):
                return source[key]
        return None

    @staticmethod
    def _parse_time(value: Any) -> datetime:
        if value is None:
            return datetime.now(tz=UTC)

        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(float(value), tz=UTC)

        if isinstance(value, str):
            text = value.strip()
            if text.isdigit():
                return datetime.fromtimestamp(float(text), tz=UTC)
            try:
                parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    return parsed.replace(tzinfo=UTC)
                return parsed.astimezone(UTC)
            except ValueError:
                return datetime.now(tz=UTC)

        return datetime.now(tz=UTC)