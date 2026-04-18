import unittest

from ig2tel.bot.telegram_handlers import parse_addlink_args


class ParseAddLinkArgsTests(unittest.TestCase):
    def test_parse_without_stories(self) -> None:
        username, chat_id, stories_enabled = parse_addlink_args(["annashumate", "-123456"])
        self.assertEqual(username, "annashumate")
        self.assertEqual(chat_id, -123456)
        self.assertFalse(stories_enabled)

    def test_parse_with_stories_flag(self) -> None:
        username, chat_id, stories_enabled = parse_addlink_args([
            "annashumate",
            "-123456",
            "--stories",
        ])
        self.assertEqual(username, "annashumate")
        self.assertEqual(chat_id, -123456)
        self.assertTrue(stories_enabled)


if __name__ == "__main__":
    unittest.main()