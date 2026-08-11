# 论文复现任务规范

## 1. 这一层解决什么问题

Task 03 定义论文理解与可执行实验之间的语义层：用户想复现论文中的什么结果、
论文声称了什么数值、哪些实验细节已知、哪些仍需推断，以及一个任务包含哪些
完整模型或消融目标。

```mermaid
flowchart LR
    Goal[User Goal] --> Repro[ReproductionSpecification]
    Repro --> Targets[ReproductionTarget(s)]
    Targets --> Planner[Future Reproduction Planner]
    Planner --> Experiments[ExperimentSpecification(s)]
    Experiments --> Runs[ExperimentRun(s)]
```

当前只建立领域模型。模型不会读取 PDF、调用 LLM、分析仓库、克隆 Git、生成
`ExperimentSpecification` 或启动实验。

## 2. 核心模型

| 模型 | 职责 |
|---|---|
| `PaperReference` | 标识论文和来源位置，不读取论文内容 |
| `ReproductionTarget` | 描述用户指定的章节、表格、图、数据集、模型或实验变体 |
| `PaperClaim` | 保存论文声明的结构化数值及证据 |
| `EvidenceReference` | 记录信息来自用户、论文、仓库、数据集、推断或系统，以及定位、原文和可信度 |
| `ReproductionParameter` | 只包装需要来源追踪的关键复现参数，并区分明确、推断和未知 |
| `AblationDefinition` | 描述消融条件中被删除或修改的组件，并引用预期论文声明 |
| `ReproductionSpecification` | 聚合一篇论文、用户目标、一个或多个复现目标、声明、消融和约束 |

### PaperReference

支持 `PDF_UPLOAD`、`ARXIV`、`URL`、`LOCAL_FILE`。例如 arXiv 来源必须有
`arxiv_id`；URL 来源必须有 HTTP(S) `source_uri`。这只是引用，不会检查 URI
是否存在，也不会打开本地文件。

### ReproductionTarget

目标类型保持为 `MAIN_EXPERIMENT`、`ABLATION`、`BASELINE`、`CUSTOM`。
没有增加“全部消融”类型：用户要求全部消融时，用多个 `ABLATION` target 表达，
这样每个目标可以独立生成实验。只提供 `table="Table 2"` 也是合法目标，其他定位
字段可在后续论文理解阶段补全。

### PaperClaim 与 Metric

| | `PaperClaim` | `Metric` |
|---|---|---|
| 语义 | 论文声称的结果 | `ExperimentRun` 实际观测结果 |
| 时间 | 执行前，从论文语义中获得 | 执行过程中或结束后产生 |
| 来源 | 必须携带 `EvidenceReference` | 携带 run 中的 step/split/metadata |
| 用途 | 未来 Result Comparator 的期望侧 | 未来 Result Comparator 的实际侧 |

Task 02 的 `MetricExpectation` 仍属于可执行实验定义。未来 Planner 可以把经确认的
`PaperClaim` 转为一个或多个 `MetricExpectation`，但 Task 03 不实现该转换。

## 3. Evidence 与 Unknown / Inferred

`EvidenceReference` 是最小来源记录：

```python
EvidenceReference(
    source_type=EvidenceSourceType.PAPER,
    source_id="paper-dmsf",
    locator="Section 4.2",
    text="The learning rate is set to 1e-5.",
    confidence=0.98,
)
```

系统不会给所有字符串加 wrapper。只有 learning rate、weight decay、预处理方式等
真正影响复现且需要审计的值使用 `ReproductionParameter`：

```python
explicit_learning_rate = ReproductionParameter(
    name="learning_rate",
    value=1e-5,
    status=InformationStatus.EXPLICIT,
    evidence=(paper_section_evidence,),
)

inferred_batch_size = ReproductionParameter(
    name="batch_size",
    value=32,
    status=InformationStatus.INFERRED,
    evidence=(repository_config_evidence,),
    confidence=0.85,
)

unknown_weight_decay = ReproductionParameter(
    name="weight_decay",
    value=None,
    status=InformationStatus.UNKNOWN,
)
```

约束规则：

- `EXPLICIT`：必须有值和证据。
- `INFERRED`：必须有值、证据和 `0..1` 可信度，不能伪装成论文事实。
- `UNKNOWN`：值和可信度必须为空，后续可由仓库分析或用户补充。

