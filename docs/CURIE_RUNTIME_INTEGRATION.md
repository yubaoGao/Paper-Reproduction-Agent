# Curie reasoning 与 Reproduction Runtime

## Production path

```text
PostgreSQL authoritative ReproductionExecutionPlan
  -> ReproductionWorker
  -> ReproductionOrchestrator
  -> PlanStepContextFactory / ExperimentSpecificationGuard
  -> curie_core.reproduction
  -> Linux sandbox structured execution ports
  -> CanonicalResultResolver / FinalResult / comparison / product events
```

`curie_core.reproduction` 只保留 production orchestrator 直接调用的五个无 IO 函数：
`architect_plan`、`scheduler_partition`、`technician_command`、
`analyzer_interpret`、`concluder_decide`。

`PlanStepContextFactory` 从 PostgreSQL authoritative plan 构建执行上下文。仓库
revision/snapshot、implementation、实验类型、数据集、entrypoint/config、expected
claims、action、seed 与科研参数均保持锁定；模型文本不能解除锁定。Production
command 始终是 program/argv，不接受 shell 字符串。

`runtime/` 只拥有 provider-neutral contracts、结构化执行模型、guard 与 ports。
`infrastructure/sandbox/` 实现 production command/workspace/artifact/coding adapters，
由 worker composition 显式注入。历史测试兼容 adapters、in-memory 实现和 host Docker
runtime 已删除，不构成第二执行路径。

旧 Curie graph/nodes、`InternalExperimentScheduler`、Shell/OpenHands tools、modified
bash、model wrapper、reporter、prompts/configs 与 legacy images 均已删除。这些实现
中出现过的 Docker socket、host network、all-GPU、宽权限 shell 和动态 `exec` 行为
不得重新进入 production。

## Structural validation

```powershell
python -m compileall backend
```
