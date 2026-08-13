"""Selected-experiment external resource derivation, validation, and gating."""

from __future__ import annotations

import hashlib
from pathlib import Path, PurePath
from typing import Protocol, runtime_checkable
from urllib.parse import urlsplit

from backend.app.domain import (
    ExperimentSelection, ExternalResourceIntakeResult, ExternalResourceReference,
    ExternalResourceRequirement,
    ExternalResourceType, GoalResolutionStatus, PaperExperimentCatalog,
    RepositoryAnalysisCatalog, ResourceAccess, ResourceBinding,
    ResourceBindingValidationStatus, ResourceIntakeState, ResourcePreparationHint,
    ResourceResolution, ResourceResolutionReport, ResourceResolutionStatus,
    ReproductionSpecification, normalize_resource_name,
)


class ResourceRegistryError(RuntimeError): pass
class ResourceAccessDeniedError(ResourceRegistryError): pass
class ResourcePathValidationError(ResourceRegistryError): pass
class ResourcesNotReadyError(ResourceRegistryError): pass


@runtime_checkable
class ResourceRegistry(Protocol):
    def register(self, binding: ResourceBinding) -> None: ...
    def get(self, resource_id: str, principal: str) -> ResourceBinding: ...
    def get_by_identity(self, resource_type: ExternalResourceType, canonical_name: str, principal: str) -> ResourceBinding | None: ...
    def list_accessible(self, principal: str) -> tuple[ResourceBinding, ...]: ...
    def validate_access(self, binding: ResourceBinding, principal: str) -> None: ...


class RequiredExternalResourceDeriver:
    """Derives only explicit resources for the authoritative selected scope."""

    def derive(self, selection, specification, paper_catalog, repository_catalog):
        if selection.resolution_status is not GoalResolutionStatus.RESOLVED:
            raise ValueError("external resources require a resolved selection")
        selected = tuple(selection.selected_experiment_ids)
        if tuple(specification.selected_experiment_ids) != selected:
            raise ValueError("resource derivation scope differs from reproduction specification")
        by_id = {item.experiment_id: item for item in paper_catalog.experiments}
        if any(item not in by_id for item in selected):
            raise ValueError("selected experiment is absent from the paper catalog")
        accumulated = {}
        for experiment_id in selected:
            experiment = by_id[experiment_id]
            if not experiment.dataset:
                continue
            components = self._matching_components(repository_catalog.datasets, experiment.dataset, experiment_id)
            paper_entity = next((item for item in paper_catalog.datasets if normalize_resource_name(item.canonical_name) == normalize_resource_name(experiment.dataset)), None)
            evidence = (*experiment.evidence, *(() if paper_entity is None else paper_entity.evidence), *(value for component in components for value in component.evidence))
            self._add(accumulated, ExternalResourceType.DATASET, experiment.dataset, (experiment_id,), True, self._structure(components), self._instructions(components), evidence)
        self._derive_explicit(accumulated, selected, repository_catalog.checkpoints, ExternalResourceType.CHECKPOINT)
        pretrained = tuple(item for item in repository_catalog.models if item.details.get("external_resource") is True or item.details.get("pretrained") is True)
        self._derive_explicit(accumulated, selected, pretrained, ExternalResourceType.PRETRAINED_MODEL)
        self._derive_metadata(accumulated, selected, specification.metadata.get("external_resources"))
        results = []
        for (resource_type, normalized), value in sorted(accumulated.items(), key=lambda item: (item[0][0].value, item[0][1])):
            digest = hashlib.sha256(f"{resource_type.value}:{normalized}".encode()).hexdigest()[:20]
            results.append(ExternalResourceRequirement(
                requirement_id=f"external-resource:{digest}", resource_type=resource_type,
                canonical_name=value["canonical_name"],
                paper_experiment_ids=tuple(item for item in selected if item in value["scope"]),
                required=value["required"], expected_structure=tuple(dict.fromkeys(value["structure"])),
                hints=tuple(dict.fromkeys(value["hints"])), evidence=tuple(value["evidence"]),
            ))
        return tuple(results)

    def _derive_explicit(self, accumulated, selected, components, resource_type):
        selected_set = set(selected)
        for component in components:
            scope = set(self._strings(component.details, "paper_experiment_ids") or self._strings(component.details, "required_for_experiment_ids")) & selected_set
            if not scope or component.details.get("required", True) is not True:
                continue
            self._add(accumulated, resource_type, component.name, tuple(scope), True, self._structure((component,)), self._instructions((component,)), component.evidence)

    def _derive_metadata(self, accumulated, selected, raw):
        if not isinstance(raw, list): return
        selected_set = set(selected)
        for item in raw[:64]:
            if not isinstance(item, dict): continue
            try: resource_type = ExternalResourceType(item.get("resource_type"))
            except (TypeError, ValueError): continue
            name = item.get("canonical_name")
            scope_raw = item.get("paper_experiment_ids", selected)
            if not isinstance(name, str) or not name.strip() or not isinstance(scope_raw, (list, tuple)): continue
            scope = tuple(value for value in scope_raw if value in selected_set)
            if scope:
                self._add(accumulated, resource_type, name, scope, item.get("required", True) is not False, self._plain_strings(item.get("expected_structure")), self._plain_strings(item.get("hints")), ())

    @classmethod
    def _matching_components(cls, components, canonical_name, experiment_id):
        normalized = normalize_resource_name(canonical_name)
        return tuple(component for component in components if isinstance(component.details.get("canonical_name", component.name), str) and normalize_resource_name(component.details.get("canonical_name", component.name)) == normalized and (not cls._strings(component.details, "paper_experiment_ids") or experiment_id in cls._strings(component.details, "paper_experiment_ids")))

    @classmethod
    def _structure(cls, components):
        return tuple(value for component in components for key in ("expected_structure", "required_paths") for value in cls._strings(component.details, key))

    @classmethod
    def _instructions(cls, components):
        return tuple(value for component in components for key in ("preparation_instructions", "data_preparation", "instructions") for value in cls._strings(component.details, key))

    @staticmethod
    def _strings(mapping, key): return RequiredExternalResourceDeriver._plain_strings(mapping.get(key))

    @staticmethod
    def _plain_strings(value):
        if isinstance(value, str) and value.strip(): return (value.strip(),)
        if isinstance(value, (list, tuple)): return tuple(item.strip() for item in value if isinstance(item, str) and item.strip())
        return ()

    @staticmethod
    def _add(accumulated, resource_type, canonical_name, experiment_ids, required, structure, hints, evidence):
        key = (resource_type, normalize_resource_name(canonical_name))
        value = accumulated.setdefault(key, {"canonical_name": canonical_name, "scope": set(), "required": False, "structure": [], "hints": [], "evidence": []})
        value["scope"].update(experiment_ids); value["required"] |= required
        value["structure"].extend(structure); value["hints"].extend(hints); value["evidence"].extend(evidence)


