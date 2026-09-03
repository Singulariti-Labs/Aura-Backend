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

    def test_dynamic_application_objects_are_allowed(self):
        application = {
            "name": "chrome",
            "title": "Aura Documentation - Google Chrome",
            "exe_path": r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            "pid": 1234,
            "hwnd": 567890,
            "is_foreground": True,
            "is_minimized": False,
            "get_info_type": "browser",
            "adapter_specific_field": {"any": "value"},
        }

        open_apps = OpenApplications(
            active_apps=[application],
            focused_app=application,
        )

        self.assertEqual(open_apps.active_apps, [application])
        self.assertEqual(open_apps.focused_app, application)


if __name__ == "__main__":
    unittest.main()
