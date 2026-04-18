from __future__ import annotations

import logging
import random
import threading
import time

from ig2tel.core.link_service import LinkService


class PollScheduler:
    def __init__(
        self,
        link_service: LinkService,
        poll_interval_seconds: int,
        jitter_seconds: int,
    ) -> None:
        self._service = link_service
        self._poll_interval_seconds = poll_interval_seconds
        self._jitter_seconds = jitter_seconds
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._run, name="poll-scheduler", daemon=True)
        self._log = logging.getLogger(__name__)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._thread.join(timeout=10)

    def _run(self) -> None:
        while not self._stop_event.is_set():
            start_time = time.monotonic()
            try:
                self._service.run_sync_cycle()
            except Exception:  # noqa: BLE001
                self._log.exception("Sync cycle failed")

            elapsed = time.monotonic() - start_time
            jitter = random.randint(0, max(self._jitter_seconds, 0))
            sleep_for = max(self._poll_interval_seconds - elapsed + jitter, 1)
            self._stop_event.wait(timeout=sleep_for)