class ResourcePreparationHintBuilder:
    def build(self, requirement, repository_catalog):
        components = repository_catalog.datasets if requirement.resource_type is ExternalResourceType.DATASET else repository_catalog.checkpoints if requirement.resource_type is ExternalResourceType.CHECKPOINT else repository_catalog.models
        matched = RequiredExternalResourceDeriver._matching_components(components, requirement.canonical_name, requirement.paper_experiment_ids[0])
        documentation = tuple(item for item in repository_catalog.documentation if normalize_resource_name(requirement.canonical_name) in {normalize_resource_name(value) for value in RequiredExternalResourceDeriver._strings(item.details, "resource_names")})
        ordered = (*documentation, *matched)
        instructions = tuple(dict.fromkeys((*((value for item in documentation for value in RequiredExternalResourceDeriver._instructions((item,)))), *requirement.hints)))
        urls = tuple(dict.fromkeys(value for item in ordered for key in ("download_url", "download_urls", "source_url", "source_urls") for value in RequiredExternalResourceDeriver._strings(item.details, key) if self._reliable_url(value)))
        return ResourcePreparationHint(resource_type=requirement.resource_type, canonical_name=requirement.canonical_name, repository_instructions=instructions, source_urls=urls, expected_structure=requirement.expected_structure, evidence=tuple(value for item in ordered for value in item.evidence) + requirement.evidence, user_action=f"Prepare {requirement.canonical_name} without using ReproPilot to download it, then provide an authorized host path.")

    @staticmethod
    def _reliable_url(value):
        parsed = urlsplit(value); return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


