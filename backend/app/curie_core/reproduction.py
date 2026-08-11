"""Dependency-light reproduction-mode logic shared by retained Curie components.

The legacy node modules delegate their production-mode behavior here so the
same orchestration can be tested without importing LangGraph or OpenHands.
These functions do not define new agents; they are the structured execution
facets of the existing Architect, Technician, validators, Analyzer, Concluder,
and InternalExperimentScheduler.
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


def llm_validator_guard(guard, context, locked_snapshot):
    return guard.validate_values(context, locked_snapshot)


def patcher_guard(guard, context, proposed_values):
    return guard.validate_patch(context, proposed_values)


def exec_validate(result):
    status = result.status.value
    if status == "timed_out":
        return {
            "valid": False,
            "status": "timeout",
            "violations": ("command timed out; execution is indeterminate",),
        }
    if status == "failed":
        return {
            "valid": False,
            "status": "failed",
            "violations": ("command returned a non-zero exit code",),
        }
    if status == "succeeded" and result.exit_code == 0:
        return {"valid": True, "status": "passed", "violations": ()}
    return {
        "valid": False,
        "status": "indeterminate",
        "violations": ("execution result is internally inconsistent",),
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
