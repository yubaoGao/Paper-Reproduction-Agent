"""Production PostgreSQL persistence adapters."""

from .models import PersistenceBase
from .job_queue import PostgresDurableJobQueue
from .gpu_scheduler import (
    PostgresGPUAwareJobQueue,PostgresGPUScheduler,PostgresGPUWorkerResourcePort,
)
from .repositories import (
    PostgresComparisonReportRepository,
    PostgresFinalResultRepository,
    PostgresPersistence,
    PostgresPersistenceUnitOfWork,
    PostgresPlanningSnapshotRepository,
    PostgresReproductionJobRepository,
    PostgresReproductionRunRepository,
)
from .serialization import deserialize_domain, serialize_domain

__all__ = [
    "PersistenceBase",
    "PostgresDurableJobQueue",
    "PostgresGPUScheduler",
    "PostgresGPUWorkerResourcePort",
    "PostgresGPUAwareJobQueue",
    "PostgresComparisonReportRepository",
    "PostgresFinalResultRepository",
    "PostgresPersistence",
    "PostgresPersistenceUnitOfWork",
    "PostgresPlanningSnapshotRepository",
    "PostgresReproductionJobRepository",
    "PostgresReproductionRunRepository",
    "deserialize_domain",
    "serialize_domain",
]