class ExternalResourcePathValidator:
    """Validates only an explicit path and a bounded expected structure."""
    def __init__(self, *, approved_roots=(), principal_roots=None):
        self.approved_roots = self._roots(approved_roots)
        self.principal_roots = {principal: self._roots(roots) for principal, roots in (principal_roots or {}).items()}

    def validate(self, host_path, *, principal, expected_structure=(), shared=False):
        try: candidate = Path(host_path).resolve(strict=True)
        except (OSError, RuntimeError) as exc: raise ResourcePathValidationError("external resource path does not exist") from exc
        allowed = self.approved_roots if shared else (*self.approved_roots, *self.principal_roots.get(principal, ()))
        if not allowed or not any(self._within(candidate, root) for root in allowed):
            raise ResourcePathValidationError("external resource path is outside approved or principal-owned roots")
        if len(expected_structure) > 64: raise ResourcePathValidationError("resource structure validation exceeds its bound")
        if expected_structure and not candidate.is_dir(): raise ResourcePathValidationError("structured external resource must be a directory")
        for relative in expected_structure:
            pure = PurePath(relative)
            if pure.is_absolute() or ".." in pure.parts: raise ResourcePathValidationError("expected structure contains an unsafe path")
            try: required = (candidate / pure).resolve(strict=True)
            except (OSError, RuntimeError) as exc: raise ResourcePathValidationError(f"required resource entry is missing: {relative}") from exc
            if not self._within(required, candidate): raise ResourcePathValidationError(f"required resource entry escapes through a symlink: {relative}")
        return str(candidate)

    @staticmethod
    def _roots(values):
        roots = []
        for value in values:
            try: root = Path(value).resolve(strict=True)
            except (OSError, RuntimeError) as exc: raise ResourcePathValidationError(f"configured resource root does not exist: {value}") from exc
            if not root.is_dir(): raise ResourcePathValidationError("configured resource root must be a directory")
            roots.append(root)
        return tuple(roots)

    @staticmethod
    def _within(path, root): return path == root or root in path.parents


class ExternalResourceResolutionService:
    def __init__(self, registry, path_validator, *, deriver=None, hint_builder=None):
        self.registry = registry; self.path_validator = path_validator
        self.deriver = deriver or RequiredExternalResourceDeriver(); self.hint_builder = hint_builder or ResourcePreparationHintBuilder()

    def resolve(self, *, intake_id, principal, selection, specification, paper_catalog, repository_catalog):
        requirements = self.deriver.derive(selection, specification, paper_catalog, repository_catalog)
        return self._report(intake_id=intake_id, principal=principal, specification_id=specification.id, selected_experiment_ids=selection.selected_experiment_ids, requirements=requirements, repository_catalog=repository_catalog)

    def register_user_path_and_resume(self, report, *, requirement_id, host_path, principal, repository_catalog):
        if principal != report.principal: raise ResourceAccessDeniedError("resource intake belongs to another principal")
        requirement = next((item.requirement for item in report.resolutions if item.requirement.requirement_id == requirement_id), None)
        if requirement is None: raise ResourceRegistryError("resource requirement is not part of this intake")
        try:
            canonical_path = self.path_validator.validate(
                host_path, principal=principal,
                expected_structure=requirement.expected_structure,
            )
        except ResourcePathValidationError as exc:
            resolutions = tuple(
                ResourceResolution(
                    requirement=item.requirement,
                    status=ResourceResolutionStatus.INVALID,
                    messages=(str(exc),),
                )
                if item.requirement.requirement_id == requirement_id
                else item
                for item in report.resolutions
            )
            return ResourceResolutionReport(
                intake_id=report.intake_id,
                principal=report.principal,
                specification_id=report.specification_id,
                selected_experiment_ids=report.selected_experiment_ids,
                resolutions=resolutions,
                states=(
                    *report.states,
                    ResourceIntakeState.MISSING_RESOURCE,
                    ResourceIntakeState.WAITING_FOR_USER_RESOURCE,
                ),
                ready_to_run=False,
            )
        existing = self.registry.get_by_identity(requirement.resource_type, requirement.canonical_name, principal)
        if existing is not None and existing.host_path != canonical_path: raise ResourceRegistryError("resource identity already has a different accessible binding")
        if existing is None:
            digest = hashlib.sha256(f"{principal}:{requirement.resource_type.value}:{normalize_resource_name(requirement.canonical_name)}:{canonical_path}".encode()).hexdigest()[:24]
            existing = ResourceBinding(resource_id=f"resource:{digest}", canonical_name=requirement.canonical_name, resource_type=requirement.resource_type, host_path=canonical_path, access=ResourceAccess.READ_ONLY, owner_principal=principal, validation_status=ResourceBindingValidationStatus.VALIDATED, validation_messages=("explicit path and bounded structure validated",))
            self.registry.register(existing)
        return self._report(intake_id=report.intake_id, principal=principal, specification_id=report.specification_id, selected_experiment_ids=report.selected_experiment_ids, requirements=tuple(item.requirement for item in report.resolutions), repository_catalog=repository_catalog, previous_states=report.states)

    def register_shared_binding(self, requirement, *, host_path):
        canonical_path = self.path_validator.validate(host_path, principal="administrator", expected_structure=requirement.expected_structure, shared=True)
        digest = hashlib.sha256(f"shared:{requirement.resource_type.value}:{canonical_path}".encode()).hexdigest()[:24]
        binding = ResourceBinding(resource_id=f"resource:{digest}", canonical_name=requirement.canonical_name, resource_type=requirement.resource_type, host_path=canonical_path, shared=True, validation_status=ResourceBindingValidationStatus.VALIDATED, validation_messages=("administrator-approved shared path validated",))
        self.registry.register(binding); return binding

    def references_for(self, report, paper_experiment_id):
        if not report.ready_to_run: raise ResourcesNotReadyError("missing external resources block execution")
        if paper_experiment_id not in report.selected_experiment_ids: raise ResourcesNotReadyError("unselected experiment cannot receive resource bindings")
        references = []
        for resolution in report.resolutions:
            if paper_experiment_id not in resolution.requirement.paper_experiment_ids or resolution.binding is None: continue
            binding = resolution.binding; digest = hashlib.sha256(binding.resource_id.encode()).hexdigest()[:16]
            root = "/datasets" if binding.resource_type is ExternalResourceType.DATASET else "/checkpoints"
            references.append(ExternalResourceReference(resource_id=binding.resource_id, canonical_name=binding.canonical_name, resource_type=binding.resource_type, logical_mount_path=f"{root}/resource-{digest}"))
        return tuple(references)

    def _report(self, *, intake_id, principal, specification_id, selected_experiment_ids, requirements, repository_catalog, previous_states=()):
        resolutions = []
        for requirement in requirements:
            binding = self.registry.get_by_identity(requirement.resource_type, requirement.canonical_name, principal)
            if binding is None:
                resolutions.append(ResourceResolution(requirement=requirement, status=ResourceResolutionStatus.MISSING, preparation_hint=self.hint_builder.build(requirement, repository_catalog))); continue
            self.registry.validate_access(binding, principal)
            if binding.validation_status is not ResourceBindingValidationStatus.VALIDATED:
                resolutions.append(ResourceResolution(requirement=requirement, status=ResourceResolutionStatus.INVALID, messages=("registered resource binding is not validated",))); continue
            resolutions.append(ResourceResolution(requirement=requirement, status=ResourceResolutionStatus.AVAILABLE, binding=binding))
        ready = all(not item.requirement.required or item.status is ResourceResolutionStatus.AVAILABLE for item in resolutions)
        transition = (ResourceIntakeState.RESOURCES_RESOLVED, ResourceIntakeState.READY_TO_RUN) if ready else (ResourceIntakeState.MISSING_RESOURCE, ResourceIntakeState.WAITING_FOR_USER_RESOURCE)
        states = (*previous_states, *transition) if previous_states else (ResourceIntakeState.GOAL_RESOLVED, *transition)
        return ResourceResolutionReport(intake_id=intake_id, principal=principal, specification_id=specification_id, selected_experiment_ids=selected_experiment_ids, resolutions=tuple(resolutions), states=states, ready_to_run=ready)


