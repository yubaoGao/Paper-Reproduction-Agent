# Curie Core 集成与 Reproduction Runtime

Task 09 将此前保留的 Curie 实验闭环接入 PaperReproAgent 的稳定 `ExperimentRuntime` 边界。production 调用链是：

```text
ReproductionExecutionPlan
  -> ExperimentSpecification
  -> CurieRuntimeAdapter
  -> ExperimentSpecificationGuard
  -> CurieExecutionContext
  -> Architect
  -> InternalExperimentScheduler
  -> Technician / LLMValidator / ExecValidator / Patcher
  -> Analyzer / Concluder
  -> Execution Ports
  -> CurieExecutionResult
  -> RunResult
```

Planner 决定“运行什么”；Curie Architect 只决定一个已锁定实验“如何推进执行”。`ReproductionExecutionMode.REPRODUCTION` 下，Architect、Technician、Patcher 和任何模型输出都不能重新选择数据集、实验目标、消融、expected claims、仓库 commit、implementation、entrypoint、config 或已裁决参数。

## 输入翻译与锁定约束

`CurieInputTranslator` 将 `RunRequest` 中的 `ExperimentSpecification` 转换为结构化 `CurieExecutionContext`，保留实验/仓库/snapshot/implementation 身份、结构化命令、数据集、环境、资源、超参数、消融、PaperClaim、PlannerDecision 与 provenance ID。只有一段简短 execution instruction 用于人类可读说明，不会把全部对象拼成 prompt。

`CurieExecutionConstraints` 将字段分为：

- `LOCKED`：仓库 revision/snapshot、implementation、实验类型、数据集、entrypoint/config、expected claims 和每个科学参数；
- `ADVISORY`：资源要求与可选运行提示；
- `RUNTIME_RESOLVED`：实际 workspace、容器路径、GPU device 与临时 artifact 路径。

`ExperimentSpecificationGuard` 在 Architect plan、执行准备与 patch/retry 边界做确定性比较。它不读取模型对约束的解释；prompt injection 文本无法解除锁定。Patcher 只允许 import、path、API mismatch、runtime error 和 generated script 类修复。

## 执行端口与部署边界

Curie Core 不拥有 host execution。运行时只依赖四个最小端口：

- `CommandExecutionPort`：执行已批准的 program/argv，不接收任意 shell 字符串；
- `CodingAgentPort`：承载既有 Technician/Patcher 的 OpenHands 能力；
- `WorkspacePort`：返回 run、repository 与 artifact workspace 引用；
- `ArtifactCollectionPort`：收集明确产物引用。

production 包不提供 fake port，也不实现 Docker Sandbox。缺少 command/workspace/artifact backend 时，`CurieRuntimeAdapter.run()` 明确抛出 `ExecutionBackendUnavailableError`。Linux Sandbox/OpenHands adapter 实现这些端口时，无需修改 Curie Agent。

## 状态、事件和结果

`CurieStateStoreFactory` 与 `CheckpointFactory` 是可替换的最小状态边界；当前 in-memory 实现适合单 worker 与测试。namespace 和 thread ID 始终包含 `run_id` 与 `experiment_id`，例如 `run/<run_id>/experiment/<experiment_id>/agent/Architect`，不存在固定 admin 或 main graph identity。两个并发 run 使用不同 store、namespace、thread 和 workspace。

`CurieEventBridge` 发布 typed events：run/status、agent/service lifecycle、plan、command、patch、validation、log、metric、artifact 与 terminal。ExecValidator 用 `component_type=service`，不会为了 UI 被伪装成自治 Agent。

`CurieExecutionResult` 隔离 LangGraph/OpenHands/Docker 内部对象，保存 plans、attempts、validation、patch、structured metrics、artifact references、analysis、conclusion、warnings、agent trace 和时间。Adapter 再将其稳定映射为 platform `RunResult`。指标优先取自 `CommandExecutionResult` 的结构化结果；artifact 是引用与 metadata，不在本 Task 持久化。Result Comparator 和 Reproduction Report 不在本 Task。

## Curie Reuse Matrix

| Curie 组件 | 状态 | Task 09 处理 |
|---|---|---|
| Architect | REUSED / MODIFIED | 保留原类与 prompt/tool workflow；增加 reproduction hook，只组织锁定规格的执行 |
| Technician / Control Technician | REUSED / MODIFIED | 保留原角色；production 命令经 `CommandExecutionPort`，coding 经 `CodingAgentPort` |
| InternalExperimentScheduler | REUSED / MODIFIED | 保留单 run 内部分区/路由；新增 run-scoped namespace 与轻量 partition hook，不承担平台/GPU 调度 |
| LLMValidator | REUSED / MODIFIED | 保留语义验证；前置 deterministic Specification Guard |
| ExecValidator | REUSED / FIXED | structured execution validation；timeout 现在是 timeout/indeterminate，不再视为成功 |
| Patcher | REUSED / MODIFIED | 保留修补职责；每次 patch 后重新执行 Guard，禁止科学约束变化 |
| Analyzer | REUSED | 解释结构化结果与异常，不做 PaperClaim 比较 |
| Concluder | REUSED | 判断单规格证据是否充分，不修改 ReproductionExecutionPlan |
| Tool layer | PARTIALLY ADAPTED | plan tools 保留；host shell 移除默认执行；command/coding/workspace/artifact 转为 ports |
| Curie model setup | ADAPTED | `CurieLLMFactory` 使用平台 DeepSeek PRIMARY / Qwen FAST 配置，关键角色默认 PRIMARY |
| State/checkpoint | ADAPTED | 注入 factory；当前提供 in-memory，可替换 durable adapter |
| Legacy Reporter | NOT USED | 历史文件保留，但 production workflow/result chain 无 import/reference |
| Legacy Runtime | NOT USED | `backend/app/runtime/legacy/` 不进入 production import path |

没有创建 `PaperReproArchitect`、`NewTechnician` 等第二套 Agent。`curie_core/reproduction.py` 是既有组件共同委托的无重依赖 reproduction-mode 核心，用来在未安装 LangGraph/OpenHands 的 Windows 测试中验证相同锁定与阶段语义；原组件模块与 production workflow 使用同一实现。

## 验证

```powershell
python -m compileall backend
```

DeepSeek 与 OpenHands 的真实集成验证应在隔离环境中显式运行，并通过环境变量注入凭据；Windows 本地结构检查不需要这些外部能力。
