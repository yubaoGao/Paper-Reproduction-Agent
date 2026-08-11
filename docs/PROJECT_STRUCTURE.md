# PaperReproAgent 项目结构

## 1. Task 01 结果

本次重构把原 Curie 产品仓库转换为 PaperReproAgent monorepo 基础。没有实现论文解析、Web API、数据库、队列、sandbox、GPU scheduler 或完整 ExperimentRunner。

```text
PaperReproAgent/
├── backend/
│   └── app/
│       ├── api/                 # 未来 FastAPI 边界（当前无 endpoint）
│       ├── curie_core/          # 科学实验推理与实验内编排
│       │   ├── nodes/
│       │   ├── prompts/
│       │   ├── configs/
│       │   ├── construct_workflow_graph.py
│       │   ├── internal_scheduler.py
│       │   ├── tool.py
│       │   ├── model.py
│       │   └── legacy_reporter.py
│       ├── domain/              # 未来论文复现领域模型
│       ├── services/            # 未来应用服务
│       ├── runtime/
│       │   ├── interfaces.py    # 平台可依赖的 provider-neutral contract
│       │   └── legacy/          # 隔离的原 Curie host runtime
│       └── infrastructure/      # 未来 DB/queue/storage/docker adapters
├── frontend/                    # 未来 React 前端
├── infra/
│   ├── docker/legacy/           # 未适配的原 Curie images
│   └── deployment/              # 未来 Linux GPU 部署
├── tests/unit/                  # Windows/Docker/GPU-independent tests
├── docs/
├── scripts/
├── README.md
├── AGENTS.md
└── LICENSE
```

## 2. Curie Core 边界

Curie Core 只负责：

- Architect、Technician、LLM Validator、Patcher、Analyzer、Concluder 等科学实验角色；
- hypothesis/variables/control group/experimental group/partition 等实验计划结构；
- LangGraph workflow 和 agent/tool transition；
- `InternalExperimentScheduler` 的 plan partition、priority queue、assignment 和 agent routing；
- LLM abstraction、tool contracts、workflow validation 和结果分析闭环。

Curie Core 不负责 User、Authentication、HTTP、Database、Job Queue、GPU allocation、Artifact Storage 或 Multi-tenancy。它目前仍有两个过渡性耦合：`tool.py` 包含 OpenHands/shell 的具体实现，workflow 仍调用 `legacy_reporter.py`。后续应通过 adapters 拆出，但 Task 01 不重写 agent 或报告系统。

## 3. Platform Layer 边界

`domain/` 将定义 Paper、Repository、Dataset、ExperimentSpecification、Experiment、ExperimentRun、Metric、Artifact 和 ReproductionReport；`services/` 编排用例；`runtime/interfaces.py` 是服务层通往安全执行环境的唯一稳定边界；`infrastructure/` 实现 DB、queue、storage 和 container adapters；`api/` 只接受请求和查询 job，不同步运行实验。

### 两种 Scheduler

| 组件 | 所属层 | 负责 | 不负责 |
|---|---|---|---|
| `InternalExperimentScheduler` | Curie Core | 单次实验内 plan partition、control/experimental 顺序、worker/verifier assignment、agent routing | GPU、租户、公平性、跨进程 job |
| Platform GPU Scheduler（未来） | Runtime/Platform | ExperimentRun admission、并发、GPU/CPU/RAM 分配、quota、preemption | 科学实验推理和 agent transition |

## 4. LEGACY_RUNTIME 边界

`backend/app/runtime/legacy/experiment.py` 与 `infra/docker/legacy/` 是迁移参考，不是生产 runtime provider。它没有实现 `ExperimentRuntime`，setup metadata 也不再暴露 `curie` CLI，因此新 platform code 不会意外依赖它。

保留它是因为现有完整 Curie workflow 在 Linux 上仍需要外层容器、OpenHands 镜像和 workspace mounts 才能集成验证；本地 Windows 阶段没有安全替代实现。已立即移除的行为包括第三方 telemetry 和自动安装/启动 Docker。仍待替换的风险包括 Docker socket、`/:/all:ro`、host network、`--gpus all`、global prune、`chmod 777` 和明文 `.setup/env.sh`。

## 5. 删除审计与结果