class ResolvedExternalResourceProvider:
    """Path-free execution view over one existing reproduction intake."""
    def __init__(self, service: ExternalResourceResolutionService, report: ResourceResolutionReport):
        self.service = service
        self.report = report

    def references_for(self, paper_experiment_id: str):
        return self.service.references_for(self.report, paper_experiment_id)


class ResourceAwareReproductionIntakeService:
    """Composes goal resolution and resource gating without creating a job."""
    def __init__(self, goal_intake, resource_resolution: ExternalResourceResolutionService):
        self.goal_intake = goal_intake
        self.resource_resolution = resource_resolution

    def intake(self, *, intake_id, principal, goal, paper_catalog, repository_catalog):
        goal_result = self.goal_intake.intake(goal, paper_catalog)
        if goal_result.status is not GoalResolutionStatus.RESOLVED:
            return ExternalResourceIntakeResult(goal_resolution=goal_result)
        report = self.resource_resolution.resolve(
            intake_id=intake_id, principal=principal,
            selection=goal_result.selection,
            specification=goal_result.specification,
            paper_catalog=paper_catalog,
            repository_catalog=repository_catalog,
        )
        return ExternalResourceIntakeResult(
            goal_resolution=goal_result, resource_resolution=report,
        )

    def resume_with_user_path(
        self, intake: ExternalResourceIntakeResult, *, requirement_id,
        host_path, principal, repository_catalog,
    ):
        if intake.resource_resolution is None:
            raise ResourceRegistryError("unresolved reproduction intake cannot accept resources")
        report = self.resource_resolution.register_user_path_and_resume(
            intake.resource_resolution, requirement_id=requirement_id,
            host_path=host_path, principal=principal,
            repository_catalog=repository_catalog,
        )
        return intake.model_copy(update={"resource_resolution": report})
