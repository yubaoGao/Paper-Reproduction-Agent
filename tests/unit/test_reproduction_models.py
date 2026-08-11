import unittest

from pydantic import ValidationError

from backend.app.domain import (
    AblationDefinition,
    EvidenceReference,
    EvidenceSourceType,
    InformationStatus,
    PaperClaim,
    PaperReference,
    PaperSourceType,
    ReproductionParameter,
    ReproductionSpecification,
    ReproductionTarget,
    TargetType,
)


def make_paper() -> PaperReference:
    return PaperReference(
        id="paper-dmsf",
        title="DMSF",
        authors=("Example Author",),
        source_type=PaperSourceType.ARXIV,
        arxiv_id="2401.01234",
        source_uri="https://arxiv.org/abs/2401.01234",
    )


def paper_evidence(locator: str = "Table 2") -> EvidenceReference:
    return EvidenceReference(
        source_type=EvidenceSourceType.PAPER,
        source_id="paper-dmsf",
        locator=locator,
        confidence=0.99,
    )


def make_full_target() -> ReproductionTarget:
    return ReproductionTarget(
        id="target-full",
        target_type=TargetType.MAIN_EXPERIMENT,
        section="4.2",
        table="Table 2",
        experiment_name="MVSA-S Full Model",
        dataset="MVSA-S",
        model="DMSF",
        variant="Full Model",
    )


def make_claims() -> tuple[PaperClaim, ...]:
    return (
        PaperClaim(
            id="claim-accuracy",
            metric_name="accuracy",
            value=0.7533,
            dataset="MVSA-S",
            split="test",
            condition="Full Model",
            target_id="target-full",
            evidence=(paper_evidence(),),
        ),
        PaperClaim(
            id="claim-f1",
            metric_name="f1",
            value=0.7531,
            dataset="MVSA-S",
            split="test",
            condition="Full Model",
            target_id="target-full",
            evidence=(paper_evidence(),),
        ),
    )