| 删除项 | 引用审计 | 删除理由 |
|---|---|---|
| `benchmark/` | runtime 无 import；只被原 README、benchmark runner 和旧教程引用 | EXP-Bench/MLE/SWE benchmark 数据与产物，不属于产品 runtime，约 19 MB |
| `evaluation/` | 仅自身离线脚本引用 | 原 benchmark 的 offline judge/statistics，不是平台验证模块 |
| `starter_file/` 与 `.gitmodules` | 仅旧 benchmark、测试脚本和 legacy 路径提示引用；公开 API 接受外部 `codebase_dir` | bundled fixtures/submodules 不应成为论文复现产品输入 |
| `curie/main.py` | 只被旧教程调用；setup 的实际入口曾是 `experiment.py` | 与 experiment host orchestration 重复的 legacy argparse CLI |
| `curie/generate_report.py` | 只被 `curie-report` entrypoint 使用；workflow 直接调用 `reporter.generate_report` | 独立起容器的 legacy report CLI，无平台 contract |
| `curie/tests/` | 逐文件检查：大量 import-time shell/LLM/stdin、缺失符号和 benchmark fixture；不是稳定 pytest suite | 以 `tests/unit` 中无外部依赖测试替代 |
| 旧 docs/tutorial/example logs/static assets | 只引用已删除 CLI/benchmark/Curie 产品内容 | 避免继续把仓库描述为 Curie demo 产品；保留源码分析并新增本文件 |
| benchmark configs/prompts | 只由已删除 benchmark task configs 或手工覆盖引用 | 不属于通用 Curie Core；保留 base/template/simple/experiment prompts |
| 原 Docker CI/deploy stub | 构建已迁移且明确不在本阶段验证的旧镜像，deploy 仅 echo | 改为 compile + unit CI |
| telemetry function/calls | `logger.py` 定义；`experiment.py`/`main.py` 调用 | 会把 question/config/log 全文通过第三方 HTTP endpoint 上传，违反产品隐私边界 |

许可证 `LICENSE`、原 Curie attribution、核心 prompts/config、全部主要 nodes、internal scheduler、tools、LLM abstraction、validation 和 workflow 均保留。

## 6. 移动与重命名

- `curie/` 核心代码 → `backend/app/curie_core/`；全部内部 import 改为 package-relative。
- `scheduler.py` → `internal_scheduler.py`；`SchedNode` → `InternalExperimentScheduler`，`SchedTool` → `InternalSchedulerTool`。
- `reporter.py` → `legacy_reporter.py`，明确它只是当前 workflow 的过渡依赖。
- `experiment.py`, `docker_setup.py` → `backend/app/runtime/legacy/`。
- Dockerfiles 与 `environment.yml` → `infra/docker/legacy/`。
- 移除 import workflow 时解析 argv/打开日志的副作用，运行期初始化移入 `build_graph()`。

## 7. 暂时保留及原因

- `construct_workflow_graph.py` 和 nodes：Curie 科学实验闭环核心。
- `tool.py` 与 modified Bash tool：agent contracts 与 OpenHands 实现仍交织，贸然删除会破坏 workflow；后续先定义 ports 再拆 adapter。
- `legacy_reporter.py`：workflow 结束路径仍直接调用；等 Structured Result Comparator 与 Reproduction Report 可替换后删除。
- `prompts/simple/`：隔离的 legacy experiment 默认 task config 仍引用。
- legacy runtime/images：留给 Linux GPU integration 对照；明确禁止生产依赖。

## 8. 后续迁移顺序

1. 定义 Curie Core 的运行输入/事件/结果 contract，去除 config/CWD/全局 logger 假设。
2. 将 OpenHands、shell、PDF 和 artifact I/O 从 `tool.py` 拆成 ports + runtime/infrastructure adapters。
3. 将 in-memory plan/store 抽象为 per-run repository，同时保持 internal scheduler 语义。
4. 实现结构化论文复现领域模型与 ingestion pipeline。
5. 实现安全 sandbox provider，再在 Linux GPU Server 执行完整 Curie integration。
6. 用 Structured Result Comparator/Reproduction Report 替换 `legacy_reporter.py`，最后删除 LEGACY_RUNTIME。
