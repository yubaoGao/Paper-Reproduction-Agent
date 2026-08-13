"""Production PostgreSQL persistence adapters."""

from .models import PersistenceBase
from .job_queue import PostgresDurableJobQueue
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
