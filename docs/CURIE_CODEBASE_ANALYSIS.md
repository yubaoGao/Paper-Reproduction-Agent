# Curie 源码分析

> 分析基线：`main` 分支，commit `db1b1f56159b591515f77e03c55bf473d5c1c201`（2025-09-28）。本文以当前源码为事实依据，不把 README 的产品描述当作实现事实。

> 历史审计说明：本文记录迁移前上游 commit 的代码与风险，不描述当前 production tree。Task 18.5 已删除旧 LangGraph、host Docker runtime 与 legacy images；路径引用仅用于解释上游事实。

## 1. 结论摘要

该上游基线中的 Curie 是一个**单进程、单实验、内存态的 LangGraph 科学实验运行时**。宿主侧 `curie.experiment(...)` 负责准备输入、启动一个外层 Docker 容器；容器内 `construct_workflow_graph.py` 建图并运行。图中的 LLM 节点通过结构化工具写实验计划、调用 OpenHands 生成或修补实验脚本、执行脚本、验证并分析结果，最后由报告器生成 Markdown 报告。

它具备值得复用的实验闭环，但不是多租户平台，也没有 GPU 资源调度、持久作业状态、队列 worker、权限边界或安全沙箱。源码里名为 scheduler 的组件只调度实验计划分区在逻辑节点间流转，并不调度服务器资源。

## 2. 仓库结构与入口

| 路径 | 实际职责 |
|---|---|
| `curie/experiment.py` | PyPI API/console entry；宿主侧配置、工作目录、外层容器生命周期 |
| `curie/main.py` | 较旧的 argparse CLI 路径；与 `experiment.py` 有大量重复，当前 `setup.py` 未将它注册为 `curie` 命令 |
| `curie/construct_workflow_graph.py` | 容器内入口；定义 `State`、实例化节点、组装/运行 LangGraph、触发报告 |
| `curie/nodes/` | LLM 节点、人工输入节点、确定性 router，以及函数式 execution validator |
| `curie/scheduler.py` | 内存计划状态、优先队列、assignment、节点间程序化路由、workspace/env 初始化 |
| `curie/tool.py` | LangChain tools；计划存储、shell/PDF、OpenHands coding/patch/data agent、状态记录 |
| `curie/model.py` | LiteLLM/LangChain 模型创建、重试、上下文裁剪、token/cost 估算 |
| `curie/reporter.py` | 结果发现、LLM 摘要、LLM 生成绘图代码并 `exec`、Markdown 报告 |
| `curie/generate_report.py` | 独立报告 CLI；另起容器重放报告生成 |
| `curie/logger.py` | 多 handler 日志、用户日志格式化以及遥测上传 |
| `curie/ExpDockerfile_pip` | Curie + micromamba + Docker Engine + 固定提交 OpenHands 的运行镜像 |
| `curie/docker_setup.py` | 检查甚至尝试安装/启动宿主 Docker |
| `benchmark/`, `evaluation/` | benchmark 数据、运行器与离线评价；不是在线实验主链的一部分 |

`setup.py` 注册 `curie=curie.experiment:experiment` 和 `curie-report=curie.generate_report:main`。这意味着 `curie.experiment()` 是当前公开主入口；`curie/main.py` 是并存的 legacy CLI。还存在版本漂移：`setup.py` 是 `0.1.11`，`curie/__init__.py` 是 `0.1.3`，遥测 payload 是 `0.1.8`。

## 3. Experiment 生命周期

### 3.1 宿主侧

