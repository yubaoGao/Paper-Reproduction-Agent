# Domain

本层承载与框架、数据库和执行提供方无关的论文复现实验模型。Task 02
定义了 `ExperimentSpecification`、`ExperimentRun`、`RunRequest`、
`RunEvent`、`RunResult`、`Metric`、`Artifact` 及其必要值对象。

模型只执行结构和业务不变量校验，不进行 repository/dataset/artifact IO，
也不依赖 Docker、LangGraph、HTTP 或 ORM。完整说明见
[`docs/DOMAIN_MODEL.md`](../../../docs/DOMAIN_MODEL.md)。
