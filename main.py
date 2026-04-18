from __future__ import annotations

import logging
import signal
import time

from ig2tel.bot.telegram_api import TelegramApiClient, TelegramApiError
from ig2tel.bot.telegram_handlers import TelegramCommandHandler
from ig2tel.config import Settings
from ig2tel.core.delivery_worker import DeliveryWorker
from ig2tel.core.link_service import LinkService
from ig2tel.core.scheduler import PollScheduler
from ig2tel.db.repository import Repository
from ig2tel.logging_utils import configure_logging
from ig2tel.providers.instagram.apify_provider import ApifyInstagramPostsProvider
from ig2tel.providers.instagram.base import ProviderUnavailableError
from ig2tel.providers.instagram.fallback_provider import FallbackInstagramPostsProvider
from ig2tel.providers.instagram.gallerydl_provider import GalleryDlInstagramProvider
from ig2tel.providers.instagram.instaloader_provider import InstaloaderProvider
from ig2tel.providers.instagram.story_best_effort_provider import StoryBestEffortProvider


def main() -> None:
    try:
        from dotenv import load_dotenv  # type: ignore

        load_dotenv()
    except Exception:  # noqa: BLE001
        pass

    settings = Settings.from_env()
    settings.tmp_dir.mkdir(parents=True, exist_ok=True)

    configure_logging(settings.log_level)
    log = logging.getLogger(__name__)

    repository = Repository(settings.db_path)
    repository.init_schema()
    repository.seed_admins(settings.admin_user_ids)

    instaloader_provider = InstaloaderProvider()

    post_providers: list[object] = [instaloader_provider]

    if settings.gallery_dl_enabled:
        try:
            gallery_provider = GalleryDlInstagramProvider(
                command=settings.gallery_dl_path,
                timeout_seconds=settings.gallery_dl_timeout_seconds,
            )
            post_providers.append(gallery_provider)
        except ProviderUnavailableError as exc:
            log.warning("gallery-dl fallback disabled: %s", exc)

    if settings.apify_token:
        apify_provider = ApifyInstagramPostsProvider(
            token=settings.apify_token,
            actor_id=settings.apify_actor_id,
            timeout_seconds=settings.apify_timeout_seconds,
        )
        post_providers.append(apify_provider)

    if len(post_providers) == 1:
        post_provider = post_providers[0]
    else:
        fallback_chain = FallbackInstagramPostsProvider(post_providers)
        post_provider = fallback_chain
        log.info("Instagram provider chain: %s", " -> ".join(fallback_chain.provider_names()))

    story_provider = StoryBestEffortProvider(instaloader_provider)

    telegram = TelegramApiClient(
        token=settings.telegram_bot_token,
        api_base=settings.telegram_api_base,
        timeout_seconds=settings.request_timeout_seconds,
    )

    delivery = DeliveryWorker(
        telegram_client=telegram,
        tmp_dir=settings.tmp_dir,
    )

    link_service = LinkService(
        repository=repository,
        post_provider=post_provider,
        story_provider=story_provider,
        delivery_worker=delivery,
        max_fetch_items=settings.max_fetch_items,
    )

    scheduler = PollScheduler(
        link_service=link_service,
        poll_interval_seconds=settings.poll_interval_seconds,
        jitter_seconds=settings.scheduler_jitter_seconds,
    )

    handlers = TelegramCommandHandler(
        api_client=telegram,
        link_service=link_service,
        repository=repository,
    )

    stop_requested = False

    def _request_stop(signum, frame):  # type: ignore[no-untyped-def]
        nonlocal stop_requested
        stop_requested = True

    signal.signal(signal.SIGINT, _request_stop)
    signal.signal(signal.SIGTERM, _request_stop)

    scheduler.start()
    log.info("Bot started. Poll interval=%ss", settings.poll_interval_seconds)

    offset: int | None = None
    while not stop_requested:
        try:
            updates = telegram.get_updates(offset=offset, timeout=25)
            for update in updates:
                handlers.handle_update(update)
                offset = int(update["update_id"]) + 1
        except TelegramApiError as exc:
            log.warning("Telegram polling error: %s", exc)
            time.sleep(2)
        except Exception:  # noqa: BLE001
            log.exception("Unexpected polling error")
            time.sleep(2)

    scheduler.stop()
    log.info("Bot stopped")


if __name__ == "__main__":
    main()