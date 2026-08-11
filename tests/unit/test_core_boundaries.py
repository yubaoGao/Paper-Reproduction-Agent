import unittest
from pathlib import Path

from backend.app import curie_core
from backend.app.runtime import ExperimentRuntime, RuntimeRequest, RuntimeResult


class CoreBoundaryTests(unittest.TestCase):
    def test_curie_core_import_has_no_runtime_side_effects(self) -> None:
        self.assertEqual(curie_core.__version__, "0.1.0")

    def test_runtime_contract_is_provider_neutral(self) -> None:
        request = RuntimeRequest(
            run_id="run-1",
            workspace=Path("workspace/run-1"),
            specification_ref="spec-1",
        )
        result = RuntimeResult(run_id=request.run_id, exit_code=0)

        self.assertEqual(result.run_id, "run-1")
        self.assertEqual(result.artifact_paths, ())
        self.assertIsNotNone(ExperimentRuntime)


if __name__ == "__main__":
    unittest.main()
