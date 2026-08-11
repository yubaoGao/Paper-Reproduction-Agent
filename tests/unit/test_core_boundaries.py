import unittest

from backend.app import curie_core
from backend.app.runtime import ExperimentRuntime, RunEventSink


class CoreBoundaryTests(unittest.TestCase):
    def test_curie_core_import_has_no_runtime_side_effects(self) -> None:
        self.assertEqual(curie_core.__version__, "0.1.0")

    def test_runtime_contracts_are_provider_neutral(self) -> None:
        self.assertIsNotNone(ExperimentRuntime)
        self.assertIsNotNone(RunEventSink)


if __name__ == "__main__":
    unittest.main()
