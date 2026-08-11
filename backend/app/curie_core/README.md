# Curie Core

Curie Core 是 PaperReproAgent 内部复用的科学实验推理与实验内编排组件。

当前包含 Architect、Technician、Validator、Patcher、Analyzer、Concluder、LangGraph workflow、实验计划工具、LLM 适配和 `InternalExperimentScheduler`。它不拥有用户、HTTP、数据库、任务队列、GPU 分配、artifact storage 或多租户概念。

`tool.py` 中仍混合了一部分 OpenHands/shell 实现，`legacy_reporter.py` 仍被现有 workflow 调用。这些是已记录的后续拆分点，不代表 Platform Layer 可以直接依赖旧 runtime。
