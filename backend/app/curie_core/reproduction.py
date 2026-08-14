"""Dependency-light scientific reasoning used by production orchestration.

The functions are deterministic facets for plan construction, partitioning,
command preparation, analysis, and conclusion. They
do not own platform scheduling, sandbox execution, persistence, or transport.
"""
import hashlib


def architect_plan(context, locked_values):
    digest = hashlib.sha256(
        f"{context.run_id}:{context.experiment_id}".encode()
    ).hexdigest()[:16]
    return {
        "plan_id": f"curie-plan:{digest}",
        "summary": f"Execute locked specification {context.experiment_id}",
        "tasks": (
            "prepare_workspace",
            "execute_command",
            "validate_execution",
            "analyze_results",
            "conclude",
        ),
        "locked_snapshot": locked_values,
    }


def scheduler_partition(plan):
    return ({
        "partition_id": f"{plan.plan_id}:partition:0",
        "tasks": plan.tasks,
        "group": "experimental",
    },)


def technician_command(context, workspace, timeout_seconds):
    command = context.command
    return {
        "run_id": context.run_id,
        "experiment_id": context.experiment_id,
        "command_id": command.command_reference_id or f"command:{context.experiment_id}",
        "program": command.program,
        "argv": command.arguments,
        "working_directory_reference": workspace.repository_workspace,
        "environment_references": command.environment_variable_references,
        "timeout_seconds": timeout_seconds,
    }


def analyzer_interpret(result):
    return {
        "execution_status": result.status.value,
        "metric_names": [item.name for item in result.metrics],
        "artifact_names": [item.name for item in result.artifacts],
        "has_stderr": bool(result.stderr),
    }


def concluder_decide(result, validations):
    latest = {item.validator_name: item for item in validations}
    if result.status.value == "succeeded" and all(
        item.valid for item in latest.values()
    ):
        return "Execution completed with sufficient validated evidence."
    if result.status.value == "timed_out":
        return "Execution timed out and is indeterminate."
    return "Execution failed or violated the locked reproduction specification."