1. `experiment()` 可把 API keys 明文写到 `.setup/env.sh`，随后检查/安装 Docker（`experiment.py:414-431`）。
2. `<CODE_INSTRUCTION>` 被拆出并写进输入代码仓库的 `description.md`。该函数在同一文件中重复定义两次（`experiment.py:320-411`）。
3. `prepare_config()` 校验路径并**强制覆盖**镜像为 `amberljc/curie:latest`、Dockerfile 为 `ExpDockerfile_pip`（`experiment.py:250-307`）。
4. 问题文本被复制到宿主 `workspace/`；配置和日志路径生成在 `logs/<run>/`。
5. `run_docker_container()` 拉取镜像并启动外层容器；挂载 Docker socket、API key、logs、workspace、dataset，以及宿主根目录到 `/all:ro`，使用 host network，并在检测到 GPU 时传 `--gpus all`（`experiment.py:61-106`）。
6. `docker exec -it ... python3 construct_workflow_graph.py /<config>` 启动图（`experiment.py:108-133`）。
7. `finally` 停止/删除容器，并执行全局 Docker container/image/volume/builder prune（`experiment.py:135-161,231-248`）。

`experiment()` 没有返回结果对象；README 中 `result = curie.experiment(...)` 只能得到 `None`。

### 3.2 容器内

1. 模块 import 时读取 config 并初始化 logger（`construct_workflow_graph.py:39-55`）。
2. `build_graph()` 创建两个 `InMemoryStore` 和一个 `MemorySaver`，实例化全部节点和子图（`construct_workflow_graph.py:405-523`）。
3. 问题通过 `/all<host absolute path>` 读取，写到 metadata store 的固定 namespace `("admin", "exp-sched")`。
4. `graph.stream()` 以 `thread_id="main_graph_id"` 运行；recursion limit 为 `max_global_steps + 15`（`construct_workflow_graph.py:560-576`）。
5. 结束后从 JSON Lines 实验计划读取 workspace，调用 `reporter.generate_report()`，记录成本和产物路径（`construct_workflow_graph.py:595-651`）。

## 4. Workflow、State 与存储

顶层 `State` 只有：

- `messages`：用 `add_messages` 聚合的 LangChain 消息历史；
- `prev_agent` / `next_agent`：程序化路由字段；
- `is_terminate`、`is_user_input_done`；
- LangGraph 管理的 `remaining_steps` 和用于展示的副本。

实验计划及调度数据不在 graph state，而在两个进程内 `InMemoryStore` 中：

- plan store namespace：`("admin", "exp-plans")`；
- metadata namespace：`("admin", "exp-sched")`；
- metadata 包括 worker/verifier assignments、两个 heap queue、各节点 wrote list、standby plan、clarification 和 data analysis。

这不是持久化“long-term storage”。进程退出后状态消失；所有运行共享硬编码 admin namespace 的设计也不能直接多租户化（`scheduler.py:50-114,517-527`）。

每个常规节点都是一个内层 LangGraph：`Node -> tools -> Node`，直到模型不再发 tool call，然后返回顶层图。所有子图共用 `MemorySaver`，但使用按节点名固定的 thread id（`nodes/base_node.py:68-110`）。顶层所有业务节点都回到 scheduler；scheduler 根据 `next_agent` 条件路由。

## 5. 节点职责与分类

