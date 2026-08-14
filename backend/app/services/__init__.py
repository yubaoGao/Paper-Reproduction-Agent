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
from .result_resolution import CanonicalResultResolver,RepositoryResultAdapter,RepositoryResultAdapterRegistry,ResultResolutionRequest,ResultResolver,aggregate_final_result
from .result_comparison import DeterministicResultComparator,MetricIdentityNormalizer
from .persistence import (
    ComparisonReportRepository,FinalResultRepository,PersistenceConflictError,
    PersistenceEntityNotFoundError,PersistenceUnitOfWork,PlanningSnapshotRepository,
    ReproductionJobRepository,
    ReproductionEventRepository,ReproductionIntakeRepository,ReproductionSessionRepository,
)
from .job_queue import (
    DurableJobQueue,InvalidJobQueueTransition,JobLeaseConflictError,JobLeaseLostError,
    ReproductionExecutor,ReproductionExecutorFactory,
)
from .gpu import (
    ExecutionConfigApplier,GPUAllocationConflictError,GPUInventoryProvider,
    GPUInventoryUnavailableError,
    GPULeaseLostError,GPUScheduler,ResourceAdaptationAgent,
    MultiGPUSemanticsAnalyzer,WorkerGPUResourcePort,
)
from .external_resources import (
    ExternalResourcePathValidator,ExternalResourceResolutionService,
    RequiredExternalResourceDeriver,ResourceAccessDeniedError,
    ResourcePathValidationError,ResourcePreparationHintBuilder,
    ResourceRegistry,ResourceRegistryError,ResourcesNotReadyError,
    ResolvedExternalResourceProvider,
    ResourceAwareReproductionIntakeService,
)
from .reproduction_api import (
    APIUseCaseError,EntityNotFoundError,IntakeAnalysis,IntakeBootstrapError,InvalidIntakeStateError,
    InvalidSessionStateError,
    PlanningBlockedError,ReproductionAnalysisPipeline,ReproductionAPIService,
)
from .product_events import ProductEventPublisher
from .job_finalization import JobFinalizationError,JobResultFinalizer
from .analysis_queue import IntakeAnalysisQueue,InvalidAnalysisQueueTransition
from .analysis_worker import IntakeAnalysisWorker
from .paper_artifacts import FilesystemIntakePaperStore,IntakePaperArtifactStore

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
    "CanonicalResultResolver","RepositoryResultAdapter","RepositoryResultAdapterRegistry","ResultResolutionRequest","ResultResolver","aggregate_final_result",
    "DeterministicResultComparator","MetricIdentityNormalizer",
    "ComparisonReportRepository","FinalResultRepository","PersistenceConflictError",
    "PersistenceEntityNotFoundError","PersistenceUnitOfWork","PlanningSnapshotRepository",
    "ReproductionJobRepository",
    "ReproductionEventRepository","ReproductionIntakeRepository","ReproductionSessionRepository",
    "DurableJobQueue","InvalidJobQueueTransition","JobLeaseConflictError","JobLeaseLostError",
    "ReproductionExecutor","ReproductionExecutorFactory",
    "ExecutionConfigApplier","GPUAllocationConflictError","GPUInventoryProvider",
    "GPUInventoryUnavailableError",
    "GPULeaseLostError","GPUScheduler","ResourceAdaptationAgent",
    "WorkerGPUResourcePort",
    "MultiGPUSemanticsAnalyzer",
    "ExternalResourcePathValidator","ExternalResourceResolutionService",
    "RequiredExternalResourceDeriver","ResourceAccessDeniedError",
    "ResourcePathValidationError","ResourcePreparationHintBuilder",
    "ResourceRegistry","ResourceRegistryError","ResourcesNotReadyError",
    "ResolvedExternalResourceProvider",
    "ResourceAwareReproductionIntakeService",
    "APIUseCaseError","EntityNotFoundError","IntakeAnalysis","IntakeBootstrapError","InvalidIntakeStateError",
    "InvalidSessionStateError",
    "PlanningBlockedError","ReproductionAnalysisPipeline","ReproductionAPIService",
    "ProductEventPublisher",
    "JobFinalizationError","JobResultFinalizer",
    "IntakeAnalysisQueue","InvalidAnalysisQueueTransition",
    "IntakeAnalysisWorker",
    "FilesystemIntakePaperStore","IntakePaperArtifactStore",
]