## 4. DMSF / MVSA-S 完整示例

目标：复现 DMSF 论文 Table 2 中 MVSA-S Full Model，并验证
Accuracy `0.7533` 与 F1 `0.7531`。

```python
from backend.app.domain import (
    EvidenceReference,
    EvidenceSourceType,
    PaperClaim,
    PaperReference,
    PaperSourceType,
    ReproductionSpecification,
    ReproductionTarget,
    TargetType,
)

paper = PaperReference(
    id="paper-dmsf",
    title="DMSF",
    authors=("Example Author",),
    source_type=PaperSourceType.ARXIV,
    arxiv_id="2401.01234",
    source_uri="https://arxiv.org/abs/2401.01234",
)

target = ReproductionTarget(
    id="target-full",
    target_type=TargetType.MAIN_EXPERIMENT,
    section="4.2",
    table="Table 2",
    experiment_name="MVSA-S Full Model",
    dataset="MVSA-S",
    model="DMSF",
    variant="Full Model",
)

table_evidence = EvidenceReference(
    source_type=EvidenceSourceType.PAPER,
    source_id=paper.id,
    locator="Table 2",
    confidence=0.99,
)

specification = ReproductionSpecification(
    id="repro-dmsf-table-2",
    paper=paper,
    user_goal="复现 Table 2 中 MVSA-S 的 DMSF Full Model，并验证 Accuracy 和 F1。",
    targets=(target,),
    claims=(
        PaperClaim(
            id="claim-accuracy",
            metric_name="accuracy",
            value=0.7533,
            dataset="MVSA-S",
            split="test",
            condition="Full Model",
            target_id=target.id,
            evidence=(table_evidence,),
        ),
        PaperClaim(
            id="claim-f1",
            metric_name="f1",
            value=0.7531,
            dataset="MVSA-S",
            split="test",
            condition="Full Model",
            target_id=target.id,
            evidence=(table_evidence,),
        ),
    ),
)
```

## 5. Full Model + 三个 Ablation

同一个 `ReproductionSpecification` 可以包含：

```text
target-full                 Full Model
target-center-loss          w/o Center Loss
target-contrastive-loss     w/o Contrastive Loss
target-image-augmentation   w/o Image Augmentation
```

对应三个 `AblationDefinition`：

```python
ablations = (
    AblationDefinition(
        id="ablation-center-loss",
        name="w/o Center Loss",
        removed_components=("center_loss",),
        target_dataset="MVSA-S",
    ),
    AblationDefinition(
        id="ablation-contrastive-loss",
        name="w/o Contrastive Loss",
        removed_components=("contrastive_loss",),
        target_dataset="MVSA-S",
    ),
    AblationDefinition(
        id="ablation-image-augmentation",
        name="w/o Image Augmentation",
        removed_components=("image_augmentation",),
        target_dataset="MVSA-S",
    ),
)
```

并非所有消融都是 remove-one-component。`modified_components` 是“组件/参数名 →
修改说明”的映射，可表达 loss replacement、module replacement、parameter
replacement 或 view removal，例如：

```python
modified_components={
    "training_objective": "replace center loss with cross-entropy only",
    "text_view": "replace multimodal text view with image-only input",
}
```

`expected_claims` 保存 `PaperClaim.id` 引用，避免在消融定义中复制声明对象；
`ReproductionSpecification` 会校验这些引用存在。

## 6. 与 ExperimentSpecification 的边界

`ReproductionSpecification` 属于论文语义/科研目标层，允许存在 `UNKNOWN`，不要求
repository、dataset URI、command 或 environment 已经就绪，因此不能直接运行。

`ExperimentSpecification` 属于执行层，必须已经具备 repository 和 entrypoint 或
command，并表达实际环境、超参数、seed 与期望指标。

未来 Reproduction Planner 在 Paper Parsing、Repository Analysis 和 Paper-Code
Alignment 之后，将一个 `ReproductionSpecification` 转成 **一个或多个**
`ExperimentSpecification`：Full Model 加三个消融通常产生至少四个独立实验定义，
每个实验定义又可以产生多个 `ExperimentRun`。当前没有实现这个 Planner。
