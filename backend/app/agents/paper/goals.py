"""Catalog-bounded deterministic and semantic reproduction goal resolution."""

from __future__ import annotations

import json
import re
import unicodedata

from backend.app.domain import (
    AblationDefinition,
    ExperimentSelection,
    ExperimentType,
    GoalResolutionResult,
    GoalResolutionStatus,
    PaperExperimentCatalog,
    ReproductionSpecification,
    ReproductionTarget,
    SelectionMode,
    TargetType,
    UserReproductionGoal,
)
from backend.app.llm import LLMRole, LLMRouter

from .prompt_registry import PromptRegistry
from .schemas import GoalSemanticSelection


class GoalResolutionError(RuntimeError):
    pass


class ReproductionGoalResolver:
    """Resolve WHICH experiments without adding unrequested catalog records."""

    _ALL_EXPERIMENTS = (
        "all experiments",
        "all of the experiments",
        "complete reproduction",
        "fully reproduce",
        "全部实验",
        "所有实验",
        "完整复现",
        "完全复现",
    )
    _ALL_ABLATIONS = (
        "all ablations",
        "all ablation",
        "all ablation experiments",
        "全部消融",
        "所有消融",
    )
    _ALL_MAIN = (
        "all main",
        "all main experiments",
        "all main experiment",
        "全部主实验",
        "所有主实验",
        "全部主要实验",
        "所有主要实验",
    )
    _VAGUE = {
        "复现这篇论文",
        "复现论文",
        "复现实验",
        "reproduce this paper",
        "reproduce the paper",
        "reproduce experiments",
        "reproduce the experiments",
    }

    def __init__(
        self,
        router: LLMRouter | None = None,
        prompts: PromptRegistry | None = None,
    ) -> None:
        self.router = router
        self.prompts = prompts or PromptRegistry()

    def resolve(
        self,
        catalog: PaperExperimentCatalog,
        goal: UserReproductionGoal,
    ) -> GoalResolutionResult:
        records = tuple(catalog.experiments)
        text = _normalize_text(goal.text)
        if not records:
            return self._not_found(goal, SelectionMode.EXPLICIT, "Catalog 中没有实验记录")

        if _contains_any(text, self._ALL_EXPERIMENTS):
            return self._resolved(
                catalog,
                goal,
                SelectionMode.ALL_EXPERIMENTS,
                records,
                (),
                "用户明确要求复现全部论文实验",
            )
        if _contains_any(text, self._ALL_ABLATIONS):
            selected = tuple(x for x in records if x.experiment_type is ExperimentType.ABLATION)
            return self._resolved_or_not_found(
                catalog,
                goal,
                SelectionMode.ALL_ABLATIONS,
                selected,
                "用户明确要求复现所有消融实验",
            )
        if _contains_any(text, self._ALL_MAIN):
            selected = tuple(x for x in records if x.experiment_type is ExperimentType.MAIN)
            return self._resolved_or_not_found(
                catalog,
                goal,
                SelectionMode.ALL_MAIN,
                selected,
                "用户明确要求复现所有主实验",
            )

        if _contains_any(text, self._VAGUE) and not self._has_explicit_scope(catalog, text):
            return self._ambiguous(
                goal,
                SelectionMode.EXPLICIT,
                records,
                "目标没有限定具体论文实验；普通论文复现请求不等于全部实验",
                (goal.text,),
            )

        deterministic = self._deterministic(catalog, text)
        if deterministic["resolved"]:
            return self._resolved(
                catalog,
                goal,
                SelectionMode.EXPLICIT,
                deterministic["records"],
                deterministic["metrics"],
                deterministic["reason"],
            )

        if self.router is not None:
            semantic = self._semantic(catalog, goal)
            semantic_result = self._semantic_result(
                catalog,
                goal,
                semantic,
                deterministic["records"],
                deterministic["unresolved"],
            )
            if semantic_result is not None:
                return semantic_result

        candidates = deterministic["records"]
        if deterministic["ambiguous"] and candidates:
            return self._ambiguous(
                goal,
                SelectionMode.EXPLICIT,
                candidates,
                deterministic["reason"],
                deterministic["unresolved"],
            )
        return self._not_found(
            goal,
            SelectionMode.EXPLICIT,
            deterministic["reason"] or "Catalog 中没有匹配的实验",
            deterministic["unresolved"],
        )

    def resolve_from_ids(
        self,
        catalog: PaperExperimentCatalog,
        goal: UserReproductionGoal,
        experiment_ids: tuple[str, ...],
    ) -> GoalResolutionResult:
        known = {record.experiment_id: record for record in catalog.experiments}
        missing = tuple(item for item in experiment_ids if item not in known)
        if missing:
            return self._not_found(
                goal,
                SelectionMode.EXPLICIT,
                "指定的实验不在 PaperExperimentCatalog 中",
                missing,
            )
        if not experiment_ids:
            return self._not_found(goal, SelectionMode.EXPLICIT, "没有指定实验")
        records = tuple(known[item] for item in dict.fromkeys(experiment_ids))
        return self._resolved(
            catalog,
            goal,
            SelectionMode.EXPLICIT,
            records,
            (),
            "用户明确指定了要复现的实验 ID",
        )

    @staticmethod
    def _has_explicit_scope(catalog, text):
        if _contains_any(
            text,
            (
                "main experiment",
                "主实验",
                "主要实验",
                "full model",
                "完整模型",
                "ablation",
                "消融",
                "without",
                "remove",
                "去掉",
                "移除",
                "删除",
                "不含",
            ),
        ):
            return True
        if re.search(r"\btable\s*[\w.-]+", text, re.IGNORECASE):
            return True
        entities = (*catalog.datasets, *catalog.model_variants)
        if any(
            _phrase_in_text(name, text)
            for entity in entities
            for name in (entity.canonical_name, *entity.aliases)
        ):
            return True
        return any(_record_mentioned(record, text) for record in catalog.experiments)

    def _deterministic(self, catalog: PaperExperimentCatalog, text: str) -> dict:
        records = tuple(catalog.experiments)
        pool = records
        evidence_of_scope = False

        dataset_names = set()
        for entity in catalog.datasets:
            names = (entity.canonical_name, *entity.aliases)
            if any(_phrase_in_text(name, text) for name in names):
                dataset_names.update(_compact(name) for name in names)
        if dataset_names:
            evidence_of_scope = True
            pool = tuple(x for x in pool if x.dataset and _compact(x.dataset) in dataset_names)

        mentioned_models = {
            _compact(record.model)
            for record in records
            if record.model and _phrase_in_text(record.model, text)
        }
        if mentioned_models:
            evidence_of_scope = True
            pool = tuple(x for x in pool if x.model and _compact(x.model) in mentioned_models)

        table_match = re.search(r"\btable\s*([\w.-]+)", text, re.IGNORECASE)
        if table_match:
            evidence_of_scope = True
            table = _compact(table_match.group(1))
            pool = tuple(
                x
                for x in pool
                if any(_compact(value).removeprefix("table") == table for value in x.source_tables)
            )

        named = tuple(x for x in pool if _record_mentioned(x, text))
        main_requested = _contains_any(
            text,
            ("main experiment", "main experiments", "主实验", "主要实验", "full model", "完整模型"),
        )
        ablation_requested = _contains_any(
            text,
            ("ablation", "without", "remove", "w/o", "消融", "去掉", "移除", "删除", "不含"),
        )
        metrics = tuple(
            dict.fromkeys(
                claim.metric_name
                for claim in catalog.paper_claims
                if _phrase_in_text(claim.metric_name, text)
            )
        )

        selected = list(named)
        if main_requested:
            for record in pool:
                if record.experiment_type is ExperimentType.MAIN and record not in selected:
                    selected.append(record)

        unresolved = []
        if ablation_requested:
            named_ablations = [
                record for record in selected if record.experiment_type is ExperimentType.ABLATION
            ]
            ablation_candidates = tuple(
                record for record in pool if record.experiment_type is ExperimentType.ABLATION
            )
            if not named_ablations:
                if len(ablation_candidates) == 1:
                    selected.append(ablation_candidates[0])
                elif ablation_candidates:
                    unresolved.append("具体消融实验")
                    return {
                        "resolved": False,
                        "ambiguous": True,
                        "records": ablation_candidates,
                        "metrics": metrics,
                        "unresolved": tuple(unresolved),
                        "reason": "用户提到消融实验，但未唯一指定 Catalog 中的消融变体",
                    }
                else:
                    unresolved.append("消融实验")

        selected = list(dict.fromkeys(record.experiment_id for record in selected))
        selected_records = tuple(
            record for record in records if record.experiment_id in set(selected)
        )

        if selected_records:
            if main_requested:
                main_count = sum(x.experiment_type is ExperimentType.MAIN for x in selected_records)
                if main_count > 1:
                    return {
                        "resolved": False,
                        "ambiguous": True,
                        "records": tuple(
                            x for x in selected_records if x.experiment_type is ExperimentType.MAIN
                        ),
                        "metrics": metrics,
                        "unresolved": ("具体主实验",),
                        "reason": "多个主实验满足目标，用户没有明确要求所有主实验",
                    }
            if unresolved:
                return {
                    "resolved": False,
                    "ambiguous": False,
                    "records": selected_records,
                    "metrics": metrics,
                    "unresolved": tuple(unresolved),
                    "reason": "目标中的部分实验无法确定",
                }
            return {
                "resolved": True,
                "ambiguous": False,
                "records": selected_records,
                "metrics": metrics,
                "unresolved": (),
                "reason": "根据用户明确给出的实验类型、数据集、模型或实验变体精确匹配",
            }

        if evidence_of_scope or metrics:
            candidates = pool
            if metrics:
                metric_ids = {
                    claim.target_id
                    for claim in catalog.paper_claims
                    if claim.metric_name in metrics and claim.target_id is not None
                }
                candidates = tuple(x for x in candidates if x.experiment_id in metric_ids)
            if len(candidates) == 1:
                return {
                    "resolved": True,
                    "ambiguous": False,
                    "records": candidates,
                    "metrics": metrics,
                    "unresolved": (),
                    "reason": "用户提供的 Catalog 限定条件唯一匹配一个实验",
                }
            if candidates:
                return {
                    "resolved": False,
                    "ambiguous": True,
                    "records": candidates,
                    "metrics": metrics,
                    "unresolved": ("具体实验",),
                    "reason": "用户提供的限定条件匹配多个论文实验",
                }

        return {
            "resolved": False,
            "ambiguous": False,
            "records": (),
            "metrics": metrics,
            "unresolved": (goal_fragment(text),),
            "reason": "Catalog 中没有可由确定性规则确认的实验",
        }

    def _semantic(self, catalog, goal):
        prompt = self.prompts.get("goal_resolution")
        summary = [
            {
                "id": x.experiment_id,
                "name": x.name,
                "type": x.experiment_type.value,
                "dataset": x.dataset,
                "model": x.model,
                "variant": x.variant,
                "metrics": [c.metric_name for c in x.claims],
            }
            for x in catalog.experiments
        ]
        return self.router.for_role(LLMRole.PRIMARY).generate_structured(
            role=LLMRole.PRIMARY,
            system_prompt=prompt.system,
            content=(
                f"{prompt.task}\nUSER GOAL: {goal.text}\n"
                f"CATALOG: {json.dumps(summary, ensure_ascii=False)}"
            ),
            output_schema=GoalSemanticSelection,
            prompt_name=prompt.name,
            prompt_version=prompt.version,
        ).value

    def _semantic_result(self, catalog, goal, semantic, candidates, unresolved):
        known = {x.experiment_id for x in catalog.experiments}
        experiment_ids = tuple(dict.fromkeys(semantic.experiment_ids))
        if not set(experiment_ids).issubset(known):
            raise GoalResolutionError("semantic resolver returned unknown experiment ids")
        known_metrics = {claim.metric_name for claim in catalog.paper_claims}
        if not set(semantic.metric_names).issubset(known_metrics):
            raise GoalResolutionError("semantic resolver returned unknown metric names")
        if semantic.ambiguous:
            ambiguous_records = tuple(
                x
                for x in catalog.experiments
                if x.experiment_id in (set(experiment_ids) or {y.experiment_id for y in candidates})
            )
            if not ambiguous_records:
                ambiguous_records = tuple(catalog.experiments)
            return self._ambiguous(
                goal,
                SelectionMode.EXPLICIT,
                ambiguous_records,
                semantic.reason or "语义目标存在歧义",
                unresolved or (goal.text,),
                semantic.clarification_questions,
            )
        if not experiment_ids:
            return None
        selected = tuple(x for x in catalog.experiments if x.experiment_id in set(experiment_ids))
        return self._resolved(
            catalog,
            goal,
            SelectionMode.EXPLICIT,
            selected,
            semantic.metric_names,
            semantic.reason or "语义解析从 Catalog 有界候选中选择了明确实验",
        )

    def _resolved_or_not_found(self, catalog, goal, mode, records, reason):
        if records:
            return self._resolved(catalog, goal, mode, records, (), reason)
        return self._not_found(goal, mode, "Catalog 中没有该类型的实验")

    def _resolved(self, catalog, goal, mode, records, metrics, reason):
        ids = tuple(x.experiment_id for x in records)
        selection = ExperimentSelection(
            selection_mode=mode,
            selected_experiment_ids=ids,
            original_user_goal=goal.text,
            selection_reason=reason,
            per_experiment_reasons={
                value: f"{reason}：{record.name}"
                for value, record in zip(ids, records)
            },
            resolution_status=GoalResolutionStatus.RESOLVED,
        )
        specification = self._specification(catalog, goal, records, metrics, selection)
        return GoalResolutionResult(
            status=GoalResolutionStatus.RESOLVED,
            selection=selection,
            specification=specification,
            reason=reason,
        )

    @staticmethod
    def _ambiguous(
        goal,
        mode,
        records,
        reason,
        unresolved=(),
        questions=(),
    ):
        questions = questions or ("请指定数据集、表格、实验类型或模型变体。",)
        selection = ExperimentSelection(
            selection_mode=mode,
            original_user_goal=goal.text,
            selection_reason=reason,
            unresolved_mentions=tuple(unresolved) or (goal.text,),
            resolution_status=GoalResolutionStatus.AMBIGUOUS,
            clarification_questions=tuple(questions),
        )
        return GoalResolutionResult(
            status=GoalResolutionStatus.AMBIGUOUS,
            selection=selection,
            candidate_experiment_ids=tuple(x.experiment_id for x in records),
            reason=reason,
            clarification_questions=tuple(questions),
        )

    @staticmethod
    def _not_found(goal, mode, reason, unresolved=()):
        selection = ExperimentSelection(
            selection_mode=mode,
            original_user_goal=goal.text,
            selection_reason=reason,
            unresolved_mentions=tuple(unresolved) or (goal.text,),
            resolution_status=GoalResolutionStatus.NOT_FOUND,
        )
        return GoalResolutionResult(
            status=GoalResolutionStatus.NOT_FOUND,
            selection=selection,
            reason=reason,
        )

    @staticmethod
    def _specification(catalog, goal, records, metrics, selection):
        target_type = {
            ExperimentType.MAIN: TargetType.MAIN_EXPERIMENT,
            ExperimentType.ABLATION: TargetType.ABLATION,
            ExperimentType.BASELINE: TargetType.BASELINE,
        }
        targets = tuple(
            ReproductionTarget(
                id=x.experiment_id,
                paper_experiment_id=x.experiment_id,
                target_type=target_type.get(x.experiment_type, TargetType.CUSTOM),
                section=x.source_sections[0] if x.source_sections else None,
                table=f"Table {x.source_tables[0]}" if x.source_tables else None,
                figure=f"Figure {x.source_figures[0]}" if x.source_figures else None,
                experiment_name=x.name,
                dataset=x.dataset,
                model=x.model,
                variant=x.variant,
                description=f"Catalog experiment {x.experiment_id}",
            )
            for x in records
        )
        ids = set(selection.selected_experiment_ids)
        claims = tuple(
            x
            for x in catalog.paper_claims
            if (x.target_id in ids or x.target_id is None)
            and (not metrics or x.metric_name in metrics)
        )
        ablations = tuple(
            AblationDefinition(
                id=f"ablation:{x.experiment_id}",
                name=x.variant or x.name,
                modified_components={"variant": x.variant or x.name},
                expected_claims=tuple(c.id for c in claims if c.target_id == x.experiment_id),
                target_dataset=x.dataset,
                description=f"Catalog ablation {x.experiment_id}",
            )
            for x in records
            if x.experiment_type is ExperimentType.ABLATION
        )
        parameters = []
        for record in records:
            parameters.extend(record.parameters)
        parameters.extend(catalog.training_parameters)
        parameters.extend(catalog.evaluation_parameters)
        unique = {x.name.casefold(): x for x in parameters}
        return ReproductionSpecification(
            id=f"repro:{goal.goal_id}",
            paper=catalog.paper,
            user_goal=goal.text,
            targets=targets,
            selected_experiment_ids=selection.selected_experiment_ids,
            claims=claims,
            ablations=ablations,
            parameters=tuple(unique.values()),
            metadata={
                "selection_mode": selection.selection_mode.value,
                "selection_reason": selection.selection_reason,
            },
        )


