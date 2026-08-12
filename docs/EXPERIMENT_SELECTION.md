# Experiment Selection and Reproduction Intake

Task 08A establishes the authoritative `WHICH experiments` boundary before
reproduction planning:

```text
UserReproductionGoal + PaperExperimentCatalog
  -> ReproductionGoalResolver
       deterministic intent resolution first
       catalog-bounded semantic resolution second
  -> ExperimentSelection
  -> ReproductionSpecification
       selected_experiment_ids
       ReproductionTarget.paper_experiment_id
  -> Task 08 exact-ID planning
  -> Task 09 locked single-experiment execution
  -> Task 11 digest-locked plan orchestration
```

`EXPLICIT`, `ALL_MAIN`, `ALL_ABLATIONS`, and `ALL_EXPERIMENTS` are selection
modes. Broad modes are expanded to concrete Catalog IDs during goal resolution;
downstream layers never reinterpret them. A vague request such as “复现这篇论文”
is `AMBIGUOUS`, not `ALL_EXPERIMENTS`.

Deterministic resolution recognizes explicit experiment names, dataset/model
scope, main/ablation terms, Chinese and English remove/without forms, and
explicit all modes. The semantic resolver is only a bounded fallback: every
returned experiment and metric ID is checked against the Catalog. It cannot
create experiments or turn a vague paper request into all experiments.

Catalog merge replaces extraction-model IDs with `paper-exp:<sha256-prefix>`.
The digest uses DOI/arXiv/paper identity plus normalized experiment type,
canonical dataset/model aliases, semantic model/variant/name, and structured
conditions. Evidence locator is only used to discriminate a theoretical digest
collision, so duplicate extraction evidence does not destabilize ordinary IDs.
Duplicate records merge before ID assignment; parent experiment and nested or
catalog-level claim references are remapped to the stable identities.

Legacy/custom specifications may still use bounded attribute matching. The
production path is isolated by non-empty `selected_experiment_ids`: Task 08
then performs exact lookup only, and missing selected IDs block planning rather
than falling back to names or broad matching.
