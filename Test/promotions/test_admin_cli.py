import unittest

from app.Promotions.admin_cli import build_parser


class PromotionAdminCliTests(unittest.TestCase):
    def test_custom_code_is_optional(self):
        parser = build_parser()

        generated = parser.parse_args(["--plan", "pro"])
        custom = parser.parse_args(
            ["--plan", "max", "--code", "AURA-FOUNDERS-2026-ACCESS"]
        )

        self.assertIsNone(generated.code)
        self.assertEqual(custom.code, "AURA-FOUNDERS-2026-ACCESS")


if __name__ == "__main__":
    unittest.main()
