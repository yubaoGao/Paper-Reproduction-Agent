import unittest

from backend.app.curie_core import settings


class InternalSchedulerSettingsTests(unittest.TestCase):
    def test_worker_names_are_deterministic(self) -> None:
        self.assertEqual(settings.list_worker_names(), ["worker_0"])
        self.assertEqual(settings.list_control_worker_names(), ["control_worker_0"])
        self.assertIn("supervisor", settings.AGENT_LIST)


if __name__ == "__main__":
    unittest.main()