def _normalize_text(value: str) -> str:
    text = unicodedata.normalize("NFKC", value).casefold()
    text = re.sub(r"\bw\s*/\s*o\b", " without ", text)
    return re.sub(r"\s+", " ", text).strip()


def _compact(value: str | None) -> str:
    return re.sub(r"[^\w]+", "", _normalize_text(value or ""), flags=re.UNICODE)


def _phrase_in_text(phrase: str, text: str) -> bool:
    return bool(phrase) and _compact(phrase) in _compact(text)


def _contains_any(text: str, phrases) -> bool:
    return any(_phrase_in_text(value, text) for value in phrases)


def _ablation_core(value: str) -> str:
    text = _normalize_text(value)
    for phrase in (
        "ablation experiment",
        "ablation",
        "experiment",
        "without",
        "removed",
        "removing",
        "remove",
        "variant",
        "消融实验",
        "消融",
        "去掉",
        "移除",
        "删除",
        "不含",
        "实验",
        "的",
    ):
        text = text.replace(phrase, " ")
    return _compact(text)


def _record_mentioned(record, text: str) -> bool:
    for value in (record.name, record.variant):
        if not value:
            continue
        if _phrase_in_text(value, text):
            return True
        core = _ablation_core(value)
        if record.experiment_type is ExperimentType.ABLATION and len(core) >= 3:
            if core in _ablation_core(text):
                return True
    return False


def goal_fragment(text: str) -> str:
    return text[:200] or "未识别目标"
