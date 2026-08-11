# Domain

本层承载与框架、数据库和执行提供方无关的论文复现实验模型。Task 02
定义了 `ExperimentSpecification`、`ExperimentRun`、`RunRequest`、
`RunEvent`、`RunResult`、`Metric`、`Artifact` 及其必要值对象。

Task 03 在执行模型之前增加论文语义层：`PaperReference`、
`ReproductionTarget`、`PaperClaim`、`EvidenceReference`、
`ReproductionParameter`、`AblationDefinition` 和
`ReproductionSpecification`。该层允许未知信息，并且不保证可执行。

模型只执行结构和业务不变量校验，不进行 repository/dataset/artifact IO，
也不依赖 Docker、LangGraph、HTTP 或 ORM。完整说明见
[`docs/DOMAIN_MODEL.md`](../../../docs/DOMAIN_MODEL.md)。

论文复现任务规范见
[`docs/REPRODUCTION_SPEC.md`](../../../docs/REPRODUCTION_SPEC.md)。
