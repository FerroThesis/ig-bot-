from __future__ import annotations

import logging

from ig2tel.models import IGPost
from ig2tel.providers.instagram.base import ProviderError


class FallbackInstagramPostsProvider:
    def __init__(self, providers: list[object]) -> None:
        if not providers:
            raise ValueError("At least one provider is required")
        self._providers = providers
        self._log = logging.getLogger(__name__)

    def fetch_recent_posts(self, username: str, limit: int = 20) -> list[IGPost]:
        errors: list[str] = []
        for index, provider in enumerate(self._providers):
            try:
                posts = provider.fetch_recent_posts(username, limit=limit)
                if index > 0:
                    self._log.warning(
                        "Used fallback Instagram provider %s for @%s",
                        provider.__class__.__name__,
                        username,
                    )
                return posts
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{provider.__class__.__name__}: {exc}")

        raise ProviderError("All Instagram providers failed: " + " | ".join(errors))

    def provider_names(self) -> list[str]:
        return [provider.__class__.__name__ for provider in self._providers]