"""Cross-catalog and output validation for planning."""
from backend.app.domain import PlanStatus
from backend.app.services import PlanningValidationError

class ReproductionPlanValidator:
    def validate_inputs(self,spec,paper,repo,alignment):
        if spec.paper.id!=paper.paper.id or paper.paper.id!=alignment.paper.id: raise PlanningValidationError("paper identity mismatch")
        known={item.experiment_id for item in paper.experiments}
        if spec.selected_experiment_ids and not set(spec.selected_experiment_ids)<=known: raise PlanningValidationError("selected experiment identity is absent from paper catalog")
        if paper.catalog_id!=alignment.paper_catalog_id: raise PlanningValidationError("paper catalog identity mismatch")
        if repo.catalog_id!=alignment.repository_catalog_id: raise PlanningValidationError("repository catalog identity mismatch")
        if repo.repository.repository_id!=alignment.repository.repository_id: raise PlanningValidationError("repository identity mismatch")
        if repo.snapshot_id!=alignment.repository_snapshot_id or repo.resolved_commit_sha!=alignment.resolved_commit_sha: raise PlanningValidationError("repository snapshot mismatch")

    def validate(self,plan,paper,repo,alignment,*,specification=None):
        production_ids=set(specification.selected_experiment_ids) if specification is not None else set()
        if production_ids and tuple(plan.target_experiment_ids)!=tuple(specification.selected_experiment_ids):raise PlanningValidationError("execution plan changes the authoritative experiment selection")
        entrypoints={x.entrypoint_id for x in repo.entrypoints}; configs={x.config_id for x in repo.configurations}; commands={x.command_id for x in repo.commands}
        decisions={x.decision_id for x in plan.decisions}; claims={x.id for x in paper.paper_claims}|{c.id for x in paper.experiments for c in x.claims}
        alignment_ids={x.alignment_id for group in (alignment.experiment_alignments,alignment.dataset_mappings,alignment.model_mappings,alignment.parameter_mappings,alignment.ablation_mappings,alignment.metric_mappings,alignment.evaluation_policy_alignments) for x in group}
        conflict_ids={x.conflict_id for x in alignment.conflicts}; paper_experiments={x.experiment_id:x for x in paper.experiments}; decision_records={x.decision_id:x for x in plan.decisions}
        for decision in plan.decisions:
            if decision.alignment_reference and decision.alignment_reference not in alignment_ids: raise PlanningValidationError("dangling decision alignment reference")
        for blocker in plan.blockers:
            if blocker.alignment_reference and blocker.alignment_reference not in alignment_ids: raise PlanningValidationError("dangling blocker alignment reference")
            if blocker.conflict_id and blocker.conflict_id not in conflict_ids: raise PlanningValidationError("dangling blocker conflict reference")
        for exp in plan.experiments:
            cmd=exp.resolved_command
            if cmd is None: raise PlanningValidationError("planned experiment requires a structured command")
            if cmd.entrypoint_id not in entrypoints: raise PlanningValidationError("unknown planned entrypoint")
            if not set(cmd.config_ids)<=configs: raise PlanningValidationError("unknown planned config")
            if cmd.command_reference_id and cmd.command_reference_id not in commands: raise PlanningValidationError("unknown command provenance")
            if not set(exp.provenance_decision_ids)<=decisions: raise PlanningValidationError("unknown planning decision provenance")
            if not set(exp.expected_claim_ids)<=claims: raise PlanningValidationError("unknown expected claim")
            paper_exp=paper_experiments.get(exp.metadata.get("paper_experiment_id"))
            if paper_exp is None: raise PlanningValidationError("planned experiment lacks a paper experiment reference")
            if paper_exp.dataset and exp.dataset_requirement is None: raise PlanningValidationError("dataset requirement is missing")
            semantic_keys={decision_records[x].semantic_key for x in exp.provenance_decision_ids}
            applied={item.name:item for item in cmd.applied_parameters}
            for key in exp.hyperparameters:
                if f"parameter:{key}" not in semantic_keys and f"ablation:{key}" not in semantic_keys: raise PlanningValidationError("hyperparameter lacks a planning decision")
            ablation_keys={x.split(":",1)[1] for x in semantic_keys if x.startswith("ablation:")}
            if exp.task_type.value=="ablation" and not ablation_keys: raise PlanningValidationError("ablation implementation lacks provenance")
            if any(key not in applied or applied[key].value!=exp.hyperparameters.get(key) for key in ablation_keys):raise PlanningValidationError("ablation value is not materialized in the executable command")
            if exp.metadata.get("paper_experiment_id") in production_ids:
                if exp.evaluation_policy is None or not exp.evaluation_policy.is_resolved or exp.action_plan is None:raise PlanningValidationError("selected production experiment lacks resolved final-result action plan")
        accounted={x.metadata.get("paper_experiment_id") for x in plan.experiments}|{x.paper_experiment_id for x in plan.blockers}|{x.paper_experiment_id for x in plan.unresolved_items}
        if not set(plan.target_experiment_ids)<=accounted: raise PlanningValidationError("not every target is accounted for")
        if plan.status is PlanStatus.READY and len(plan.experiments)!=len(plan.target_experiment_ids): raise PlanningValidationError("ready plan must specify every target")
        if plan.policy.value=="strict":
            unresolved={x.conflict_id for x in alignment.conflicts if x.status.value=="unresolved" and x.conflict_type.value!="evaluation_policy_conflict"}
            relevant={x for record in alignment.experiment_alignments if record.paper_experiment_id in plan.target_experiment_ids for x in record.conflict_ids}
            blocked={x.conflict_id for x in plan.blockers if x.conflict_id}
            if unresolved&relevant-blocked: raise PlanningValidationError("strict plan silently ignores an unresolved conflict")
        return plan
