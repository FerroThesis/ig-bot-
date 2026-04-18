from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests


@dataclass(slots=True)
class TelegramApiError(Exception):
    message: str

    def __str__(self) -> str:
        return self.message


@dataclass(slots=True)
class TelegramRateLimitError(TelegramApiError):
    retry_after: int


class TelegramApiClient:
    def __init__(self, token: str, api_base: str, timeout_seconds: int = 30) -> None:
        self._base_url = f"{api_base}/bot{token}"
        self._timeout_seconds = timeout_seconds
        self._session = requests.Session()

    def get_updates(self, offset: int | None, timeout: int = 25) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {
            "timeout": timeout,
            "allowed_updates": json.dumps(["message"]),
        }
        if offset is not None:
            payload["offset"] = offset

        data = self._request("getUpdates", data=payload)
        return data

    def send_message(
        self,
        chat_id: int,
        text: str,
        reply_to_message_id: int | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "chat_id": str(chat_id),
            "text": text,
            "disable_web_page_preview": True,
        }
        if reply_to_message_id is not None:
            payload["reply_to_message_id"] = str(reply_to_message_id)

        return self._request("sendMessage", data=payload)

    def send_photo(self, chat_id: int, photo_path: Path, caption: str = "") -> dict[str, Any]:
        with photo_path.open("rb") as stream:
            files = {"photo": (photo_path.name, stream, "application/octet-stream")}
            payload = {"chat_id": str(chat_id), "caption": caption}
            return self._request("sendPhoto", data=payload, files=files)

    def send_video(self, chat_id: int, video_path: Path, caption: str = "") -> dict[str, Any]:
        with video_path.open("rb") as stream:
            files = {"video": (video_path.name, stream, "application/octet-stream")}
            payload = {"chat_id": str(chat_id), "caption": caption}
            return self._request("sendVideo", data=payload, files=files)

    def send_media_group(
        self,
        chat_id: int,
        media_entries: list[tuple[str, Path]],
        caption: str = "",
    ) -> list[dict[str, Any]]:
        files: dict[str, Any] = {}
        streams = []
        media_payload = []
        try:
            for idx, (kind, media_path) in enumerate(media_entries):
                attach_name = f"file{idx}"
                stream = media_path.open("rb")
                streams.append(stream)
                files[attach_name] = (media_path.name, stream, "application/octet-stream")
                payload: dict[str, Any] = {
                    "type": kind,
                    "media": f"attach://{attach_name}",
                }
                if idx == 0 and caption:
                    payload["caption"] = caption
                media_payload.append(payload)

            data = {
                "chat_id": str(chat_id),
                "media": json.dumps(media_payload),
            }
            result = self._request("sendMediaGroup", data=data, files=files)
            return list(result)
        finally:
            for stream in streams:
                stream.close()

    def _request(
        self,
        method: str,
        data: dict[str, Any] | None = None,
        files: dict[str, Any] | None = None,
    ) -> Any:
        response = self._session.post(
            f"{self._base_url}/{method}",
            data=data,
            files=files,
            timeout=self._timeout_seconds,
        )
        try:
            payload = response.json()
        except ValueError as exc:
            raise TelegramApiError(f"Telegram API returned non-JSON response for {method}") from exc

        if response.status_code == 429:
            params = payload.get("parameters") or {}
            retry_after = int(params.get("retry_after", 1))
            description = payload.get("description", "Rate limited")
            raise TelegramRateLimitError(description, retry_after=retry_after)

        if not payload.get("ok"):
            description = payload.get("description", "Unknown Telegram API error")
            raise TelegramApiError(description)

        return payload.get("result")