import unittest

from app.Types.agent_types import AuraConfig, OpenApplications


class OpenApplicationsTests(unittest.TestCase):
    """Protect valid client states where no applications were discovered."""

    def test_empty_active_apps_and_null_focused_app_are_allowed(self):
        open_apps = OpenApplications(
            active_apps=[],
            focused_app=None,
        )

        self.assertEqual(open_apps.active_apps, [])
        self.assertIsNone(open_apps.focused_app)

    def test_empty_open_apps_payload_is_accepted_by_aura_config(self):
        config = AuraConfig(
            open_apps={
                "active_apps": [],
                "focused_app": None,
            }
        )

        self.assertIsNotNone(config.open_apps)
        self.assertEqual(config.open_apps.active_apps, [])
        self.assertIsNone(config.open_apps.focused_app)


if __name__ == "__main__":
    unittest.main()
