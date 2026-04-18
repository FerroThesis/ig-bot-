from __future__ import annotations

import logging
import time
from pathlib import Path

import requests

from ig2tel.bot.telegram_api import TelegramApiClient, TelegramApiError, TelegramRateLimitError
from ig2tel.models import IGPost, IGStory


class DeliveryWorker:
    def __init__(
        self,
        telegram_client: TelegramApiClient,
        tmp_dir: Path,
    ) -> None:
        self._client = telegram_client
        self._tmp_dir = tmp_dir
        self._tmp_dir.mkdir(parents=True, exist_ok=True)
        self._log = logging.getLogger(__name__)

    def deliver_post(self, chat_id: int, post: IGPost) -> list[int]:
        local_files: list[tuple[str, Path]] = []
        try:
            for index, media in enumerate(post.media):
                local_files.append((media.media_type, self._download(media.url, post.item_id, index)))

            caption = self._post_caption(post)

            if len(local_files) == 1:
                media_type, file_path = local_files[0]
                if media_type == "video":
                    result = self._with_retry(lambda: self._client.send_video(chat_id, file_path, caption))
                else:
                    result = self._with_retry(lambda: self._client.send_photo(chat_id, file_path, caption))
                return [int(result["message_id"])]

            message_ids: list[int] = []
            chunk_size = 10
            for start in range(0, len(local_files), chunk_size):
                chunk = local_files[start : start + chunk_size]
                chunk_caption = caption if start == 0 else ""
                payload = [("video" if kind == "video" else "photo", path) for kind, path in chunk]
                results = self._with_retry(lambda: self._client.send_media_group(chat_id, payload, chunk_caption))
                message_ids.extend(int(entry["message_id"]) for entry in results)
            return message_ids
        finally:
            for _, file_path in local_files:
                file_path.unlink(missing_ok=True)

    def deliver_story(self, chat_id: int, story: IGStory) -> int:
        local_path = self._download(story.media.url, story.item_id, 0)
        try:
            caption = self._story_caption(story)
            if story.media.media_type == "video":
                result = self._with_retry(lambda: self._client.send_video(chat_id, local_path, caption))
            else:
                result = self._with_retry(lambda: self._client.send_photo(chat_id, local_path, caption))
            return int(result["message_id"])
        finally:
            local_path.unlink(missing_ok=True)

    def _download(self, url: str, item_id: str, index: int) -> Path:
        response = requests.get(url, stream=True, timeout=45)
        response.raise_for_status()
        suffix = ".bin"
        content_type = response.headers.get("Content-Type", "").lower()
        if "image/" in content_type:
            suffix = ".jpg"
        elif "video/" in content_type:
            suffix = ".mp4"

        target = self._tmp_dir / f"{item_id}_{index}{suffix}"
        with target.open("wb") as stream:
            for chunk in response.iter_content(chunk_size=1024 * 256):
                if not chunk:
                    continue
                stream.write(chunk)
        return target

    def _with_retry(self, func):
        attempts = 0
        while True:
            attempts += 1
            try:
                return func()
            except TelegramRateLimitError as exc:
                if attempts >= 4:
                    raise
                wait_seconds = max(exc.retry_after, 1)
                self._log.warning("Telegram rate limit hit, retrying in %ss", wait_seconds)
                time.sleep(wait_seconds)
            except TelegramApiError:
                if attempts >= 2:
                    raise
                time.sleep(1)

    @staticmethod
    def _truncate(text: str, limit: int) -> str:
        if len(text) <= limit:
            return text
        return text[: limit - 3] + "..."

    def _post_caption(self, post: IGPost) -> str:
        base = f"@{post.username}\n{post.permalink}"
        if not post.caption:
            return base
        caption = self._truncate(post.caption, 850)
        return f"{base}\n\n{caption}"

    def _story_caption(self, story: IGStory) -> str:
        base = f"Story from @{story.username}\n{story.permalink}"
        if not story.caption:
            return base
        caption = self._truncate(story.caption, 850)
        return f"{base}\n\n{caption}"