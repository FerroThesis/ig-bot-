from __future__ import annotations

import logging
from typing import Callable

from ig2tel.bot.telegram_api import TelegramApiClient, TelegramApiError
from ig2tel.core.link_service import LinkService
from ig2tel.db.repository import Repository


def parse_addlink_args(args: list[str]) -> tuple[str, int, bool]:
    if len(args) < 2:
        raise ValueError("Usage: /addlink <ig_username> <chat_id> [--stories]")

    stories_enabled = False
    filtered: list[str] = []
    for token in args:
        if token == "--stories":
            stories_enabled = True
        else:
            filtered.append(token)

    if len(filtered) != 2:
        raise ValueError("Usage: /addlink <ig_username> <chat_id> [--stories]")

    username = filtered[0]
    chat_id = _parse_chat_id(filtered[1])
    return username, chat_id, stories_enabled


def parse_link_args(args: list[str], command: str) -> tuple[str, int]:
    if len(args) != 2:
        raise ValueError(f"Usage: /{command} <ig_username> <chat_id>")
    return args[0], _parse_chat_id(args[1])


def _parse_chat_id(raw: str) -> int:
    try:
        chat_id = int(raw)
    except ValueError as exc:
        raise ValueError("Chat ID must be an integer") from exc

    if chat_id == 0:
        raise ValueError("Chat ID cannot be 0")
    return chat_id


class TelegramCommandHandler:
    def __init__(
        self,
        api_client: TelegramApiClient,
        link_service: LinkService,
        repository: Repository,
    ) -> None:
        self._api = api_client
        self._service = link_service
        self._repo = repository
        self._log = logging.getLogger(__name__)

        self._handlers: dict[str, Callable[[int, list[str]], str]] = {
            "addlink": self._handle_addlink,
            "removelink": self._handle_removelink,
            "pause": self._handle_pause,
            "resume": self._handle_resume,
            "listlinks": self._handle_listlinks,
            "help": self._handle_help,
        }

    def handle_update(self, update: dict) -> None:
        message = update.get("message")
        if not message:
            return

        text = (message.get("text") or "").strip()
        if not text.startswith("/"):
            return

        chat_id = int(message["chat"]["id"])
        reply_to_id = int(message["message_id"])
        user_id = int(message.get("from", {}).get("id", 0))

        command, args = self._parse_command(text)
        if command not in self._handlers:
            self._safe_reply(chat_id, "Unknown command. Use /help.", reply_to_id)
            return

        if command != "help" and not self._repo.is_admin(user_id):
            self._safe_reply(chat_id, "You are not authorized to manage links.", reply_to_id)
            return

        try:
            response = self._handlers[command](user_id, args)
        except Exception as exc:  # noqa: BLE001
            self._log.exception("Command execution failed")
            response = f"Error: {exc}"

        self._safe_reply(chat_id, response, reply_to_id)

    def _handle_addlink(self, user_id: int, args: list[str]) -> str:
        username, chat_id, stories_enabled = parse_addlink_args(args)
        result = self._service.add_link(username, chat_id, stories_enabled)
        details = [
            f"Link configured: @{result.link.ig_username} -> {result.link.chat_id}",
            f"Stories: {'on' if result.link.stories_enabled else 'off'}",
            f"Backfilled posts: {result.backfilled_count}",
        ]
        details.extend(result.warnings)
        return "\n".join(details)

    def _handle_removelink(self, user_id: int, args: list[str]) -> str:
        username, chat_id = parse_link_args(args, "removelink")
        removed = self._service.remove_link(username, chat_id)
        if not removed:
            return "Link not found."
        return f"Removed link: @{username} -> {chat_id}"

    def _handle_pause(self, user_id: int, args: list[str]) -> str:
        username, chat_id = parse_link_args(args, "pause")
        paused = self._service.pause_link(username, chat_id)
        if not paused:
            return "Link not found."
        return f"Paused link: @{username} -> {chat_id}"

    def _handle_resume(self, user_id: int, args: list[str]) -> str:
        username, chat_id = parse_link_args(args, "resume")
        resumed = self._service.resume_link(username, chat_id)
        if not resumed:
            return "Link not found."
        return f"Resumed link: @{username} -> {chat_id}"

    def _handle_listlinks(self, user_id: int, args: list[str]) -> str:
        links = self._service.list_links()
        if not links:
            return "No links configured."

        lines: list[str] = []
        for link in links:
            mode = "active" if link.active else "paused"
            stories = "stories:on" if link.stories_enabled else "stories:off"
            last_success = link.last_success_at.isoformat() if link.last_success_at else "never"
            retry_at = link.next_retry_at.isoformat() if link.next_retry_at else "-"
            error = link.last_error if link.last_error else "-"
            lines.append(
                f"@{link.ig_username} -> {link.chat_id} [{mode}, {stories}] "
                f"fail_count={link.fail_count} last_success={last_success} retry_at={retry_at} error={error}"
            )
        return "\n".join(lines)

    def _handle_help(self, user_id: int, args: list[str]) -> str:
        return (
            "Commands:\n"
            "/addlink <ig_username> <chat_id> [--stories]\n"
            "/removelink <ig_username> <chat_id>\n"
            "/listlinks\n"
            "/pause <ig_username> <chat_id>\n"
            "/resume <ig_username> <chat_id>\n"
            "/help"
        )

    def _parse_command(self, text: str) -> tuple[str, list[str]]:
        parts = text.split()
        raw_command = parts[0][1:]
        command = raw_command.split("@", 1)[0].lower()
        return command, parts[1:]

    def _safe_reply(self, chat_id: int, text: str, reply_to_message_id: int) -> None:
        try:
            self._api.send_message(chat_id, text, reply_to_message_id=reply_to_message_id)
        except TelegramApiError:
            self._log.exception("Failed sending command response to chat_id=%s", chat_id)