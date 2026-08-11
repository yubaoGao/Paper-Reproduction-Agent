# Reproduction Planner

Task 08 建立生产级、只规划不执行的边界。`ReproductionPlannerAgent` 将 `ReproductionSpecification`、`PaperExperimentCatalog`、`RepositoryAnalysisCatalog` 和 `PaperCodeAlignmentCatalog` 合成为 `ReproductionExecutionPlan`，其中每个目标实验都有独立且语义完整的 `ExperimentSpecification`。

## 计划、策略与决策

`ReproductionExecutionPlan` 固定论文、仓库快照与 commit，记录实验、拓扑顺序、依赖、共享设置、警告、阻塞项、未决项和状态。状态与运行状态无关：`READY` 表示全部目标都已形成规格；`NEEDS_CONFIRMATION` 表示仍需非致命的用户输入（例如数据集绑定）；`BLOCKED` 表示缺少入口、实现、映射或存在不可裁决冲突。

`ReproductionPolicy` 默认是 `STRICT`。它阻止未解决的论文/仓库冲突；`PAPER_FAITHFUL` 选择有论文证据的候选；`CODE_FAITHFUL` 选择有仓库证据的候选。三种模式都保留差异。固定优先级为：显式用户覆盖、策略裁决、仓库显式值补足论文未知值。

每项关键选择形成 `PlannerDecision`，包括语义键、最终值、来源、备选值、策略、理由、论文/仓库证据、Alignment 引用、置信度和是否需确认。Planner 不会静默生成常见默认值。

## 可执行语义，但不执行

`ExperimentSpecification` 新增 `resolved_command`、`dataset_requirement`、`environment_requirement`、`resource_requirement`、`expected_claim_ids` 和 `provenance_decision_ids`。`ExecutableCommand` 只保存 program 与参数向量、工作目录、环境变量引用、入口/config/command 引用；它不是 shell 字符串，也不会以 `shell=True` 执行。

`DatasetRequirement` 保存数据集名称、真实绑定、split、预处理假设、loader 引用、双方证据与可用状态；没有用户路径时保持待绑定，不伪造路径。`EnvironmentRequirement` 描述 Python、依赖、框架、manifest 和 CUDA 提示，不安装环境。`ResourceRequirement` 只保存有证据的 GPU/CPU/内存要求，未知的 GPU 数量、显存与 CPU 均保持未知。

预期结果仍来自 `PaperClaim`，仅转换为比较用的 `MetricExpectation`，不会伪装成实际运行 `Metric`。“All Ablations” 会按论文消融逐项展开，每项必须有 Task 07 的仓库机制证据；布尔 `false` 与数值 `0` 都会被准确保留。显式实验依赖经过引用检查和拓扑排序，环会阻塞计划。

## Agent、模型路由与验证

确定性规则负责目标、普通策略、参数、单入口、配置、数据集、命令、环境、资源、状态和 provenance。只有多个有效入口、复杂 config 等真实语义歧义才交给 DeepSeek PRIMARY；输出 schema 只能选择目录中已有 ID，不能生成命令或事实。无效结果通过有界的 Qwen FAST/DeepSeek 修复，Qwen FAST 负责最终 plan review。全部 prompt 从 `v1` 文本文件加载并随包发布。

`ReproductionPlanValidator` 检查输入目录身份、commit、目标覆盖、入口/config/command/dataset/claim/Alignment 引用、参数决策、消融 provenance、依赖顺序、状态一致性，以及 STRICT 模式是否遗漏未解决冲突。`PlanningTrace` 记录输入身份、策略、时间、确定性决策数、PRIMARY/FAST 调用、修复次数、prompt 版本、warning、blocker 和无密钥 usage 元数据。

职责链严格保持为：Reproduction Planner → `ExperimentSpecification` → Future `CurieRuntimeAdapter` → Curie Core。Planner 回答“运行什么”；未来 Curie Architect 负责单个规格内部“怎样组织和推进执行”。本模块没有 Docker、GPU 调度、Curie/Technician/OpenHands 调用、仓库执行、数据下载、网络或文件修改能力。

## 验证命令

离线检查：

```powershell
python -m compileall backend tests
python -m unittest discover -s tests/unit -v
```

真实 provider smoke test 为 opt-in，并要求两个固定角色的凭据：

```powershell
$env:REPRODUCTION_PLANNER_INTEGRATION="1"
$env:DEEPSEEK_API_KEY="..."
$env:DASHSCOPE_API_KEY="..."
python -m unittest tests.integration.test_reproduction_planner_deepseek -v
```
