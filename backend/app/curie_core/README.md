# Curie Core

Curie Core 是 PaperReproAgent 内部复用的科学实验推理与单次实验编排组件，保留 Architect、Technician、Validator、Patcher、Analyzer、Concluder、LangGraph workflow 和 `InternalExperimentScheduler`。

Task 09 的 production 入口是 `CurieRuntimeAdapter`。命令执行、工作区、coding agent 与 artifact 收集均通过 runtime ports 注入；Core 不再提供默认 host shell。原 `legacy_reporter.py` 仅作为历史文件保留，新的 workflow 和结果链不引用它。`backend/app/runtime/legacy/` 同样不进入 production import path。

`reproduction.py` 是现有 Curie 组件共同委托的无重依赖 reproduction-mode 逻辑，使 Windows 单元测试不必安装 LangGraph、Docker、OpenHands 或 GPU runtime。它不定义第二套 Agent；原组件模块和 production workflow 使用同一实现。
