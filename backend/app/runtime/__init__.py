"""Provider-neutral runtime contracts.

Production code imports concrete runtime models and ports from their defining
modules. Test-only adapters and sinks are intentionally not re-exported here,
so importing a production submodule cannot initialize in-memory helpers.
"""

from .interfaces import ExperimentRuntime, RunEventSink

__all__ = ["ExperimentRuntime", "RunEventSink"]
