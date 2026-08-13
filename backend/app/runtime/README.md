# Runtime

本层定义平台与执行环境之间的稳定接口。`ExperimentRuntime` 只接收
`RunRequest` 和 `RunEventSink`，并返回 `RunResult`；实现不得直接操作平台数据库、
WebSocket 或全局 logger。

平台 GPU Scheduler 负责 admission、并发和 GPU 分配。Curie Core 的
`InternalExperimentScheduler` 只负责单次实验内部的 partition、control/experimental
group、worker assignment 和 agent routing，两者不可混用。

Task 10 sandbox 只消费 Task 15 `GPULease` 中明确分配的 GPU IDs；裸 GPU 列表、
`all` 和过期 lease 均不能进入 production execution spec。

`InMemoryEventSink` 是无 IO 的测试实现。`CurieRuntimeAdapter` 当前只建立请求翻译
边界，真实 Curie/Docker/OpenHands 执行由基础设施适配器提供。
