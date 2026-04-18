from __future__ import annotations

from ig2tel.models import StoryFetchResult
from ig2tel.providers.instagram.base import InstagramStoryProvider


class StoryBestEffortProvider:
    """Wraps a story provider and turns hard failures into soft 'unavailable' responses."""

    def __init__(self, inner: InstagramStoryProvider) -> None:
        self._inner = inner

    def fetch_recent_stories(self, username: str) -> StoryFetchResult:
        try:
            result = self._inner.fetch_recent_stories(username)
            if result.unavailable_reason:
                return StoryFetchResult(items=[], unavailable_reason=result.unavailable_reason)
            return result
        except Exception as exc:  # noqa: BLE001
            return StoryFetchResult(items=[], unavailable_reason=str(exc))