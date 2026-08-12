"""Deterministic, paper-scoped experiment identity and reference remapping."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import OrderedDict

from backend.app.domain import CatalogEntity, PaperClaim, PaperExperimentRecord, PaperReference


class StableExperimentIdentityError(ValueError):
    pass


def normalize_experiment_identity(value: str | None) -> str:
    normalized = unicodedata.normalize("NFKC", value or "").casefold()
    return re.sub(r"[^\w]+", "", normalized, flags=re.UNICODE)


class StableExperimentIdentityGenerator:
    """Merge duplicate semantic records and replace model-provided IDs.

    The identity digest is based on paper identity and normalized scientific
    semantics. Evidence locators are only collision discriminators, so the
    same experiment remains stable when duplicate extraction stages cite
    different supporting locations.
    """

    def assign(
        self,
        paper: PaperReference,
        records: tuple[PaperExperimentRecord, ...] | list[PaperExperimentRecord],
        *,
        datasets: tuple[CatalogEntity, ...] | list[CatalogEntity] = (),
        models: tuple[CatalogEntity, ...] | list[CatalogEntity] = (),
    ) -> tuple[tuple[PaperExperimentRecord, ...], dict[str, str]]:
        dataset_aliases = self._aliases(datasets)
        model_aliases = self._aliases(models)
        groups: OrderedDict[tuple[str, ...], list[PaperExperimentRecord]] = OrderedDict()
        old_id_groups: dict[str, set[tuple[str, ...]]] = {}
        for record in records:
            key = self.semantic_key(record, dataset_aliases, model_aliases)
            groups.setdefault(key, []).append(record)
            old_id_groups.setdefault(record.experiment_id, set()).add(key)

        ambiguous_old_ids = [value for value, keys in old_id_groups.items() if len(keys) > 1]
        if ambiguous_old_ids:
            raise StableExperimentIdentityError(
                "one extracted experiment id refers to multiple semantic experiments: "
                + ", ".join(sorted(ambiguous_old_ids))
            )

        paper_key = self.paper_identity(paper)
        assigned: dict[tuple[str, ...], str] = {}
        used: dict[str, tuple[str, ...]] = {}
        merged_by_key: dict[tuple[str, ...], PaperExperimentRecord] = {}
        for key, values in groups.items():
            merged = self._merge(values)
            stable_id = self._stable_id(paper_key, key)
            if stable_id in used and used[stable_id] != key:
                stable_id = self._collision_id(paper_key, key, merged)
            if stable_id in used and used[stable_id] != key:
                raise StableExperimentIdentityError("stable experiment identity collision")
            used[stable_id] = key
            assigned[key] = stable_id
            merged_by_key[key] = merged

        references = {
            old_id: assigned[next(iter(keys))]
            for old_id, keys in old_id_groups.items()
        }
        output = []
        for key, merged in merged_by_key.items():
            stable_id = assigned[key]
            parent = merged.parent_experiment_id
            if parent is not None:
                parent = references.get(parent, parent)
            claims = tuple(
                claim.model_copy(
                    update={"target_id": references.get(claim.target_id, stable_id)}
                )
                for claim in merged.claims
            )
            output.append(
                merged.model_copy(
                    update={
                        "experiment_id": stable_id,
                        "parent_experiment_id": parent,
                        "claims": claims,
                    }
                )
            )
        return tuple(output), references

    @staticmethod
    def remap_claims(
        claims: tuple[PaperClaim, ...] | list[PaperClaim],
        references: dict[str, str],
    ) -> tuple[PaperClaim, ...]:
        return tuple(
            claim.model_copy(update={"target_id": references.get(claim.target_id, claim.target_id)})
            for claim in claims
        )

    @staticmethod
    def paper_identity(paper: PaperReference) -> str:
        if paper.doi:
            return f"doi:{normalize_experiment_identity(paper.doi)}"
        if paper.arxiv_id:
            return f"arxiv:{normalize_experiment_identity(paper.arxiv_id)}"
        return f"paper:{normalize_experiment_identity(paper.id)}"

    @staticmethod
    def semantic_key(
        record: PaperExperimentRecord,
        dataset_aliases: dict[str, str] | None = None,
        model_aliases: dict[str, str] | None = None,
    ) -> tuple[str, ...]:
        dataset_aliases = dataset_aliases or {}
        model_aliases = model_aliases or {}
        conditions = json.dumps(
            record.conditions,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        semantic_name = record.variant or record.name
        dataset = normalize_experiment_identity(record.dataset)
        model = normalize_experiment_identity(record.model)
        semantic = normalize_experiment_identity(semantic_name)
        return (
            record.experiment_type.value,
            dataset_aliases.get(dataset, dataset),
            model_aliases.get(model, model),
            model_aliases.get(semantic, semantic),
            normalize_experiment_identity(conditions),
        )

    @staticmethod
    def _aliases(entities) -> dict[str, str]:
        result = {}
        for entity in entities:
            canonical = normalize_experiment_identity(entity.canonical_name)
            for value in (entity.canonical_name, *entity.aliases):
                result[normalize_experiment_identity(value)] = canonical
        return result

    @staticmethod
    def _stable_id(paper_key: str, key: tuple[str, ...]) -> str:
        payload = json.dumps((paper_key, key), ensure_ascii=False, separators=(",", ":"))
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]
        return f"paper-exp:{digest}"

    @staticmethod
    def _collision_id(
        paper_key: str,
        key: tuple[str, ...],
        record: PaperExperimentRecord,
    ) -> str:
        locators = sorted(
            value
            for value in (
                *(item.locator for item in record.evidence),
                *record.source_sections,
                *record.source_tables,
                *record.source_figures,
            )
            if value
        )
        payload = json.dumps(
            (paper_key, key, locators[0] if locators else ""),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return f"paper-exp:{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"

    @staticmethod
    def _merge(values: list[PaperExperimentRecord]) -> PaperExperimentRecord:
        base = values[0]
        claims = _unique(item for value in values for item in value.claims)
        evidence = _unique(item for value in values for item in value.evidence)
        parameters = _unique_parameters(item for value in values for item in value.parameters)
        return base.model_copy(
            update={
                "claims": claims,
                "evidence": evidence,
                "parameters": parameters,
                "source_sections": _strings(value for item in values for value in item.source_sections),
                "source_tables": _strings(value for item in values for value in item.source_tables),
                "source_figures": _strings(value for item in values for value in item.source_figures),
            }
        )


def _unique(items) -> tuple:
    seen = set()
    result = []
    for item in items:
        key = item.model_dump_json() if hasattr(item, "model_dump_json") else str(item)
        if key not in seen:
            seen.add(key)
            result.append(item)
    return tuple(result)


def _unique_parameters(items) -> tuple:
    result = {}
    for item in items:
        key = item.name.casefold()
        if key not in result:
            result[key] = item
        elif result[key].value == item.value and result[key].status == item.status:
            result[key] = result[key].model_copy(
                update={"evidence": _unique((*result[key].evidence, *item.evidence))}
            )
    return tuple(result.values())


def _strings(items) -> tuple[str, ...]:
    return tuple(dict.fromkeys(items))
