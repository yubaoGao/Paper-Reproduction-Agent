# Runtime contracts

本目录定义领域层与执行适配器之间的稳定契约。生产路径只从包根导出
`ExperimentRuntime` 和 `RunEventSink`，不会隐式加载测试适配器或内存实现。

历史测试兼容适配层和 `runtime.legacy` host-Docker 执行路径均已删除。生产 GPU admission、并发和
GPU 分配由 `infrastructure/gpu` 负责；容器隔离执行由
`infrastructure/sandbox` 负责，且只能消费有效 `GPULease` 中明确分配的 GPU ID。
