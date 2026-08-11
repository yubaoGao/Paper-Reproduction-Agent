"""Paper-level reproduction goals that precede executable experiments."""

from __future__ import annotations

from enum import Enum

from pydantic import Field, JsonValue, field_validator, model_validator

from .experiment import DomainModel, NonEmptyStr


class PaperSourceType(str, Enum):
    """Ways a paper can be referenced without reading its contents."""

    PDF_UPLOAD = "pdf_upload"
    ARXIV = "arxiv"
    URL = "url"
    LOCAL_FILE = "local_file"


class TargetType(str, Enum):
    """Scientific intent of a requested reproduction target."""

    MAIN_EXPERIMENT = "main_experiment"
    ABLATION = "ablation"
    BASELINE = "baseline"
    CUSTOM = "custom"


class EvidenceSourceType(str, Enum):
    """Origin category for a piece of reproduction evidence."""

    USER = "user"
    PAPER = "paper"
    REPOSITORY = "repository"
    DATASET = "dataset"
    INFERENCE = "inference"
    SYSTEM = "system"


class InformationStatus(str, Enum):
    """Whether a critical reproduction value is stated, inferred or missing."""

    EXPLICIT = "explicit"
    INFERRED = "inferred"
    UNKNOWN = "unknown"


class PaperReference(DomainModel):
    """Bibliographic identity and source pointer; performs no paper IO."""

    id: NonEmptyStr
    title: NonEmptyStr
    authors: tuple[NonEmptyStr, ...] = ()
    doi: NonEmptyStr | None = None
    arxiv_id: NonEmptyStr | None = None
    source_type: PaperSourceType
    source_uri: NonEmptyStr | None = None

    @model_validator(mode="after")
    def validate_source_identity(self) -> PaperReference:
        if self.source_type is PaperSourceType.ARXIV and self.arxiv_id is None:
            raise ValueError("arxiv papers require arxiv_id")
        if self.source_type in {
            PaperSourceType.PDF_UPLOAD,
            PaperSourceType.URL,
            PaperSourceType.LOCAL_FILE,
        } and self.source_uri is None:
            raise ValueError(f"{self.source_type.value} papers require source_uri")
        if self.source_type is PaperSourceType.URL and not self.source_uri.startswith(
            ("https://", "http://")
        ):
            raise ValueError("url paper sources require an HTTP(S) URI")
        if len(set(self.authors)) != len(self.authors):
            raise ValueError("authors must be unique")
        return self


class EvidenceReference(DomainModel):
    """Minimal provenance pointer supporting audit without retrieval behavior."""

    source_type: EvidenceSourceType
    source_id: NonEmptyStr | None = None
    locator: NonEmptyStr | None = None
    text: NonEmptyStr | None = None
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)

    @field_validator("confidence", mode="before")
    @classmethod
    def reject_boolean_confidence(cls, value: object) -> object:
        if isinstance(value, bool):
            raise ValueError("confidence must be numeric")
        return value

    @model_validator(mode="after")
    def require_traceable_location(self) -> EvidenceReference:
        if self.source_id is None and self.locator is None and self.text is None:
            raise ValueError("evidence requires source_id, locator or text")
        return self


class ReproductionParameter(DomainModel):
    """A provenance-sensitive detail needed to plan a reproduction.

    This wrapper is reserved for critical values such as learning rate, weight
    decay or preprocessing choices. Ordinary labels remain ordinary strings.
    """

    name: NonEmptyStr
    value: JsonValue | None = None
    status: InformationStatus
    evidence: tuple[EvidenceReference, ...] = ()
    confidence: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)

    @field_validator("confidence", mode="before")
    @classmethod
    def reject_boolean_confidence(cls, value: object) -> object:
        if isinstance(value, bool):
            raise ValueError("confidence must be numeric")
        return value

    @model_validator(mode="after")
    def validate_information_state(self) -> ReproductionParameter:
        if self.status is InformationStatus.UNKNOWN:
            if self.value is not None:
                raise ValueError("unknown information cannot have a value")
            if self.confidence is not None:
                raise ValueError("unknown information cannot have confidence")
            return self

        if self.value is None:
            raise ValueError(f"{self.status.value} information requires a value")
        if not self.evidence:
            raise ValueError(f"{self.status.value} information requires evidence")
        if self.status is InformationStatus.INFERRED and self.confidence is None:
            raise ValueError("inferred information requires confidence")
        return self


