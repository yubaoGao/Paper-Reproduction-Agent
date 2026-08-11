# PaperReproAgent

PaperReproAgent 是面向论文实验自动复现的 AI Agent 平台。目标输入包括论文、代码仓库、数据集和复现目标；目标输出是可审计的实验运行、结构化指标、论文声明值对照和复现报告。

当前仓库已完成 **Task 03：Paper Reproduction Specification**。项目已具备论文引用、复现目标、论文声明、来源证据、未知/推断信息和消融定义模型，并保持论文语义目标到可执行实验的一对多规划边界。Paper Parser、Reproduction Planner、FastAPI、数据库、队列、React、sandbox、GPU Scheduler 和真实 Curie execution 尚未实现。

## 架构边界

- `backend/app/curie_core/`：从 [Just-Curieous/Curie](https://github.com/Just-Curieous/Curie) 复用并迁移的科学实验推理、验证和实验内编排能力。
- `backend/app/domain/`、`services/`：论文复现语义、可执行实验领域模型与未来应用用例。
- `backend/app/runtime/`：平台与执行环境之间的稳定接口、内存事件接收器和 Curie adapter skeleton；未来承载安全 sandbox。
- `backend/app/runtime/legacy/`：隔离的 `LEGACY_RUNTIME`，不得被新平台代码直接依赖。
- `frontend/`、`infra/`：未来 Web 前端、容器和部署实现。

Curie Core 的 `InternalExperimentScheduler` 负责单个实验内部的 plan partition、control/experimental group、worker assignment 和 agent routing。未来 Platform GPU Scheduler 负责 `ExperimentRun` admission、并发与 GPU/CPU/RAM 分配；两者是不同层次的组件。

## Windows 本地检查

```powershell
python -m compileall backend tests
python -m unittest discover -s tests/unit -v
```

完整 Docker/OpenHands/GPU integration 明确推迟到 Linux GPU Server Integration Phase。

## 文档

- [项目结构与迁移说明](docs/PROJECT_STRUCTURE.md)
- [实验领域模型与运行时契约](docs/DOMAIN_MODEL.md)
- [论文复现任务规范](docs/REPRODUCTION_SPEC.md)
- [迁移前 Curie 源码分析](docs/CURIE_CODEBASE_ANALYSIS.md)

## Attribution 与许可证

Curie Core 源自 [Just-Curieous/Curie](https://github.com/Just-Curieous/Curie) 的提交 `db1b1f56159b591515f77e03c55bf473d5c1c201`，并在 Apache License 2.0 下进行二次开发。原始许可与版权声明保留在 [LICENSE](LICENSE)。PaperReproAgent 是新产品名；“Curie”仅指内部复用的 Curie Core。
