"""Production PostgreSQL persistence adapters."""

from .models import PersistenceBase
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