class ReproductionTarget(DomainModel):
    """The paper experiment, table, figure or condition requested by the user."""

    id: NonEmptyStr
    target_type: TargetType
    section: NonEmptyStr | None = None
    table: NonEmptyStr | None = None
    figure: NonEmptyStr | None = None
    experiment_name: NonEmptyStr | None = None
    dataset: NonEmptyStr | None = None
    model: NonEmptyStr | None = None
    variant: NonEmptyStr | None = None
    description: NonEmptyStr | None = None

    @model_validator(mode="after")
    def require_target_scope(self) -> ReproductionTarget:
        scope = (
            self.section,
            self.table,
            self.figure,
            self.experiment_name,
            self.dataset,
            self.model,
            self.variant,
            self.description,
        )
        if not any(scope):
            raise ValueError("target requires a paper locator or experiment description")
        return self


class PaperClaim(DomainModel):
    """A numeric result claimed by a paper, not a measured runtime Metric."""

    id: NonEmptyStr
    metric_name: NonEmptyStr
    value: float = Field(allow_inf_nan=False)
    unit: NonEmptyStr | None = None
    dataset: NonEmptyStr | None = None
    split: NonEmptyStr | None = None
    condition: NonEmptyStr | None = None
    target_id: NonEmptyStr | None = None
    evidence: tuple[EvidenceReference, ...]

    @field_validator("value", mode="before")
    @classmethod
    def reject_boolean_value(cls, value: object) -> object:
        if isinstance(value, bool):
            raise ValueError("claim value must be numeric")
        return value

    @model_validator(mode="after")
    def validate_claim_context(self) -> PaperClaim:
        if not self.evidence:
            raise ValueError("paper claims require source evidence")
        if self.split is not None and self.dataset is None:
            raise ValueError("claims with a split must identify the dataset")
        return self


class AblationDefinition(DomainModel):
    """A structured non-full-model condition in an ablation study.

    ``modified_components`` maps a component or parameter name to a concise
    change description, covering replacement, view removal and parameter/loss
    changes without assuming every ablation is remove-one-component.
    """

    id: NonEmptyStr
    name: NonEmptyStr
    removed_components: tuple[NonEmptyStr, ...] = ()
    modified_components: dict[NonEmptyStr, NonEmptyStr] = Field(default_factory=dict)
    expected_claims: tuple[NonEmptyStr, ...] = ()
    target_dataset: NonEmptyStr | None = None
    description: NonEmptyStr | None = None

    @model_validator(mode="after")
    def validate_ablation_change(self) -> AblationDefinition:
        if not self.removed_components and not self.modified_components:
            raise ValueError("ablation requires a removed or modified component")
        if len(set(self.removed_components)) != len(self.removed_components):
            raise ValueError("removed_components must be unique")
        if len(set(self.expected_claims)) != len(self.expected_claims):
            raise ValueError("expected_claims must be unique")
        return self


class ReproductionSpecification(DomainModel):
    """Paper-semantic reproduction goal that may still contain unknowns.

    This model is intentionally not executable. A future Reproduction Planner
    will combine paper parsing, repository analysis and paper-code alignment to
    produce one or more ``ExperimentSpecification`` instances.
    """

    id: NonEmptyStr
    paper: PaperReference
    user_goal: NonEmptyStr
    targets: tuple[ReproductionTarget, ...]
    claims: tuple[PaperClaim, ...] = ()
    ablations: tuple[AblationDefinition, ...] = ()
    parameters: tuple[ReproductionParameter, ...] = ()
    constraints: tuple[NonEmptyStr, ...] = ()
    metadata: dict[NonEmptyStr, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_internal_references(self) -> ReproductionSpecification:
        if not self.targets:
            raise ValueError("reproduction specification requires at least one target")

        target_ids = [target.id for target in self.targets]
        claim_ids = [claim.id for claim in self.claims]
        ablation_ids = [ablation.id for ablation in self.ablations]
        parameter_names = [parameter.name for parameter in self.parameters]

        self._require_unique(target_ids, "target ids")
        self._require_unique(claim_ids, "claim ids")
        self._require_unique(ablation_ids, "ablation ids")
        self._require_unique(parameter_names, "parameter names")
        self._require_unique(list(self.constraints), "constraints")

        known_targets = set(target_ids)
        for claim in self.claims:
            if claim.target_id is not None and claim.target_id not in known_targets:
                raise ValueError(f"claim {claim.id!r} references an unknown target")

        known_claims = set(claim_ids)
        for ablation in self.ablations:
            missing_claims = set(ablation.expected_claims) - known_claims
            if missing_claims:
                missing = ", ".join(sorted(missing_claims))
                raise ValueError(
                    f"ablation {ablation.id!r} references unknown claims: {missing}"
                )
        return self

    @staticmethod
    def _require_unique(values: list[str], label: str) -> None:
        if len(set(values)) != len(values):
            raise ValueError(f"{label} must be unique")
