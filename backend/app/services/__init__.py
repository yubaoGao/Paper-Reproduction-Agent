"""PaperReproAgent application-service namespace."""

from .paper_ingestion import (
    CompositePaperParser,
    InvalidPaperSourceError,
    PaperDownloadError,
    PaperIngestionError,
    PaperIngestionService,
    PaperIngestionSettings,
    PaperParser,
    PaperParsingError,
    PaperSourceResolver,
    ResolvedPaperSource,
    UnsafePaperSourceError,
)
from .repository_analysis import (
    InvalidRepositorySourceError,RepositoryAnalysisError,RepositoryAnalysisSettings,
    RepositoryCredentialProvider,RepositoryResolutionError,RepositorySnapshotBuilder,
    RepositorySourceResolver,RepositoryStaticAnalysisError,ResolvedRepositorySource,
    UnsafeRepositorySourceError,
)
from .alignment import AlignmentSettings,AlignmentValidationError,PaperCodeAlignmentError
from .planning import PlanningSettings,PlanningValidationError,ReproductionPlanningError
from .reproduction_intake import ReproductionIntakeError,ReproductionIntakeService
from .result_resolution import CanonicalResultResolver,RepositoryResultAdapter,ResultResolutionRequest,ResultResolver,aggregate_final_result
from .result_comparison import DeterministicResultComparator,MetricIdentityNormalizer

__all__ = [
    "CompositePaperParser",
    "InvalidPaperSourceError",
    "PaperDownloadError",
    "PaperIngestionError",
    "PaperIngestionService",
    "PaperIngestionSettings",
    "PaperParser",
    "PaperParsingError",
    "PaperSourceResolver",
    "ResolvedPaperSource",
    "UnsafePaperSourceError",
    "InvalidRepositorySourceError","RepositoryAnalysisError","RepositoryAnalysisSettings",
    "RepositoryCredentialProvider","RepositoryResolutionError","RepositorySnapshotBuilder",
    "RepositorySourceResolver","RepositoryStaticAnalysisError","ResolvedRepositorySource",
    "UnsafeRepositorySourceError",
    "AlignmentSettings","AlignmentValidationError","PaperCodeAlignmentError",
    "PlanningSettings","PlanningValidationError","ReproductionPlanningError",
    "ReproductionIntakeError","ReproductionIntakeService",
    "CanonicalResultResolver","RepositoryResultAdapter","ResultResolutionRequest","ResultResolver","aggregate_final_result",
    "DeterministicResultComparator","MetricIdentityNormalizer",
]
