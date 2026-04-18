from __future__ import annotations

from typing import Protocol

from ig2tel.models import IGPost, StoryFetchResult


class ProviderError(Exception):
    """Raised when a provider call fails."""


class ProviderUnavailableError(ProviderError):
    """Raised when provider dependencies are missing."""


class InstagramPostProvider(Protocol):
    def fetch_recent_posts(self, username: str, limit: int = 20) -> list[IGPost]:
        ...


class InstagramStoryProvider(Protocol):
    def fetch_recent_stories(self, username: str) -> StoryFetchResult:
        ...