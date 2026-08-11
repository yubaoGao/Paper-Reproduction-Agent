import unittest

from pydantic import ValidationError

from backend.app.curie_core.formatter import NewExperimentalPlanResponseFormatter


class ExperimentPlanModelTests(unittest.TestCase):
    def test_valid_experiment_plan_model(self) -> None:
        plan = NewExperimentalPlanResponseFormatter(
            hypothesis="A controlled change affects the measured metric.",
            constant_vars=["seed"],
            independent_vars=["learning_rate"],
            dependent_vars=["accuracy"],
            controlled_experiment_setup_description="Keep seed fixed and vary learning rate.",
            control_group=[{"learning_rate": "0.001"}],
            experimental_group=[{"learning_rate": "0.01"}],
            priority=1,
        )

        self.assertEqual(plan.priority, 1)
        self.assertEqual(len(plan.experimental_group), 1)

    def test_control_group_is_required(self) -> None:
        with self.assertRaises(ValidationError):
            NewExperimentalPlanResponseFormatter(
                hypothesis="Invalid plan",
                constant_vars=[],
                independent_vars=["x"],
                dependent_vars=["y"],
                controlled_experiment_setup_description="test",
                control_group=[],
                priority=1,
            )


if __name__ == "__main__":
    unittest.main()