| 源码类 / 图名 | 职责与输入 | 输出 / 状态写入 | 工具 | 下一节点与错误处理 | 分类 |
|---|---|---|---|---|---|
| `Clarification` / `clarification` | 从首条 message 取原问题，LLM 生成最多 5 个问题，通过 stdin 收回答 | `clarification_data`，组合 clarified context | 直接调用模型；无 ToolNode | `clarification_router`；LLM JSON 失败使用固定问题 | 有推理的交互节点，但交互方式是 CLI 阻塞 |
| `ClarificationRouter` | 读取 clarification metadata | 写 `enriched_question` | 无 | 有 dataset 到 `data_analyzer`，否则 `supervisor` | 确定性 workflow node，不是 agent |
| `DataAnalyzer` / `data_analyzer` | question + mounted dataset | 写 `data_analysis` | `DataAgentTool`（OpenHands） | `supervisor`；缺 dataset 跳过，工具异常返回文本 | 专用 agent wrapper |
| `Architect` / `supervisor` | 原/增强问题、数据分析、计划和反馈 | 通过工具写/改 plan store 和 wrote lists | new plan、priority、redo、get、file/PDF | scheduler 决定 user input、control/experimental worker、END | 核心独立推理 agent |
| `Technician` / `worker_0` | scheduler 分配的 experimental partitions | OpenHands 创建脚本/结果；done tool 更新 plan | code agent、shell、PDF、done/get | 未 done 自循环；完成到 `llm_verifier` | 核心执行 agent |
| `Technician` / `control_worker_0` | 同上，但 control partition | 同上 | 同上 | 同上 | 同一 agent 类的控制组角色，不是不同实现 |
| `LLMValidator` / `llm_verifier` | 已完成脚本、结果和 assignment | `llm_verifier_wrote_list` | shell、get、verifier record | false 到 patcher；true 直接调用 exec validator 后到 analyzer | 有独立判断的 validator agent |
| `Patcher` / `patch_verifier` | LLM validator 判错的任务和反馈 | OpenHands 修补；写 patch record | patch agent、shell、get、record | 仍失败回 supervisor；成功经 exec validator 到 analyzer | 有独立修复决策的 agent |
| `exec_validator()` | 脚本与结果路径 | 重跑一次、拼接两次结果、更新 item | 直接 `subprocess.run` | 由调用者送 analyzer；异常标错 | 确定性函数/service，不是 LangGraph agent/node |
| `Analyzer` / `analyzer` | 多次运行结果 | `analyzer_wrote_list`，`no_change` + analysis | file read、get、record | 终止条件满足到 concluder，否则 supervisor | 结果解释 agent |
| `Concluder` / `concluder` | 全部结果、分析及剩余步数 | `concluder_wrote_list` | file read、get、record | conclude 到 END，否则 supervisor；步数不足强制结论 | 总结/停止决策 agent |
| `UserInput` / `user_input` | Architect 提案 | LangGraph interrupt + stdin response | 无 | `user_input_router` | 人工 I/O workflow node |
| `UserInputRouter` | 用户反馈和全部 plans | `user_router_wrote_list`；拒绝时删除全部 plans | record | 接受/拒绝都回 supervisor | 有 LLM 判断，但职责主要是 workflow router |
| `SchedNode` / `scheduler` | `prev_agent`、stores、queues | assignments、queues、`next_agent`、计划快照 | `SchedTool` | 所有路由；不调用 LLM | 确定性 orchestration service |

因此 Curie 不是“每个框都是自治 agent”。真正具有独立 LLM 推理/决策职责的是 Architect、Technician、DataAnalyzer/OpenHands、LLMValidator、Patcher、Analyzer、Concluder，以及有限意义上的 clarification/user feedback router。Scheduler、ClarificationRouter、UserInput、ExecValidator 是 workflow/service 节点。

## 6. Scheduler 的真实能力

`SchedNode` 把 plan 的 control/experimental groups 每 5 个变量值切成 partition，使用 Python `heapq` 按 plan priority 排队。control partition 先执行；完成后实验 partitions 才从 standby 转入 worker queue。默认只有一个 worker 和一个 control worker，每次每 worker 一个 partition（`settings.py`）。源码明确写明异步和并行尚未实现（`architect.py:121-127`, `scheduler.py:133-143`）。

它还负责：

- 为每个 plan 复制 starter repository 到 `/workspace/<name>_<plan_id>`；
- 用 micromamba 建每 plan 环境并安装 LLM 推断的包；
- 给 worker/verifier 分配任务；
- 检查所有 partition 的 `done`；
- 把内存 plans 写成 JSON Lines 快照。

这些能力可作为 Curie Core 的实验内 orchestration，但不能承担跨用户、跨进程、跨 GPU 的平台 scheduler。

## 7. Tool、代码修改与执行

Architect 通过 Pydantic schema 生成 hypothesis、constant/independent/dependent variables、control/experimental groups 和 priority。`NewExpPlanStoreWriteTool` 分区并添加运行字段；Technician 的 done tool 把模型提供的脚本与结果路径写回 plan。

