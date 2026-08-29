import unittest

from app.api.websocket_utils import normalize_compression_id


class CompressionWebSocketContractTests(unittest.TestCase):
    def test_normalizes_compression_uuid(self):
        self.assertEqual(
            normalize_compression_id(
                "compression_7B23EC29-9D65-47C6-8F8B-329F10EC8524"
            ),
            "compression_7b23ec29-9d65-47c6-8f8b-329f10ec8524",
        )

    def test_rejects_invalid_compression_id(self):
        for value in ("", "cmp_123", "compression_not-a-uuid", 123):
            with self.subTest(value=value):
                with self.assertRaisesRegex(
                    ValueError,
                    "compression_id",
                ):
                    normalize_compression_id(value)


if __name__ == "__main__":
    unittest.main()
