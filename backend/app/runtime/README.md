# Runtime

本层定义平台与执行环境之间的稳定接口。`ExperimentRuntime` 只接受
`RunRequest` 和 `RunEventSink`，并返回 `RunResult`。实现不得直接操作平台的
数据库实体、WebSocket 或全局 logger。

平台 GPU Scheduler 负责 ExperimentRun admission、并发、GPU 和资源分配；Curie Core 的 `InternalExperimentScheduler` 只负责单次实验内部的 partition、control/experimental group、worker assignment 和 agent routing。二者不可混用。

`InMemoryEventSink` 是无 IO 的测试实现。`CurieRuntimeAdapter` 当前只建立
请求翻译边界，真实 Curie/Docker/OpenHands 执行明确推迟到后续集成任务。