class PaperReproductionModelTests(unittest.TestCase):
    def test_paper_reference_validation(self) -> None:
        paper = make_paper()
        self.assertEqual(paper.arxiv_id, "2401.01234")

        with self.assertRaises(ValidationError):
            PaperReference(
                id="bad-paper",
                title="Missing arXiv identity",
                source_type=PaperSourceType.ARXIV,
            )
        with self.assertRaises(ValidationError):
            PaperReference(
                id="bad-url",
                title="Bad URL",
                source_type=PaperSourceType.URL,
                source_uri="ftp://example.com/paper.pdf",
            )

    def test_main_experiment_target(self) -> None:
        target = make_full_target()
        self.assertEqual(target.target_type, TargetType.MAIN_EXPERIMENT)
        self.assertEqual(target.table, "Table 2")

    def test_ablation_target(self) -> None:
        target = ReproductionTarget(
            id="target-no-center-loss",
            target_type=TargetType.ABLATION,
            section="Ablation Study",
            dataset="MVSA-S",
            model="DMSF",
            variant="w/o Center Loss",
        )
        self.assertEqual(target.variant, "w/o Center Loss")

    def test_paper_claim(self) -> None:
        claim = make_claims()[0]
        self.assertEqual(claim.metric_name, "accuracy")
        self.assertEqual(claim.value, 0.7533)
        self.assertEqual(claim.evidence[0].locator, "Table 2")

    def test_evidence_reference(self) -> None:
        evidence = EvidenceReference(
            source_type=EvidenceSourceType.REPOSITORY,
            source_id="dmsf-repository",
            locator="configs/mvsa_s.yaml:learning_rate",
            text="learning_rate: 1e-5",
            confidence=0.95,
        )
        self.assertEqual(evidence.source_type, EvidenceSourceType.REPOSITORY)

        with self.assertRaises(ValidationError):
            EvidenceReference(
                source_type=EvidenceSourceType.PAPER,
                confidence=0.9,
            )

    def test_unknown_information(self) -> None:
        parameter = ReproductionParameter(
            name="weight_decay",
            value=None,
            status=InformationStatus.UNKNOWN,
        )
        self.assertIsNone(parameter.value)
        self.assertIsNone(parameter.confidence)

        with self.assertRaises(ValidationError):
            ReproductionParameter(
                name="weight_decay",
                value=0.01,
                status=InformationStatus.UNKNOWN,
            )

    def test_inferred_information_requires_confidence_and_evidence(self) -> None:
        evidence = EvidenceReference(
            source_type=EvidenceSourceType.INFERENCE,
            source_id="repository-analysis-1",
            text="Value inferred from the default training config.",
            confidence=0.85,
        )
        parameter = ReproductionParameter(
            name="learning_rate",
            value=1e-5,
            status=InformationStatus.INFERRED,
            evidence=(evidence,),
            confidence=0.85,
        )
        self.assertEqual(parameter.status, InformationStatus.INFERRED)

        with self.assertRaises(ValidationError):
            ReproductionParameter(
                name="learning_rate",
                value=1e-5,
                status=InformationStatus.INFERRED,
                evidence=(evidence,),
            )

    def test_ablation_definition_supports_removal_and_replacement(self) -> None:
        ablation = AblationDefinition(
            id="ablation-center-loss",
            name="w/o Center Loss",
            removed_components=("center_loss",),
            modified_components={
                "training_objective": "replace combined loss with classification loss"
            },
            expected_claims=("claim-ablation-center-loss",),
            target_dataset="MVSA-S",
            description="Measure the effect of center loss.",
        )
        self.assertIn("center_loss", ablation.removed_components)
        self.assertIn("training_objective", ablation.modified_components)

        with self.assertRaises(ValidationError):
            AblationDefinition(id="no-change", name="No change")

    def test_single_target_reproduction_specification(self) -> None:
        specification = ReproductionSpecification(
            id="repro-dmsf-table-2",
            paper=make_paper(),
            user_goal="复现 Table 2 中 MVSA-S 的 DMSF Full Model。",
            targets=(make_full_target(),),
            claims=make_claims(),
            parameters=(
                ReproductionParameter(
                    name="learning_rate",
                    value=1e-5,
                    status=InformationStatus.EXPLICIT,
                    evidence=(paper_evidence("Section 4.2"),),
                ),
            ),
        )
        self.assertEqual(len(specification.targets), 1)
        self.assertEqual(len(specification.claims), 2)

    def test_multi_target_reproduction_specification(self) -> None:
        ablation_names = (
            ("center-loss", "w/o Center Loss", "center_loss"),
            ("contrastive", "w/o Contrastive Loss", "contrastive_loss"),
            ("augmentation", "w/o Image Augmentation", "image_augmentation"),
        )
        targets = [make_full_target()]
        ablations = []
        for slug, name, component in ablation_names:
            targets.append(
                ReproductionTarget(
                    id=f"target-{slug}",
                    target_type=TargetType.ABLATION,
                    section="Ablation Study",
                    dataset="MVSA-S",
                    model="DMSF",
                    variant=name,
                )
            )
            ablations.append(
                AblationDefinition(
                    id=f"ablation-{slug}",
                    name=name,
                    removed_components=(component,),
                    target_dataset="MVSA-S",
                )
            )

        specification = ReproductionSpecification(
            id="repro-dmsf-full-and-ablations",
            paper=make_paper(),
            user_goal="复现完整模型以及所有 Ablation Study。",
            targets=tuple(targets),
            ablations=tuple(ablations),
        )

        self.assertEqual(len(specification.targets), 4)
        self.assertEqual(len(specification.ablations), 3)

    def test_invalid_confidence(self) -> None:
        for confidence in (-0.01, 1.01, True):
            with self.subTest(confidence=confidence), self.assertRaises(ValidationError):
                EvidenceReference(
                    source_type=EvidenceSourceType.USER,
                    source_id="user-goal",
                    confidence=confidence,
                )

    def test_invalid_or_inconsistent_claim_input(self) -> None:
        with self.assertRaises(ValidationError):
            PaperClaim(
                id="claim-no-evidence",
                metric_name="accuracy",
                value=0.75,
                dataset="MVSA-S",
                split="test",
                evidence=(),
            )
        with self.assertRaises(ValidationError):
            PaperClaim(
                id="claim-no-dataset",
                metric_name="f1",
                value=0.75,
                split="test",
                evidence=(paper_evidence(),),
            )
        with self.assertRaises(ValidationError):
            PaperClaim(
                id="claim-nan",
                metric_name="accuracy",
                value=float("nan"),
                evidence=(paper_evidence(),),
            )

    def test_unknown_internal_claim_reference_is_rejected(self) -> None:
        ablation = AblationDefinition(
            id="ablation-missing-claim",
            name="w/o module",
            removed_components=("module",),
            expected_claims=("missing-claim",),
        )
        with self.assertRaises(ValidationError):
            ReproductionSpecification(
                id="bad-references",
                paper=make_paper(),
                user_goal="Reproduce one ablation.",
                targets=(make_full_target(),),
                ablations=(ablation,),
            )


if __name__ == "__main__":
    unittest.main()
