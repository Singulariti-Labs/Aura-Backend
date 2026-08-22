import re
import unittest

from app.Promotions.code_security import (
    PromotionConfigurationError,
    generate_promo_code,
    hash_promo_code,
    mask_promo_code,
    normalize_promo_code,
)


class PromoCodeSecurityTests(unittest.TestCase):
    """Ensure promo secrets are generated and looked up safely."""

    def test_normalization_accepts_hyphen_and_space_formatting(self):
        self.assertEqual(
            normalize_promo_code(" pro-abcd efgh-2345 "),
            "PROABCDEFGH2345",
        )

    def test_equivalent_formatting_produces_the_same_hash(self):
        pepper = "p" * 32
        formatted = hash_promo_code("PRO-ABCDE-FGHIJ-23456", pepper=pepper)
        compact = hash_promo_code("proabcdefghij23456", pepper=pepper)
        self.assertEqual(formatted, compact)
        self.assertRegex(formatted, r"^[a-f0-9]{64}$")

    def test_short_pepper_is_rejected(self):
        with self.assertRaises(PromotionConfigurationError):
            hash_promo_code("PRO-ABCDE-FGHIJ-23456", pepper="unsafe")

    def test_generated_code_has_paid_plan_prefix_and_safe_hint(self):
        code = generate_promo_code("max")
        self.assertRegex(code, re.compile(r"^MAX(?:-[A-Z2-9]{5}){5}$"))
        hint = mask_promo_code(code)
        self.assertTrue(hint.startswith("MAX-****-"))
        self.assertNotEqual(hint, code)


if __name__ == "__main__":
    unittest.main()
