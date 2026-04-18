from __future__ import annotations

import logging
import re
from collections import defaultdict
from datetime import UTC, datetime, timedelta

from ig2tel.core.delivery_worker import DeliveryWorker
from ig2tel.db.repository import Repository
from ig2tel.models import AddLinkResult, IGPost, LinkRecord
from ig2tel.providers.instagram.base import InstagramPostProvider, InstagramStoryProvider, ProviderError

USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9._]{1,30}$")
RETRY_STEPS_SECONDS = [60, 180, 600, 1800, 3600]


class LinkService:
    def __init__(
        self,
        repository: Repository,
        post_provider: InstagramPostProvider,
        story_provider: InstagramStoryProvider,
        delivery_worker: DeliveryWorker,
        max_fetch_items: int,
    ) -> None:
        self._repo = repository
        self._post_provider = post_provider
        self._story_provider = story_provider
        self._delivery = delivery_worker
        self._max_fetch_items = max_fetch_items
        self._log = logging.getLogger(__name__)

    def add_link(self, ig_username: str, chat_id: int, stories_enabled: bool) -> AddLinkResult:
        username = self._normalize_username(ig_username)
        self._validate_chat_id(chat_id)

        operation = self._repo.create_or_reactivate_link(username, chat_id, stories_enabled)
        warnings: list[str] = []

        backfilled = 0
        try:
            posts = self._post_provider.fetch_recent_posts(username, limit=3)
            backfilled = self._deliver_posts_for_link(operation.link, posts, stop_on_error=False)
            newest = posts[0] if posts else None
            self._repo.mark_link_success(
                operation.link.id,
                newest.item_id if newest else None,
                newest.taken_at if newest else None,
            )
        except Exception as exc:  # noqa: BLE001
            warning = f"Link created but initial backfill failed: {exc}"
            warnings.append(warning)
            self._set_link_retry(operation.link, warning)

        return AddLinkResult(link=operation.link, backfilled_count=backfilled, warnings=warnings)

    def remove_link(self, ig_username: str, chat_id: int) -> bool:
        username = self._normalize_username(ig_username)
        self._validate_chat_id(chat_id)
        return self._repo.remove_link(username, chat_id)

    def pause_link(self, ig_username: str, chat_id: int) -> bool:
        username = self._normalize_username(ig_username)
        self._validate_chat_id(chat_id)
        return self._repo.pause_link(username, chat_id)

    def resume_link(self, ig_username: str, chat_id: int) -> bool:
        username = self._normalize_username(ig_username)
        self._validate_chat_id(chat_id)
        return self._repo.resume_link(username, chat_id)

    def list_links(self) -> list[LinkRecord]:
        return self._repo.list_links()

    def run_sync_cycle(self) -> None:
        now = datetime.now(tz=UTC)
        self._repo.purge_expired_stories(now)
        links = self._repo.get_active_links(now)
        if not links:
            return

        grouped: dict[str, list[LinkRecord]] = defaultdict(list)
        for link in links:
            grouped[link.ig_username].append(link)

        for username, username_links in grouped.items():
            posts: list[IGPost]
            try:
                posts = self._post_provider.fetch_recent_posts(username, limit=self._max_fetch_items)
            except ProviderError as exc:
                message = f"Instagram fetch failed: {exc}"
                for link in username_links:
                    self._set_link_retry(link, message)
                continue
            except Exception as exc:  # noqa: BLE001
                message = f"Instagram fetch failed: {exc}"
                for link in username_links:
                    self._set_link_retry(link, message)
                continue

            newest_post = posts[0] if posts else None

            for link in username_links:
                delivery_failed = False
                try:
                    self._deliver_posts_for_link(link, posts, stop_on_error=True)
                except Exception as exc:  # noqa: BLE001
                    delivery_failed = True
                    self._set_link_retry(link, f"Delivery error: {exc}")

                if not delivery_failed:
                    self._repo.mark_link_success(
                        link.id,
                        newest_post.item_id if newest_post else None,
                        newest_post.taken_at if newest_post else None,
                    )

                if link.stories_enabled:
                    self._sync_stories(link, username)

    def _deliver_posts_for_link(
        self,
        link: LinkRecord,
        posts: list[IGPost],
        stop_on_error: bool,
    ) -> int:
        sent_count = 0
        for post in sorted(posts, key=lambda entry: entry.taken_at):
            reserved = self._repo.reserve_item(link.id, post.item_id, post.item_type)
            if not reserved:
                continue
            try:
                message_ids = self._delivery.deliver_post(link.chat_id, post)
                self._repo.mark_item_sent(link.id, post.item_id, ",".join(str(mid) for mid in message_ids))
                sent_count += 1
            except Exception:
                self._repo.release_item(link.id, post.item_id)
                if stop_on_error:
                    raise
                self._log.exception(
                    "Post delivery failed for link_id=%s ig_item_id=%s",
                    link.id,
                    post.item_id,
                )
        return sent_count

    def _sync_stories(self, link: LinkRecord, username: str) -> None:
        result = self._story_provider.fetch_recent_stories(username)
        if result.unavailable_reason:
            self._repo.mark_story_warning(link.id, f"Stories unavailable: {result.unavailable_reason}")
            return

        stories = sorted(result.items, key=lambda item: item.taken_at)
        for story in stories:
            story_key = f"story:{story.item_id}"
            reserved = self._repo.reserve_item(link.id, story_key, "story")
            if not reserved:
                continue

            try:
                message_id = self._delivery.deliver_story(link.chat_id, story)
                self._repo.mark_seen_story(link.id, story.item_id, story.expires_at)
                self._repo.mark_item_sent(link.id, story_key, str(message_id))
            except Exception as exc:  # noqa: BLE001
                self._repo.release_item(link.id, story_key)
                self._repo.mark_story_warning(link.id, f"Story delivery failed: {exc}")
                break

        self._repo.mark_story_check_success(link.id)

    def _set_link_retry(self, link: LinkRecord, message: str) -> None:
        fail_index = min(link.fail_count, len(RETRY_STEPS_SECONDS) - 1)
        backoff_seconds = RETRY_STEPS_SECONDS[fail_index]
        retry_at = datetime.now(tz=UTC) + timedelta(seconds=backoff_seconds)
        self._repo.mark_link_failure(link.id, message, retry_at)

    def _normalize_username(self, username: str) -> str:
        normalized = username.strip().lstrip("@").lower()
        if not USERNAME_PATTERN.fullmatch(normalized):
            raise ValueError("Invalid Instagram username")
        return normalized

    def _validate_chat_id(self, chat_id: int) -> None:
        if chat_id == 0:
            raise ValueError("Invalid chat ID")