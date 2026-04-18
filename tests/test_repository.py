import tempfile
import unittest
from pathlib import Path

from ig2tel.db.repository import Repository


class RepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp_dir.name) / "test.db"
        self.repo = Repository(self.db_path)
        self.repo.init_schema()
        self.repo.seed_admins({1001})

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_admin_lookup(self) -> None:
        self.assertTrue(self.repo.is_admin(1001))
        self.assertFalse(self.repo.is_admin(2002))

    def test_link_lifecycle_and_dedupe_reservation(self) -> None:
        result = self.repo.create_or_reactivate_link("annashumate", -123456, True)
        link = result.link

        self.assertTrue(self.repo.reserve_item(link.id, "post1", "post"))
        self.assertFalse(self.repo.reserve_item(link.id, "post1", "post"))

        self.repo.mark_item_sent(link.id, "post1", "10")

        paused = self.repo.pause_link("annashumate", -123456)
        self.assertTrue(paused)

        resumed = self.repo.resume_link("annashumate", -123456)
        self.assertTrue(resumed)

        removed = self.repo.remove_link("annashumate", -123456)
        self.assertTrue(removed)


if __name__ == "__main__":
    unittest.main()