代码生成和修补实际委托给固定 commit 的 OpenHands。Curie 拼接 prompt 后通过 shell 启动 OpenHands；OpenHands 再借外层容器里的 host Docker socket 创建其 runtime 容器。因此存在“宿主 -> Curie 外层容器 -> OpenHands 内层容器/实验代码”的二层容器关系。workspace 在启动前被递归 `chmod 777`。

通用 `execute_shell_command` 几乎不做 allowlist；只禁止递归 `ls`，将模型文本传入 Bash tool。workspace regex 只验证字符串包含 `/workspace/<token>`，不是路径 canonicalization 或 confinement（`utils.py:101-116`）。

## 8. Validation

验证是三层串联：

1. LLM Validator 检查生成 workflow 是否正确并结构化记录；
2. Patcher 在判错时调用 OpenHands 修复，再记录判断；
3. Exec Validator 直接重跑 control script 一次，把原结果与新结果合并供 Analyzer 判断。

关键局限：Exec Validator 不做数值容差/统计检验；已有 `compare_results()` 但主链没有调用。30 秒 timeout 被当作 `no_error=True` 返回，这会把未完成运行误当作非错误（`nodes/exec_validator.py:127-146`）。论文复现还缺目标指标 schema、seed/provenance、expected-vs-actual 比较和统计接受标准。

## 9. LLM abstraction

`model.py` 用 `ChatLiteLLM(model=$MODEL)` 统一 provider，非 Anthropic 禁用 parallel tool calls。`query_model_safe()` 提供最多 3 次重试、输入裁剪/分块摘要、粗略 token 与成本累计。它是可复用的薄适配层，但配置完全依赖进程环境变量，价格表是静态代码，累计计数是类全局，缺少 per-run tenant attribution、rate limit、structured tracing 和 provider policy。

## 10. Workspace、日志、结果与报告

宿主 `workspace/` 和 `logs/` 以读写方式挂到外层容器。每个 plan 有独立 workspace 名称，但所有实验仍共享同一宿主父目录；没有租户 owner、quota 或不可变 artifact manifest。

logger 同时产生普通、verbose、user 三类文件。`send_question_telemetry()` 会把 question、config 和最终 log 的**完整内容**以明文 HTTP POST 到固定 IP，而非真正匿名元数据（`logger.py:242-272`）。

报告器扫描 workspace 中 `.log/.txt/.json` 和 custom result paths，用 LLM 提取结果、总结日志，再让 LLM生成 Python 绘图代码并在 Curie 进程内 `exec`，最后由 LLM 写 Markdown。它能形成端到端结果，但 artifact discovery 是扩展名扫描，不是可信 manifest；报告没有论文声明值与复现值的结构化对照；`exec` 模型生成代码扩大了攻击面。

## 11. 源码与目标设想不一致处

- 当前已有 PDF 问答工具，但没有可靠的论文结构解析、实验抽取或 paper-code alignment pipeline。
- “Scheduler” 只调度 plan partitions，不调度 GPU/容器资源。
- 默认节点名是 `supervisor`, `worker_0`, `patch_verifier`, `llm_verifier`；Architect/Technician/Patcher/LLMValidator 是 Python 类名。
- Exec Validator 不是独立 agent，甚至不是顶层图节点。
- 配置虽然有 `timeout`、`max_clarification_rounds`，主链相应实现分别未使用或被硬编码覆盖。
- 所谓 long-term store 是 `InMemoryStore`；图中断后不能跨进程恢复。
- 当前默认是一实验、一 worker 的同步执行，并非 multiple concurrent experiments。
- 当前报告是通用实验报告，不是论文复现证据报告。

## 12. 可复用核心边界

建议把以下能力定义为 Curie Core：实验计划/partition 模型、LangGraph 节点闭环、实验内路由、coding/patch/validate/analyze/conclude 协议，以及可替换的 LLM/tool contracts。宿主 Docker 管理、明文 secrets、全局目录和遥测、CLI stdin、内存存储及报告容器启动方式都不应成为平台边界的一部分。详细处置见 `CURIE_REUSE_PLAN.md`。
