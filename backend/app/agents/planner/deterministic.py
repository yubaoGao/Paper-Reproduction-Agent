"""Deterministic, auditable reproduction-plan construction."""
from __future__ import annotations
import hashlib
import re
from backend.app.domain import *

def _norm(value):
    return re.sub(r"[^a-z0-9]+","",(value or "").casefold())

def _id(prefix,*parts):
    return f"{prefix}:"+hashlib.sha256("\x1f".join(str(x) for x in parts).encode()).hexdigest()[:16]

class DeterministicPlanBuilder:
    def build(self,spec,paper,repo,alignment,policy,overrides,semantic=None):
        semantic=semantic or {}; blockers=[]; unresolved=[]; decisions=[]; experiments=[]
        selected=self._targets(spec,paper,blockers)
        alignments={x.paper_experiment_id:x for x in alignment.experiment_alignments}
        entrypoints={x.entrypoint_id:x for x in repo.entrypoints}; configs={x.config_id:x for x in repo.configurations}
        datasets={x.component_id:x for x in repo.datasets}; components={x.component_id:x for x in repo.ablation_mechanisms}
        params={x.alignment_id:x for x in alignment.parameter_mappings}; dataset_maps={x.alignment_id:x for x in alignment.dataset_mappings}
        conflicts={x.conflict_id:x for x in alignment.conflicts}
        user_params={x.name:x.value for x in spec.parameters if x.value is not None and any(e.source_type is EvidenceSourceType.USER for e in x.evidence)}|dict(overrides.parameters)
        for paper_exp in selected:
            record=alignments.get(paper_exp.experiment_id)
            if record is None:
                blockers.append(self._block("missing_alignment",paper_exp.experiment_id,"No paper-code experiment alignment exists.")); continue
            strict_conflicts=[conflicts[x] for x in record.conflict_ids if x in conflicts and conflicts[x].status is AlignmentConflictStatus.UNRESOLVED]
            if policy is ReproductionPolicy.STRICT and strict_conflicts:
                for conflict in strict_conflicts: blockers.append(self._block("unresolved_alignment_conflict",paper_exp.experiment_id,f"Strict policy cannot resolve alignment conflict {conflict.semantic_key!r}.",record.alignment_id,conflict.conflict_id))
                continue
            ep_ids=list(record.entrypoint_ids)
            semantic_exp=semantic.get(paper_exp.experiment_id,{})
            requested=overrides.entrypoint_ids.get(paper_exp.experiment_id) or semantic_exp.get("entrypoint_id")
            if requested: ep_ids=[requested] if requested in record.entrypoint_ids and requested in entrypoints else []
            if len(ep_ids)!=1:
                code="ambiguous_entrypoint" if len(ep_ids)>1 else "missing_entrypoint"
                blockers.append(self._block(code,paper_exp.experiment_id,"A single evidence-backed entrypoint could not be selected.",record.alignment_id)); continue
            ep=entrypoints.get(ep_ids[0])
            if ep is None:
                blockers.append(self._block("invalid_entrypoint_reference",paper_exp.experiment_id,"Alignment references an unknown repository entrypoint.",record.alignment_id)); continue
            selected_configs=tuple(overrides.config_ids.get(paper_exp.experiment_id) or semantic_exp.get("config_ids") or record.config_ids)
            config_ambiguities=[x for x in alignment.ambiguities if x.paper_item_id in {paper_exp.experiment_id,record.alignment_id} and len(set(x.candidate_repository_ids)&set(record.config_ids))>1]
            if config_ambiguities and not overrides.config_ids.get(paper_exp.experiment_id) and not semantic_exp.get("config_ids"):
                blockers.append(self._block("ambiguous_config",paper_exp.experiment_id,"Multiple aligned configuration candidates require semantic selection.",record.alignment_id)); continue
            if any(x not in configs for x in selected_configs):
                blockers.append(self._block("invalid_config_reference",paper_exp.experiment_id,"Selected configuration is absent from repository analysis.",record.alignment_id)); continue
            values={}; exp_decisions=[]; failed=False
            mappings=[params[x] for x in record.parameter_mapping_ids if x in params]
            mappings += [x for x in alignment.parameter_mappings if x.paper_experiment_id is None and x.alignment_id not in record.parameter_mapping_ids]
            for mapping in mappings:
                key=mapping.semantic_name; value,source,reason=self._parameter(mapping,user_params,policy)
                if value is None:
                    blockers.append(self._block("unresolved_parameter",paper_exp.experiment_id,f"Parameter {key!r} cannot be resolved under {policy.value}.",mapping.alignment_id,mapping.conflict_id)); failed=True; continue
                values[key]=value
                decision=PlannerDecision(decision_id=_id("decision",paper_exp.experiment_id,key),experiment_id=paper_exp.experiment_id,semantic_key=f"parameter:{key}",selected_value=value,source=source,alternative_values=tuple(x for x in (mapping.paper_value,mapping.repository_value) if x is not None and x!=value),policy=policy,reason=reason,paper_evidence=mapping.paper_evidence,repository_evidence=mapping.repository_evidence,alignment_reference=mapping.alignment_id,confidence=mapping.confidence)
                decisions.append(decision); exp_decisions.append(decision.decision_id)
            for key,value in user_params.items():
                if key not in values:
                    values[key]=value
                    decision=PlannerDecision(decision_id=_id("decision",paper_exp.experiment_id,key,"user"),experiment_id=paper_exp.experiment_id,semantic_key=f"parameter:{key}",selected_value=value,source=DecisionSource.USER,policy=policy,reason="Explicit user override has highest priority.",confidence=1)
                    decisions.append(decision); exp_decisions.append(decision.decision_id)
            if failed: continue
            if paper_exp.experiment_type is ExperimentType.ABLATION:
                maps=[x for x in alignment.ablation_mappings if x.paper_experiment_id==paper_exp.experiment_id or x.alignment_id in record.ablation_mapping_ids]
                if not maps or not any(x.repository_ablation_ids for x in maps):
                    blockers.append(self._block("missing_ablation_mapping",paper_exp.experiment_id,"Ablation condition has no repository mechanism.",record.alignment_id)); continue
                for mapping in maps:
                    for component_id in mapping.repository_ablation_ids:
                        component=components.get(component_id)
                        if component is None:
                            blockers.append(self._block("invalid_ablation_reference",paper_exp.experiment_id,"Ablation mapping references an unknown mechanism.",mapping.alignment_id)); failed=True; continue
                        value=component.details.get("value",paper_exp.conditions.get(component.name))
                        if value is None:
                            blockers.append(self._block("unknown_ablation_value",paper_exp.experiment_id,f"Ablation mechanism {component.name!r} has no explicit value.",mapping.alignment_id)); failed=True; continue
                        values[component.name]=value
                        decision=PlannerDecision(decision_id=_id("decision",paper_exp.experiment_id,"ablation",component.name),experiment_id=paper_exp.experiment_id,semantic_key=f"ablation:{component.name}",selected_value=value,source=DecisionSource.ALIGNMENT,policy=policy,reason="Ablation modification is backed by the aligned repository mechanism.",paper_evidence=mapping.paper_evidence,repository_evidence=component.evidence,alignment_reference=mapping.alignment_id,confidence=mapping.confidence)
                        decisions.append(decision); exp_decisions.append(decision.decision_id)
            if failed: continue
            dataset_req=self._dataset(paper_exp,record,dataset_maps,datasets,overrides,unresolved)
            if paper_exp.dataset and dataset_req is None:
                blockers.append(self._block("missing_dataset_mapping",paper_exp.experiment_id,"Paper dataset has no usable repository mapping.",record.alignment_id)); continue
            entry_source=DecisionSource.USER if overrides.entrypoint_ids.get(paper_exp.experiment_id) else (DecisionSource.PLANNER_DECISION if semantic_exp.get("entrypoint_id") else DecisionSource.ALIGNMENT)
            entry_decision=PlannerDecision(decision_id=_id("decision",paper_exp.experiment_id,"entrypoint"),experiment_id=paper_exp.experiment_id,semantic_key="entrypoint",selected_value=ep.entrypoint_id,source=entry_source,alternative_values=tuple(x for x in record.entrypoint_ids if x!=ep.entrypoint_id),policy=policy,reason="Selected from the experiment alignment; semantic selection is bounded to aligned candidates.",paper_evidence=record.paper_evidence,repository_evidence=ep.evidence,alignment_reference=record.alignment_id,confidence=1 if entry_source is DecisionSource.USER else min(record.confidence,ep.confidence))
            decisions.append(entry_decision); exp_decisions.append(entry_decision.decision_id)
            if selected_configs:
                config_source=DecisionSource.USER if overrides.config_ids.get(paper_exp.experiment_id) else (DecisionSource.PLANNER_DECISION if semantic_exp.get("config_ids") else DecisionSource.ALIGNMENT)
                config_decision=PlannerDecision(decision_id=_id("decision",paper_exp.experiment_id,"configs"),experiment_id=paper_exp.experiment_id,semantic_key="configs",selected_value=list(selected_configs),source=config_source,policy=policy,reason="Configuration references come from the validated experiment alignment.",paper_evidence=record.paper_evidence,repository_evidence=tuple(e for x in selected_configs for e in configs[x].evidence),alignment_reference=record.alignment_id,confidence=1 if config_source is DecisionSource.USER else record.confidence)
                decisions.append(config_decision); exp_decisions.append(config_decision.decision_id)
            if dataset_req is not None:
                dataset_decision=PlannerDecision(decision_id=_id("decision",paper_exp.experiment_id,"dataset"),experiment_id=paper_exp.experiment_id,semantic_key="dataset",selected_value=dataset_req.repository_dataset_id,source=DecisionSource.ALIGNMENT,policy=policy,reason="Dataset loader selection comes from Task 07 alignment.",paper_evidence=dataset_req.paper_evidence,repository_evidence=dataset_req.repository_evidence,alignment_reference=record.dataset_mapping_id,confidence=record.confidence)
                decisions.append(dataset_decision); exp_decisions.append(dataset_decision.decision_id)
                if dataset_req.binding:
                    binding_decision=PlannerDecision(decision_id=_id("decision",paper_exp.experiment_id,"dataset_binding"),experiment_id=paper_exp.experiment_id,semantic_key="dataset_binding",selected_value=dataset_req.binding,source=DecisionSource.USER,policy=policy,reason="Dataset binding was explicitly supplied by the operator.",confidence=1)
                    decisions.append(binding_decision); exp_decisions.append(binding_decision.decision_id)
            commands=[x for x in repo.commands if x.command_id in record.command_ids and (x.entrypoint_path is None or x.entrypoint_path==ep.path)]
            command_ref=commands[0] if len(commands)==1 else None
            arguments=(ep.path,)+(() if command_ref is None else command_ref.arguments)
            program=ep.interpreter or ("python" if ep.path.casefold().endswith(".py") else ep.path)
            resolved=ExecutableCommand(program=program,arguments=arguments,entrypoint_id=ep.entrypoint_id,config_ids=selected_configs,command_reference_id=None if command_ref is None else command_ref.command_id,environment_variable_references=() if command_ref is None else command_ref.environment_variables)
            environment=self._environment(repo)
            claims=tuple(dict.fromkeys([x.id for x in paper_exp.claims]+[x.id for x in paper.paper_claims if x.target_id==paper_exp.experiment_id]))
            claim_records={x.id:x for x in (*paper_exp.claims,*paper.paper_claims)}
            metrics=tuple(MetricExpectation(name=claim_records[x].metric_name,value=claim_records[x].value,split=claim_records[x].split,unit=claim_records[x].unit) for x in claims if x in claim_records)
            exp_id=_id("experiment",spec.id,paper_exp.experiment_id)
            task={ExperimentType.ABLATION:ExperimentTaskType.ABLATION,ExperimentType.BASELINE:ExperimentTaskType.BASELINE_REPRODUCTION}.get(paper_exp.experiment_type,ExperimentTaskType.FULL_REPRODUCTION)
            legacy_dataset=DatasetSource(uri=dataset_req.binding,name=dataset_req.name) if dataset_req is not None and dataset_req.binding else None
            experiments.append(ExperimentSpecification(id=exp_id,name=paper_exp.name,description=f"Planned reproduction of paper experiment {paper_exp.experiment_id}.",task_type=task,repository=RepositorySource(uri=repo.repository.source_uri,revision=repo.resolved_commit_sha),dataset=legacy_dataset,entrypoint=ep.path,resolved_command=resolved,dataset_requirement=dataset_req,environment_requirement=environment,resource_requirement=self._resources(environment),hyperparameters=values,expected_metrics=metrics,expected_claim_ids=claims,provenance_decision_ids=tuple(exp_decisions),tags=(paper_exp.experiment_type.value,),metadata={"paper_experiment_id":paper_exp.experiment_id,"alignment_id":record.alignment_id,"repository_snapshot_id":repo.snapshot_id,"implementation_id":record.repository_implementation_ids[0] if len(record.repository_implementation_ids)==1 else None}))
        dependencies=[]; by_paper={x.metadata.get("paper_experiment_id"):x.id for x in experiments}
        for child,parents in overrides.dependencies.items():
            if child in by_paper and all(x in by_paper for x in parents): dependencies.append(ExperimentDependency(experiment_id=by_paper[child],depends_on_experiment_ids=tuple(by_paper[x] for x in parents),reason="Explicit planning dependency."))
            else: blockers.append(self._block("invalid_dependency",child,"Dependency references an unplanned experiment."))
        order=self._order(tuple(x.id for x in experiments),dependencies,blockers)
        status=PlanStatus.BLOCKED if blockers else (PlanStatus.NEEDS_CONFIRMATION if unresolved else PlanStatus.READY)
        return ReproductionExecutionPlan(plan_id=_id("plan",spec.id,alignment.catalog_id,policy.value),reproduction_specification_id=spec.id,paper=paper.paper,repository=repo.repository,repository_snapshot_id=repo.snapshot_id,resolved_commit_sha=repo.resolved_commit_sha,alignment_catalog_id=alignment.catalog_id,policy=policy,target_experiment_ids=tuple(x.experiment_id for x in selected),experiments=tuple(experiments),execution_order=order,dependencies=tuple(dependencies),warnings=tuple(paper.extraction_metadata.warnings)+tuple(repo.analysis_metadata.warnings)+tuple(alignment.alignment_metadata.warnings),blockers=tuple(blockers),decisions=tuple(decisions),unresolved_items=tuple(unresolved),status=status,metadata=PlanningMetadata(stages_completed=("target_resolution","policy_resolution","execution_specification"),prompt_versions={}))

    def _targets(self,spec,paper,blockers):
        if spec.selected_experiment_ids:
            by_id={item.experiment_id:item for item in paper.experiments}
            result=[]
            for experiment_id in spec.selected_experiment_ids:
                item=by_id.get(experiment_id)
                if item is None:
                    blockers.append(self._block("target_not_found",experiment_id,"Selected paper experiment id is absent from the catalog."))
                else: result.append(item)
            return result

        bound_ids=tuple(target.paper_experiment_id for target in spec.targets if target.paper_experiment_id)
        if bound_ids:
            if len(bound_ids)!=len(spec.targets):
                blockers.append(self._block("mixed_target_binding",spec.id,"Exact paper-experiment targets cannot be mixed with legacy attribute targets."))
                return []
            by_id={item.experiment_id:item for item in paper.experiments}
            result=[]
            for experiment_id in bound_ids:
                item=by_id.get(experiment_id)
                if item is None: blockers.append(self._block("target_not_found",experiment_id,"Bound paper experiment id is absent from the catalog."))
                elif item.experiment_id not in {x.experiment_id for x in result}: result.append(item)
            return result

        # Compatibility only: legacy/custom specifications created before
        # ExperimentSelection may still use bounded catalog attributes.
        result=[]
        for target in spec.targets:
            text=" ".join(x for x in (target.experiment_name,target.description) if x)
            broad=target.target_type is TargetType.ABLATION and "all" in text.casefold()
            expected_type={TargetType.MAIN_EXPERIMENT:ExperimentType.MAIN,TargetType.ABLATION:ExperimentType.ABLATION,TargetType.BASELINE:ExperimentType.BASELINE}.get(target.target_type)
            def matches_target(x):
                if expected_type and x.experiment_type is not expected_type:return False
                if target.experiment_name and not broad and _norm(target.experiment_name) not in {_norm(x.name),_norm(x.experiment_id)}:return False
                if target.dataset and _norm(target.dataset)!=_norm(x.dataset):return False
                if target.model and _norm(target.model)!=_norm(x.model):return False
                if target.variant and _norm(target.variant)!=_norm(x.variant):return False
                if target.section and _norm(target.section) not in {_norm(v) for v in x.source_sections}:return False
                if target.table and _norm(target.table) not in {_norm(v) for v in x.source_tables}:return False
                if target.figure and _norm(target.figure) not in {_norm(v) for v in x.source_figures}:return False
                return broad or any((target.experiment_name,target.dataset,target.model,target.variant,target.section,target.table,target.figure))
            matches=[x for x in paper.experiments if matches_target(x)]
            if not matches: blockers.append(self._block("target_not_found",target.id,"Reproduction target does not resolve to a paper experiment."))
            for item in matches:
                if item.experiment_id not in {x.experiment_id for x in result}: result.append(item)
        return result

    def _parameter(self,mapping,user,policy):
        if mapping.semantic_name in user: return user[mapping.semantic_name],DecisionSource.USER,"Explicit user override has highest priority."
        status=mapping.mapping_status
        if status is ParameterMappingStatus.VALUE_CONFLICT:
            if policy is ReproductionPolicy.PAPER_FAITHFUL and mapping.paper_value is not None: return mapping.paper_value,DecisionSource.PAPER,"Paper-faithful policy selects the reported paper value."
            if policy is ReproductionPolicy.CODE_FAITHFUL and mapping.repository_value is not None: return mapping.repository_value,DecisionSource.REPOSITORY,"Code-faithful policy selects the repository value."
            return None,DecisionSource.ALIGNMENT,"Strict policy preserves unresolved conflicts."
        if status is ParameterMappingStatus.MATCHED:
            return (mapping.paper_value if mapping.paper_value is not None else mapping.repository_value),DecisionSource.MATCHED,"Paper and repository alignment agree."
        if status in (ParameterMappingStatus.REPOSITORY_ONLY,ParameterMappingStatus.SEMANTIC_MATCH_VALUE_UNKNOWN) and mapping.repository_value is not None: return mapping.repository_value,DecisionSource.REPOSITORY_FILL,"Repository provides an explicit value missing from the paper."
        if status is ParameterMappingStatus.PAPER_ONLY and mapping.paper_value is not None: return mapping.paper_value,DecisionSource.PAPER,"Only the paper supplies an explicit value."
        return None,DecisionSource.ALIGNMENT,"Alignment does not provide a defensible value."

    def _dataset(self,exp,record,mappings,datasets,overrides,unresolved):
        if not exp.dataset: return None
        mapping=mappings.get(record.dataset_mapping_id); ids=() if mapping is None else mapping.repository_dataset_ids
        if len(ids)!=1 or ids[0] not in datasets: return None
        component=datasets[ids[0]]; binding=overrides.dataset_bindings.get(exp.dataset) or overrides.dataset_bindings.get(component.component_id)
        availability=DatasetAvailability.AVAILABLE if binding else DatasetAvailability.BINDING_REQUIRED
        if not binding: unresolved.append(UnresolvedPlanItem(item_id=_id("unresolved",exp.experiment_id,"dataset"),category="dataset_binding",paper_experiment_id=exp.experiment_id,reason=f"Dataset {exp.dataset!r} requires an operator-provided binding.",candidate_ids=(component.component_id,),requires_confirmation=True))
        prep=component.details.get("preprocessing",())
        if isinstance(prep,str): prep=(prep,)
        return DatasetRequirement(name=exp.dataset,repository_dataset_id=component.component_id,binding=binding,loader_references=component.paths,preprocessing_assumptions=tuple(str(x) for x in prep),paper_evidence=mapping.paper_evidence,repository_evidence=component.evidence,availability=availability)

    def _environment(self,repo):
        deps=tuple(f"{x.name}{x.version_spec or ''}" for x in repo.dependencies); python=next((x.version_spec for x in repo.dependencies if x.name.casefold()=="python"),None)
        frameworks=tuple(x.name for x in repo.dependencies if x.name.casefold() in {"torch","pytorch","tensorflow","jax"})
        cuda=tuple(x.name+(x.version_spec or "") for x in repo.dependencies if "cuda" in x.name.casefold() or "cudnn" in x.name.casefold())
        return EnvironmentRequirement(python_constraint=python,dependencies=deps,frameworks=frameworks,cuda_hints=cuda,manifest_references=tuple(dict.fromkeys(x.source_path for x in repo.dependencies)))

    def _resources(self,env):
        return ResourceRequirement(gpu_required=True if env.cuda_hints else None,notes=("GPU count is intentionally unspecified; repository evidence does not establish it.",) if env.cuda_hints else ())

    def _block(self,code,exp,message,alignment=None,conflict=None):
        return PlanBlocker(blocker_id=_id("blocker",code,exp),code=code,message=message,severity=BlockerSeverity.BLOCKING,paper_experiment_id=exp,alignment_reference=alignment,conflict_id=conflict)

    def _order(self,ids,deps,blockers):
        waiting={x:set() for x in ids}
        for dep in deps: waiting[dep.experiment_id].update(dep.depends_on_experiment_ids)
        order=[]
        while waiting:
            ready=[x for x in ids if x in waiting and not waiting[x]]
            if not ready:
                blockers.append(self._block("dependency_cycle","plan","Experiment dependencies contain a cycle.")); return tuple(ids)
            for item in ready: order.append(item); waiting.pop(item)
            for values in waiting.values(): values.difference_update(ready)
        return tuple(order)
