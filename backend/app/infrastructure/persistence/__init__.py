"""Production PostgreSQL persistence adapters."""

from .models import PersistenceBase
from .analysis_queue import PostgresIntakeAnalysisQueue
from .job_queue import PostgresDurableJobQueue
from .gpu_scheduler import (
    PostgresGPUAwareJobQueue,PostgresGPUScheduler,PostgresGPUWorkerResourcePort,
)
from .resource_registry import PostgresResourceRegistry
from .repositories import (
    PostgresComparisonReportRepository,
    PostgresFinalResultRepository,
    PostgresPersistence,
    PostgresProductPersistence,
    PostgresPersistenceUnitOfWork,
    PostgresPlanningSnapshotRepository,
    PostgresRepositorySnapshotRegistry,
    PostgresReproductionEventRepository,
    PostgresReproductionIntakeRepository,
    PostgresReproductionJobRepository,
    PostgresReproductionRunRepository,
    PostgresReproductionSessionRepository,
)
from .serialization import deserialize_domain, serialize_domain

__all__ = [
    "PersistenceBase",
    "PostgresDurableJobQueue",
    "PostgresIntakeAnalysisQueue",
    "PostgresGPUScheduler",
    "PostgresResourceRegistry",
    "PostgresGPUWorkerResourcePort",
    "PostgresGPUAwareJobQueue",
    "PostgresComparisonReportRepository",
    "PostgresFinalResultRepository",
    "PostgresPersistence",
    "PostgresProductPersistence",
    "PostgresPersistenceUnitOfWork",
    "PostgresPlanningSnapshotRepository",
    "PostgresRepositorySnapshotRegistry",
    "PostgresReproductionEventRepository",
    "PostgresReproductionIntakeRepository",
    "PostgresReproductionJobRepository",
    "PostgresReproductionRunRepository",
    "PostgresReproductionSessionRepository",
    "deserialize_domain",
    "serialize_domain",
]
