import os
import unittest
from unittest.mock import patch

from app.GraphMemory.config import MemoryConfigurationError, MemorySettings


class MemoryConfigurationTests(unittest.TestCase):
    def test_accepts_gemini_alias_and_uses_google_default_model(self):
        with patch.dict(
            os.environ,
            {"MEMORY_LLM_PROVIDER": "gemini"},
            clear=False,
        ):
            with patch.dict(
                os.environ,
                {"MEMORY_LLM_MODEL": "gemini-3-flash-preview"},
                clear=False,
            ):
                settings = MemorySettings.from_env()

        self.assertEqual(settings.llm_config.provider, "google")
        self.assertEqual(settings.llm_config.model_name, "gemini-3-flash-preview")

    def test_rejects_provider_outside_anthropic_and_gemini(self):
        with patch.dict(
            os.environ,
            {"MEMORY_LLM_PROVIDER": "openai"},
            clear=False,
        ):
            with self.assertRaisesRegex(
                MemoryConfigurationError,
                "either 'anthropic' or 'google'",
            ):
                MemorySettings.from_env()


if __name__ == "__main__":
    unittest.main()
