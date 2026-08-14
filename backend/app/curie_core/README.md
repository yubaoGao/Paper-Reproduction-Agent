# Curie Core

Task 18.5 审计后，生产代码只保留 `reproduction.py` 中由
`ReproductionOrchestrator` 直接调用的、无副作用的实验推理函数。

历史 LangGraph 节点图、内部调度器类、host shell/OpenHands 工具、报告器、
prompts/configs、测试兼容模型和旧 Docker runtime 已删除。

`curie_core` 不拥有 Docker、GPU、HTTP、数据库或宿主机执行能力。平台 GPU
调度和沙箱执行分别由 `infrastructure/gpu` 与 `infrastructure/sandbox` 负责。
