from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import requests

from ig2tel.models import IGMedia, IGPost
from ig2tel.providers.instagram.base import ProviderError


class ApifyInstagramPostsProvider:
    def __init__(self, token: str, actor_id: str, timeout_seconds: int = 60) -> None:
        self._token = token
        self._actor_id = actor_id
        self._timeout_seconds = timeout_seconds

    def fetch_recent_posts(self, username: str, limit: int = 20) -> list[IGPost]:
        url = (
            f"https://api.apify.com/v2/acts/{self._actor_id}/"
            f"run-sync-get-dataset-items?token={self._token}"
        )

        payload = {
            # Commonly supported by instagram-scraper actors.
            "instagramUsernames": [username],
            "resultsLimit": max(1, limit),
        }

        response = requests.post(url, json=payload, timeout=self._timeout_seconds)
        if response.status_code >= 400:
            raise ProviderError(
                f"Apify fallback failed with HTTP {response.status_code}: {response.text[:300]}"
            )

        try:
            items = response.json()
        except ValueError as exc:
            raise ProviderError("Apify fallback returned non-JSON response") from exc

        if not isinstance(items, list):
            raise ProviderError("Apify fallback returned unexpected payload")

        posts: list[IGPost] = []
        for raw in items:
            mapped = self._map_item(username, raw)
            if mapped is not None:
                posts.append(mapped)

        if not posts:
            raise ProviderError("Apify fallback returned no parseable posts")

        posts.sort(key=lambda x: x.taken_at, reverse=True)
        return posts[:limit]

    def _map_item(self, username: str, raw: Any) -> IGPost | None:
        if not isinstance(raw, dict):
            return None

        item_id = self._pick(raw, ["id", "postId", "pk", "mediaid"])
        shortcode = self._pick(raw, ["shortCode", "shortcode", "code"])
        permalink = self._pick(raw, ["url", "postUrl", "permalink"]) or (
            f"https://www.instagram.com/p/{shortcode}/" if shortcode else f"https://www.instagram.com/{username}/"
        )

        caption = self._pick(raw, ["caption", "text", "description"]) or ""

        taken_at = self._parse_time(
            self._pick(raw, ["timestamp", "taken_at_timestamp", "createdAt", "takenAt"])
        )

        media = self._extract_media(raw)
        if not media:
            return None

        item_type = "post"
        type_value = str(self._pick(raw, ["type", "mediaType", "productType"]) or "").lower()
        if "reel" in type_value or "video" in type_value:
            item_type = "reel"
        elif "carousel" in type_value or len(media) > 1:
            item_type = "carousel"

        return IGPost(
            item_id=str(item_id or shortcode or permalink),
            username=username,
            item_type=item_type,
            caption=str(caption).strip(),
            permalink=str(permalink),
            taken_at=taken_at,
            media=media,
        )

    def _extract_media(self, raw: dict[str, Any]) -> list[IGMedia]:
        media: list[IGMedia] = []

        # Single media fields.
        video_url = self._pick(raw, ["videoUrl", "video_url"])
        image_url = self._pick(raw, ["displayUrl", "imageUrl", "thumbnailUrl", "display_url"])

        if video_url:
            media.append(IGMedia(media_type="video", url=str(video_url)))
        elif image_url:
            media.append(IGMedia(media_type="photo", url=str(image_url)))

        # Carousel/media arrays.
        candidates = raw.get("images") or raw.get("media") or raw.get("carouselMedia") or []
        if isinstance(candidates, list):
            for entry in candidates:
                if isinstance(entry, str):
                    media.append(IGMedia(media_type="photo", url=entry))
                    continue
                if not isinstance(entry, dict):
                    continue
                entry_video = self._pick(entry, ["videoUrl", "video_url"])
                entry_photo = self._pick(entry, ["displayUrl", "imageUrl", "url", "display_url"])
                if entry_video:
                    media.append(IGMedia(media_type="video", url=str(entry_video)))
                elif entry_photo:
                    media.append(IGMedia(media_type="photo", url=str(entry_photo)))

        # Deduplicate by URL preserving order.
        unique: list[IGMedia] = []
        seen: set[str] = set()
        for item in media:
            if item.url in seen:
                continue
            seen.add(item.url)
            unique.append(item)
        return unique

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