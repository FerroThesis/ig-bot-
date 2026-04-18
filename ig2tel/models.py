from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal


MediaType = Literal["photo", "video"]
IGItemType = Literal["post", "reel", "carousel", "story"]


@dataclass(slots=True)
class IGMedia:
    media_type: MediaType
    url: str


@dataclass(slots=True)
class IGPost:
    item_id: str
    username: str
    item_type: IGItemType
    caption: str
    permalink: str
    taken_at: datetime
    media: list[IGMedia]


@dataclass(slots=True)
class IGStory:
    item_id: str
    username: str
    caption: str
    permalink: str
    taken_at: datetime
    media: IGMedia
    expires_at: datetime


@dataclass(slots=True)
class StoryFetchResult:
    items: list[IGStory]
    unavailable_reason: str | None = None


@dataclass(slots=True)
class LinkRecord:
    id: int
    ig_username: str
    chat_id: int
    stories_enabled: bool
    active: bool
    fail_count: int
    next_retry_at: datetime | None
    last_error: str | None
    last_success_at: datetime | None
    last_post_id: str | None
    last_post_timestamp: datetime | None


@dataclass(slots=True)
class LinkOperationResult:
    link: LinkRecord
    created: bool
    reactivated: bool


@dataclass(slots=True)
class AddLinkResult:
    link: LinkRecord
    backfilled_count: int
    warnings: list[str]