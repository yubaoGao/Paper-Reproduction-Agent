"""Deterministic merge and cross-reference validation."""
from __future__ import annotations
from backend.app.domain import *
from .evidence import RepositoryEvidenceValidator

class RepositoryCatalogValidationError(ValueError): pass

def _unique(records,field):
    result={}
    for record in records:result.setdefault(getattr(record,field),record)
    return tuple(result.values())

class RepositoryCatalogMerger:
    def merge(self,snapshot,static,stages,missing=(),warnings=()):
        semantic_components=[x for _,stage in stages for x in stage.components]
        def components(kind,base):return _unique((*base,*(x for x in semantic_components if x.kind.startswith(kind))),"component_id")
        implementations=_unique((x for _,stage in stages for x in stage.implementations),"implementation_id")
        evaluation_policies=_unique((x for _,stage in stages for x in stage.evaluation_policies),"policy_id")
        unknowns=tuple(x for _,stage in stages for x in stage.facts)
        conflicts=_unique((*static.conflicts,*(x for _,stage in stages for x in stage.conflicts)),"conflict_id")
        evidence=[]
        groups=(static.documentation,static.environment_definitions,static.dependencies,static.entrypoints,static.configurations,static.datasets,static.models,static.ablations,static.metrics,static.checkpoints,static.artifacts,static.commands,implementations,evaluation_policies)
        for group in groups:
            for record in group:evidence.extend(getattr(record,"evidence",()))
        for _,stage in stages:evidence.extend(stage.evidence)
        status=RepositoryAnalysisStatus.PARTIAL if missing else RepositoryAnalysisStatus.COMPLETE
        return RepositoryAnalysisCatalog(catalog_id=f"repository-catalog:{snapshot.snapshot_id}",repository=snapshot.repository,snapshot_id=snapshot.snapshot_id,resolved_commit_sha=snapshot.resolved_commit_sha,languages=snapshot.languages,project_structure=tuple(x.path for x in snapshot.files),code_index=static.code_index,documentation=static.documentation,environment_definitions=static.environment_definitions,dependencies=static.dependencies,entrypoints=static.entrypoints,configurations=static.configurations,datasets=components("dataset",static.datasets),models=components("model",static.models),experiment_implementations=implementations,evaluation_policies=evaluation_policies,ablation_mechanisms=components("ablation",static.ablations),metrics=components("metric",static.metrics),checkpoints=components("checkpoint",static.checkpoints),artifact_paths=components("artifact",static.artifacts),commands=static.commands,evidence=tuple(dict.fromkeys(evidence)),conflicts=conflicts,unknowns=unknowns,analysis_status=status,analysis_metadata=RepositoryAnalysisMetadata(stages_completed=tuple(stage_name for stage_name,_ in stages),missing_components=tuple(dict.fromkeys(missing)),warnings=tuple(dict.fromkeys((*static.warnings,*warnings))),prompt_versions={"context_classification":"v1","stage_analysis":"v1","repair":"v1","catalog_review":"v1"}))

