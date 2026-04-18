from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from ig2tel.models import IGMedia, IGPost, IGStory, StoryFetchResult
from ig2tel.providers.instagram.base import ProviderError, ProviderUnavailableError


class InstaloaderProvider:
    def __init__(self) -> None:
        try:
            import instaloader  # type: ignore
        except ImportError as exc:
            raise ProviderUnavailableError(
                "instaloader is not installed. Install dependencies from requirements.txt"
            ) from exc

        self._instaloader_module = instaloader
        # Important: disable Instaloader's long internal waiting on 429.
        # The scheduler/repository retry logic handles backoff instead.
        self._loader = instaloader.Instaloader(
            download_pictures=False,
            download_videos=False,
            download_video_thumbnails=False,
            save_metadata=False,
            compress_json=False,
            quiet=True,
            sleep=False,
            max_connection_attempts=1,
            request_timeout=20,
        )

    def fetch_recent_posts(self, username: str, limit: int = 20) -> list[IGPost]:
        profile = self._get_profile(username)
        posts: list[IGPost] = []

        try:
            for idx, post in enumerate(profile.get_posts()):
                if idx >= limit:
                    break
                posts.append(self._map_post(username, post))
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(self._normalize_provider_error(f"Failed to fetch posts for @{username}", exc)) from exc

        return posts

    def fetch_recent_stories(self, username: str) -> StoryFetchResult:
        profile = self._get_profile(username)

        try:
            stories_iter = self._loader.get_stories(userids=[profile.userid])
            stories: list[IGStory] = []
            for story in stories_iter:
                for item in story.get_items():
                    media = self._story_media(item)
                    if media is None:
                        continue
                    taken_at = self._to_utc(item.date_utc)
                    stories.append(
                        IGStory(
                            item_id=str(item.mediaid),
                            username=username,
                            caption=getattr(item, "caption", "") or "",
                            permalink=f"https://www.instagram.com/stories/{username}/{item.mediaid}/",
                            taken_at=taken_at,
                            media=media,
                            expires_at=taken_at + timedelta(hours=24),
                        )
                    )
            return StoryFetchResult(items=stories)
        except Exception as exc:  # noqa: BLE001
            return StoryFetchResult(items=[], unavailable_reason=self._normalize_provider_error("Stories unavailable", exc))

    def _get_profile(self, username: str) -> Any:
        try:
            return self._instaloader_module.Profile.from_username(self._loader.context, username)
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(
                self._normalize_provider_error(f"Failed to resolve Instagram profile @{username}", exc)
            ) from exc

    def _map_post(self, username: str, post: Any) -> IGPost:
        media = self._post_media(post)
        if not media:
            raise ProviderError(f"No media found for Instagram post {post.shortcode}")

        typename = getattr(post, "typename", "")
        item_type = "post"
        if typename == "GraphVideo":
            item_type = "reel"
        elif typename == "GraphSidecar":
            item_type = "carousel"

        return IGPost(
            item_id=str(post.mediaid),
            username=username,
            item_type=item_type,
            caption=(post.caption or "").strip(),
            permalink=f"https://www.instagram.com/p/{post.shortcode}/",
            taken_at=self._to_utc(post.date_utc),
            media=media,
        )

    def _post_media(self, post: Any) -> list[IGMedia]:
        typename = getattr(post, "typename", "")
        if typename == "GraphImage":
            return [IGMedia(media_type="photo", url=getattr(post, "url"))]
        if typename == "GraphVideo":
            video_url = getattr(post, "video_url", None)
            if video_url:
                return [IGMedia(media_type="video", url=video_url)]
            return [IGMedia(media_type="photo", url=getattr(post, "url"))]

        if typename == "GraphSidecar":
            entries: list[IGMedia] = []
            for node in post.get_sidecar_nodes():
                if getattr(node, "is_video", False):
                    node_url = getattr(node, "video_url", None)
                    if node_url:
                        entries.append(IGMedia(media_type="video", url=node_url))
                else:
                    entries.append(IGMedia(media_type="photo", url=getattr(node, "display_url")))
            return entries

        url = getattr(post, "url", None)
        if url:
            return [IGMedia(media_type="photo", url=url)]
        return []

    def _story_media(self, item: Any) -> IGMedia | None:
        if getattr(item, "is_video", False):
            url = getattr(item, "video_url", None)
            if url:
                return IGMedia(media_type="video", url=url)
            return None

        image_url = getattr(item, "url", None)
        if image_url:
            return IGMedia(media_type="photo", url=image_url)
        return None

    @staticmethod
    def _to_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    @staticmethod
    def _normalize_provider_error(prefix: str, exc: Exception) -> str:
        text = str(exc)
        if "429" in text or "Too Many Requests" in text:
            return f"{prefix}: Instagram rate-limited this VPS IP (429)"
        return f"{prefix}: {text}"