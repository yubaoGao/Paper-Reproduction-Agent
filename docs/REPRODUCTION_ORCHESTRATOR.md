# Production Reproduction Orchestrator

Task 11 在 Task 08 的 `ReproductionExecutionPlan` 与 Task 09/10 的 runtime ports 之间增加跨实验编排层。`ReproductionExecutionPlan` 是唯一 execution truth；orchestrator 不生成第二份实验计划，也不允许 Curie Architect 改写实验目标、command、dataset、repository snapshot、implementation、config、hyperparameter、ablation 或 expected claims。

```text
ReproductionExecutionPlan (authoritative, digest locked)
                    │
                    ▼
      ReproductionRun / RunManifest
                    │
       deterministic DAG dispatcher
        control-first → priority → plan order
                    │
                    ▼
 Curie execution organization (locked context only)
 Architect → Technician → deterministic validators
       → semantic validator → Patcher → retry
       → Analyzer → Concluder
                    │
                    ▼
 Task 09 ports: Workspace / Command / Coding / Artifact
                    │
                    ▼
 Task 10 hardened Linux sandbox adapters
```

## 状态与持久化

已有 `RunStatus` 被复用，没有建立冲突枚举。新增 `ReproductionRun`、`StepRun`、`AttemptRecord`、`FailureRecord`、`PatchRecord`、`ValidationRecord`、`ArtifactReference` 和 `RunManifest`。Run 状态严格遵循：

```text
PENDING → QUEUED → PREPARING → RUNNING → SUCCEEDED | FAILED
    └────────────── non-terminal states ───────────→ CANCELLED
```

Step 使用 `PENDING → READY → PREPARING → RUNNING → VALIDATING` 主链；验证失败可进入 `PATCHING → RETRYING → RUNNING`，依赖失败进入 `BLOCKED`。所有 terminal state 不允许再次转换。Attempt history 只能按 1、2、3……连续追加，失败重试不会覆盖前一次 exit code、validation、failure、patch、artifact 或 metric。

`ReproductionRunRepository` 是带 optimistic revision 的持久化端口。production 代码没有 `InMemoryStore` 实现；数据库、队列和 worker 留给后续任务。`CancellationPort` 同样是外部协调边界。

## DAG 与 artifact

`RunManifest` 固定 plan ID、reproduction specification、snapshot、commit、完整 step order、dependencies 和整个 plan 的 SHA-256。执行前重新计算摘要，plan 被替换或修改会拒绝。Dispatcher 只运行依赖全部成功的 step；父 step 失败、取消或 blocked 时，子 step 确定性标记为 `BLOCKED`。多个 runnable step 按 control、显式整数 priority、plan order 排序，不承担 GPU scheduling。

上游成功 step 的 `ArtifactReference` 会进入下游 `input_artifacts` 和 run manifest history。实际跨 sandbox artifact 持久化/重新挂载由后续 deployment/storage adapter 完成，orchestrator 不把 artifact URI 解释为 host path。

## 验证、失败与重试

确定性验证固定先执行：command status/exit code、声明的 required artifact、artifact metadata schema。任一确定性检查失败时 semantic validator 不会被调用。只有确定性检查全部通过后才进入 `SemanticValidationPort`。

带 `ExperimentActionPlan` 的计划在 admission 时展开到同一 Task 11 DAG。需要结果的 TRAIN/EVALUATE step 必须经 `ResultResolver` 得到 run-level canonical result；AGGREGATE step 确定性合并全部 seed。`exit_code == 0` 但缺少 `FinalResult` 会以 validation failure 结束，run manifest 也禁止缺少 required FinalResult 的 run 标记成功。

`FailureClassifier` 输出 environment、dependency、code、config、data、resource、timeout、validation 或 unknown。`RetryPolicy` 同时限制最大 attempt 数和允许重试/补丁的 category。Timeout、data 与 resource 默认不重试；code/config/dependency/validation 只能通过 `CodingAgentPort` 请求修补。在 production composition 中该 port 必须绑定 Task 10 `OpenHandsCodingAgentAdapter`，因此补丁只能落在当前 run-private workspace，并继续受 locked constraint 与 Task 10 filesystem policy 约束。

## Curie 复用与拒绝

复用部分：locked-context Architect 组织、deterministic partition/routing 思想、Technician structured command、Validator/Patcher/Analyzer/Concluder 闭环、control-first/priority 思想、Pydantic workflow contracts。

拒绝部分：Curie host Docker management、Docker socket、host namespaces、`--gpus all`、global prune、shell command 拼接、host pip/conda mutation、writable shared workspace、plaintext secret、telemetry、reporter `exec()`、production `InMemoryStore`。Orchestration package 不导入 Docker SDK、`subprocess`、HTTP framework 或 `runtime.legacy`。

## 延期能力

本 Task 不实现 PostgreSQL、Redis、Celery、queue worker、GPU Scheduler、FastAPI、React 或真实 Linux deployment。Linux Docker、OpenHands、NVIDIA、environment reuse 的端到端验证继续由最终 deployment task 执行；Windows 只运行 domain、state machine、dispatcher、policy 和 port-isolation unit tests。
