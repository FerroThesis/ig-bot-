import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ig2tel.core.link_service import LinkService
from ig2tel.db.repository import Repository
from ig2tel.models import IGMedia, IGPost, StoryFetchResult


class FakePostProvider:
    def __init__(self, posts):
        self._posts = posts

    def fetch_recent_posts(self, username: str, limit: int = 20):
        return list(self._posts)[:limit]


class FakeStoryProvider:
    def fetch_recent_stories(self, username: str):
        return StoryFetchResult(items=[])


class FakeDeliveryWorker:
    def __init__(self):
        self.sent_posts = []
        self._next_message_id = 1

    def deliver_post(self, chat_id: int, post: IGPost):
        self.sent_posts.append((chat_id, post.item_id))
        message_id = self._next_message_id
        self._next_message_id += 1
        return [message_id]

    def deliver_story(self, chat_id, story):
        message_id = self._next_message_id
        self._next_message_id += 1
        return message_id


class LinkServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(self.temp_dir.name) / "svc.db"
        self.repo = Repository(db_path)
        self.repo.init_schema()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_addlink_backfills_last_three_in_chronological_order(self) -> None:
        base = datetime(2026, 1, 1, tzinfo=UTC)
        posts = [
            IGPost(
                item_id=f"id{i}",
                username="annashumate",
                item_type="post",
                caption="",
                permalink=f"https://instagram.com/p/{i}",
                taken_at=base + timedelta(minutes=i),
                media=[IGMedia(media_type="photo", url="https://example.com/a.jpg")],
            )
            for i in range(5, 0, -1)
        ]

        provider = FakePostProvider(posts)
        delivery = FakeDeliveryWorker()

        service = LinkService(
            repository=self.repo,
            post_provider=provider,
            story_provider=FakeStoryProvider(),
            delivery_worker=delivery,
            max_fetch_items=20,
        )

        result = service.add_link("annashumate", -123456, stories_enabled=False)

        self.assertEqual(result.backfilled_count, 3)
        self.assertEqual(
            delivery.sent_posts,
            [(-123456, "id3"), (-123456, "id4"), (-123456, "id5")],
        )


if __name__ == "__main__":
    unittest.main()