class RepositoryCatalogValidator:
    def __init__(self,evidence_validator=None):self.evidence=evidence_validator or RepositoryEvidenceValidator()
    def validate(self,catalog,snapshot,static):
        if catalog.snapshot_id!=snapshot.snapshot_id or catalog.resolved_commit_sha!=snapshot.resolved_commit_sha:raise RepositoryCatalogValidationError("catalog identity does not match snapshot")
        paths={x.path for x in snapshot.files};symbols={x.symbol_id for x in static.code_index.symbols};entries={x.entrypoint_id for x in catalog.entrypoints};configs={x.config_id for x in catalog.configurations};components={x.component_id for x in (*catalog.datasets,*catalog.models)};commands={x.command_id for x in catalog.commands}
        if any(x.path not in paths for x in catalog.code_index.symbols):raise RepositoryCatalogValidationError("code index contains a dangling path")
        for config in catalog.configurations:
            if config.path not in paths or any(x not in configs and x not in paths for x in config.references):raise RepositoryCatalogValidationError(f"invalid config references: {config.config_id}")
        for dependency in catalog.dependencies:
            if dependency.source_path not in paths:raise RepositoryCatalogValidationError(f"invalid dependency reference: {dependency.dependency_id}")
        for path in catalog.project_structure:
            if path not in paths:raise RepositoryCatalogValidationError(f"unknown project path: {path}")
        for entry in catalog.entrypoints:
            if entry.path not in paths or (entry.symbol_id and entry.symbol_id not in symbols):raise RepositoryCatalogValidationError(f"invalid entrypoint: {entry.entrypoint_id}")
        for item in (*catalog.documentation,*catalog.environment_definitions,*catalog.datasets,*catalog.models,*catalog.ablation_mechanisms,*catalog.metrics,*catalog.checkpoints,*catalog.artifact_paths):
            if any(x not in paths for x in item.paths) or any(x not in symbols for x in item.symbol_ids):raise RepositoryCatalogValidationError(f"invalid component references: {item.component_id}")
        for item in catalog.experiment_implementations:
                if not set(item.entrypoint_ids)<=entries or not set(item.config_ids)<=configs or not set((*item.dataset_ids,*item.model_ids))<=components or not set(item.command_ids)<=commands:raise RepositoryCatalogValidationError(f"invalid implementation references: {item.implementation_id}")
        implementations={x.implementation_id for x in catalog.experiment_implementations}
        for item in catalog.evaluation_policies:
            if item.implementation_id and item.implementation_id not in implementations:raise RepositoryCatalogValidationError(f"invalid evaluation policy implementation: {item.policy_id}")
            if not set(item.entrypoint_ids)<=entries:raise RepositoryCatalogValidationError(f"invalid evaluation policy entrypoint: {item.policy_id}")
            if any(value and value not in commands for value in (item.training_command_id,item.evaluation_command_id)):raise RepositoryCatalogValidationError(f"invalid evaluation policy command: {item.policy_id}")
        for command in catalog.commands:
            if command.source_path not in paths or (command.entrypoint_path and command.entrypoint_path not in paths):raise RepositoryCatalogValidationError(f"invalid command references: {command.command_id}")
        id_groups=((catalog.documentation,"component_id"),(catalog.environment_definitions,"component_id"),(catalog.datasets,"component_id"),(catalog.models,"component_id"),(catalog.ablation_mechanisms,"component_id"),(catalog.metrics,"component_id"),(catalog.checkpoints,"component_id"),(catalog.artifact_paths,"component_id"),(catalog.evaluation_policies,"policy_id"),(catalog.commands,"command_id"),(catalog.conflicts,"conflict_id"))
        for group,field in id_groups:
            values=[getattr(x,field) for x in group]
            if len(values)!=len(set(values)):raise RepositoryCatalogValidationError(f"duplicate {field}")
        all_evidence=list(catalog.evidence)
        for conflict in catalog.conflicts:
            if conflict.status is RepositoryConflictStatus.RESOLVED and conflict.resolution is None:raise RepositoryCatalogValidationError(f"resolved conflict has no resolution: {conflict.conflict_id}")
            for candidate in conflict.candidates:all_evidence.extend(candidate.evidence)
        for group in (catalog.documentation,catalog.environment_definitions,catalog.dependencies,catalog.entrypoints,catalog.configurations,catalog.datasets,catalog.models,catalog.experiment_implementations,catalog.evaluation_policies,catalog.ablation_mechanisms,catalog.metrics,catalog.checkpoints,catalog.artifact_paths,catalog.commands):
            for record in group:
                all_evidence.extend(record.evidence)
                if isinstance(record,RepositoryEvaluationPolicyRecord):
                    for value in record.policy.evidence:
                        try:all_evidence.append(EvidenceReference.model_validate(value))
                        except Exception as exc:raise RepositoryCatalogValidationError(f"invalid evaluation policy evidence: {record.policy_id}") from exc
        self.evidence.validate_all(all_evidence,snapshot,static)
        return catalog
