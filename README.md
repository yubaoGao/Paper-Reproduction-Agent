# PaperReproAgent

PaperReproAgent 是面向论文实验自动复现的 AI Agent 平台。目标输入包括论文、代码仓库、数据集和复现目标；目标输出是可审计的实验运行、结构化指标、论文声明值对照和复现报告。

当前仓库处于 **Task 01：Curie Core Extraction & Repository Cleanup** 完成状态。此阶段只建立干净的 monorepo、提取 Curie Core、隔离旧 runtime 并提供 Windows 可执行的静态/单元测试基础；Paper Parser、FastAPI、数据库、队列、React、sandbox 和 GPU Scheduler 尚未实现。

## 架构边界

- `backend/app/curie_core/`：从 [Just-Curieous/Curie](https://github.com/Just-Curieous/Curie) 复用并迁移的科学实验推理、验证和实验内编排能力。
- `backend/app/domain/`、`services/`：未来论文复现领域模型与应用用例。
- `backend/app/runtime/`：平台与执行环境之间的接口；未来承载安全 sandbox 和平台 GPU scheduler。
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
- [迁移前 Curie 源码分析](docs/CURIE_CODEBASE_ANALYSIS.md)

## Attribution 与许可证

Curie Core 源自 Just-Curieous/Curie，并在 Apache License 2.0 下进行二次开发。原始许可与版权声明保留在 [LICENSE](LICENSE)。PaperReproAgent 是新产品名；“Curie”仅指内部复用的 Curie Core。
