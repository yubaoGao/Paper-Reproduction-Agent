# Runtime

本层定义平台与执行环境之间的稳定接口。未来 `CurieRunner`、`SandboxRuntime` 和平台级 GPU Scheduler 位于此边界之后。

平台 GPU Scheduler 负责 ExperimentRun admission、并发、GPU 和资源分配；Curie Core 的 `InternalExperimentScheduler` 只负责单次实验内部的 partition、control/experimental group、worker assignment 和 agent routing。二者不可混用。
