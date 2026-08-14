# Task 18.5：Production Codebase Cleanup & Curie Dead-Code Audit

审计日期：2026-08-14。审计基线为 `python -m backend.app.api`、
`python -m backend.app.worker`、Alembic revision chain 和 `frontend` 的 Vite build。
判断依据包括 Python AST import graph、直接调用点、包初始化副作用、动态资源注册、
测试引用、构建清单和全文危险模式扫描。只有同时满足“生产不可达、测试不可达、无
动态注册、无迁移/回滚/打包职责且已有安全替代”的代码才按 `LEGACY_UNUSED` 删除。

## 1. 分类结论

| 分类 | 范围 | 结论 |
| --- | --- | --- |
| `PRODUCTION_USED` | `api`、`worker`、`domain`、`services`、`orchestration`、`agents`、`infrastructure`、当前 `llm`；`runtime` 的 contracts/models/guard/ports/state；`curie_core.reproduction`；Alembic migrations；frontend `src` | 保留 |
| `DEVELOPMENT_TOOLING` | `.github`、`docs`、`scripts/README.md`、`infra/deployment`、`infra/docker/README.md`、frontend TypeScript/Vite 配置 | 保留；历史 Curie 分析文档已标记为历史材料 |
| `LEGACY_UNUSED` | 历史 Curie LangGraph/node/tool/model/reporter/config/prompt 栈、测试兼容 adapters/models、host-Docker runtime、Curie LLM shim、legacy images | 删除 |
| `UNKNOWN` | 无可安全删除的未决源码 | 不确定项采取保留策略；缺失的测试入口单独列为验证缺口 |

最终 AST 图共有 145 个 backend Python 文件。从 API、Worker 两个入口并考虑父包
`__init__` 后可达 136 个；其余为 Alembic 独立入口和两个 prompt package
initializers。Prompt assets 由 registry 按路径读取并由 `setup.py`/`MANIFEST.in`
打包，因此不是死目录。

## 2. 生产依赖链

```text
FastAPI routes
  -> ReproductionAPIService / intake / resource resolution
  -> PostgresProductPersistence -> durable PostgreSQL job queue
  -> independent ReproductionWorker
  -> PostgreSQL GPU scheduler + exact GPULease
  -> ReproductionOrchestrator
  -> retained Curie reproduction facets
  -> LinuxSandboxManager / Docker sandbox adapters
  -> canonical result resolver
  -> JobResultFinalizer -> deterministic comparison
  -> PostgreSQL ProductEvents -> SSE replay -> React UI
```

API composition只入队，不同步执行实验。Worker 才拥有 GPU 与 sandbox adapters。
平台 GPU scheduler 与实验内 partition 是不同职责；删除的
`InternalExperimentScheduler` 不再被描述为平台调度器。`repository_dataset_id`
仍由 domain、alignment 与 deterministic planner 使用，是锁定数据集身份，不是旧
workspace path，故保留。

## 3. Curie 模块与符号审计

### 保留

| 模块/符号 | 分类 | 证据 |
| --- | --- | --- |
| `curie_core.reproduction.architect_plan`、`scheduler_partition`、`technician_command`、`analyzer_interpret`、`concluder_decide` | `PRODUCTION_USED` / adapted Curie | `orchestration.orchestrator` 直接导入和调用 |

### 删除的 `LEGACY_UNUSED`

| 模块 | 类/函数 | 删除证据与替代 |
| --- | --- | --- |
| `construct_workflow_graph` | `State`、`AllNodes`、`setup_logging`、`create_graph_stores`、`build_graph`、question/stream/report helpers、`main` | 仅旧容器入口引用；API/Worker、tests、registry 均不可达。由 Worker + orchestrator + durable persistence 替代 |
| `internal_scheduler` | `InternalExperimentScheduler`、`InternalSchedulerInput`、`InternalSchedulerTool`、`partition_reproduction_execution` | 只服务旧 LangGraph；partition 语义已在 `scheduler_partition`，平台 admission 在 PostgreSQL GPU scheduler |
| `nodes.analyzer` / `architect` / `concluder` / `technician` | 对应 agent class 与 structured helper | 节点类只被旧 graph 构造；生产直接调用保留的 deterministic facets |
| `nodes.llm_validator` / `patcher` / `exec_validator` | validator/patcher class、command port shim、execution/compare helpers | 无生产或测试引用；生产使用 specification guard、deterministic validator、patch coordinator 与 sandbox command adapter |
| `nodes.base_node` / `clarification` / `data_analyzer` / `user_input` | `NodeConfig`、`BaseNode`、router 和 node classes | 仅旧图内部可达；当前 intake/clarification/API service 与 result analysis 已替代 |
| `tool` | OpenHands code/patch/data tools、shell/file/PDF tools、plan/store/verifier/analyzer/concluder write tools及其 input models | 只被删除节点引用；包含 host shell、宽权限文件操作与本地 secret 处理。生产使用 typed ports 与隔离 sandbox adapters |
| `modified_deps.langchain_bash` | `BashProcess`、`ShellInput`、`ShellTool`、platform helpers | 仅旧 `tool` 引用；生产命令为 argv 结构，由 sandbox manager 执行 |
| `model` | LLM factory configuration、`TokenCounter`、completion/cost/message helpers | 仅旧节点调用；当前使用 `llm.contracts`、router 与显式 adapters |
| `logger` | formatters、filters、message helpers、`init_logger` | 仅旧图全局日志链调用；无 production/test consumer |
| `legacy_reporter` | log/result parsing、timeout、plot、`generate_report` | 仅旧容器尾处理调用，且含动态 `exec` 风险；当前使用 canonical result resolver、comparison 与 persisted events |
| `utils` | price/model helpers、plan/workspace parsers、env categorization、OpenHands credentials、prompt loader | 仅上述旧模块引用；当前职责已有 typed domain/services/LLM/sandbox 实现 |
| `configs/**`、`prompts/**` | 旧 LangGraph JSON 配置与 prompts | 仅已删除 graph/node/tool 栈消费；同时从 `MANIFEST.in` 移除 |
| `llm.curie_factory.CurieLLMFactory`、`runtime.llm_factory` | factory 与 re-export shim | 无入口、测试、registry 或 package consumer；当前 LLM router/adapters 替代 |
| `runtime.legacy.docker_setup` | `require_docker_available` | 仅 legacy runtime 使用 |
| `runtime.legacy.experiment` | API key file、container launch/exec/cleanup/global prune、config/question helpers、`experiment` | 生产不可达且违反 runtime boundary；由 hardened sandbox + independent Worker 替代 |
| `curie_core.formatter`、`curie_core.settings` | response formatter 与 worker-name helpers | 删除测试后无 production consumer |
| `runtime.curie_adapter`、`event_bridge`、`event_sinks`、`translation`、`workflow` | 早期 contract compatibility adapters | 删除测试后无 production consumer；Worker 使用 orchestrator 与 sandbox adapters |
| `reproduction.llm_validator_guard`、`patcher_guard`、`exec_validate` | 早期 compatibility workflow helpers | production orchestrator 不调用，随兼容 workflow 删除 |

旧 `tool.py` 中所有 plan mutation 与 write-tool 类构成同一封闭 legacy cluster；不存在
外部导入或字符串 registry。其完整删除而非保留半套 shim，避免形成第二执行真相。

## 4. Runtime 清理

- `runtime.__init__` 只导出 `ExperimentRuntime` 和 `RunEventSink`。
- 删除 `state.py` 的 in-memory store/checkpointer，以及全部测试兼容 adapter/workflow。
- `runtime.legacy` 全部删除；production 没有 Docker socket、host namespace、
  privileged、all-GPU、全局 prune、明文 secret 或同步 HTTP execution 路径。

## 5. 目录前后对比

```text
清理前                              清理后
backend/app/curie_core/             backend/app/curie_core/
  nodes/ prompts/ configs/            reproduction.py
  modified_deps/                       README.md
  graph/scheduler/tool/model/...       __init__.py
  reproduction.py                      README.md
backend/app/runtime/legacy/          backend/app/runtime/ # production contracts/models
infra/docker/legacy/                infra/docker/README.md
```

共删除超过 10,000 行 tracked legacy/test code 与 assets。空 runtime workspace 与 build
output 继续由 `.gitignore` 排除；migration 与历史说明文档保留。测试文件按后续用户
明确指令删除。

## 6. 依赖审计

Python 直接依赖均有生产证据：Pydantic(domain contracts)、pypdf/Docling(paper
parsing)、httpx(download)、pathspec(snapshot filtering)、PyYAML/manifests、
Tree-sitter 与各语言 grammar(static analysis；grammar 由映射表动态导入)、
packaging(requirement checks)、Docker(sandbox backend)、SQLAlchemy/Alembic/psycopg
(persistence/migrations)、FastAPI/python-multipart/Uvicorn(API)。

`typing-extensions` 在旧 Curie formatter 清理后不再是项目直接依赖，已从
`setup.py` 删除；它仍可作为 SQLAlchemy/FastAPI 的传递依赖安装。前端 React、
React DOM、React Router、React Query、Ant Design/icons、Day.js 以及 TypeScript/
Vite 类型与构建依赖均有源码或配置引用，没有删除。

## 7. Scripts、infra、workspace 与文档

- `scripts/` 没有可执行脚本，仅保留开发用途说明，分类为 `DEVELOPMENT_TOOLING`。
- `infra/deployment/` 是明确的未来部署预留说明；没有伪生产资产。
- `infra/docker/legacy` 镜像与 environment 删除；`infra/docker/README.md` 明确安全边界。
- `workspace/` 是运行时生成目录，整个目录被忽略，不提交本地产物。
- README、项目结构、Curie/runtime/orchestrator/planner/domain 文档已与当前实现同步；
  `CURIE_CODEBASE_ANALYSIS.md` 明确标记为删除前历史分析。

## 8. 验证结果

| 检查 | 结果 |
| --- | --- |
| `pip install -e .` | 通过；Python 3.14 环境完成 editable install |
| `pip check` | 通过，无 broken requirements |
| `pnpm install --frozen-lockfile` | 通过，lockfile 与 package manifest 一致 |
| `python -m compileall backend` | 通过 |
| automated tests | 测试目录已按后续用户明确指令删除；提交中没有可运行测试套件 |
| `pnpm run typecheck` | 通过 |
| `pnpm test` | 不可执行：`package.json` 没有 `test` script |
| `pnpm run build` | 通过；3097 modules，存在大于 500 kB chunk 的非阻断 warning |
| Alembic | 单一 head `20260813_06`，01→06 线性 |
| production import smoke | API、Worker 两个 `__main__` 与 composition builders 导入通过 |
| `git diff --check` | 通过 |
| dangerous-pattern scan | 仅命中 sandbox deny-list、静态 `ast.literal_eval` 和文档中的禁止说明；无 unsafe production path |

## 9. 已知风险与下一任务建议

1. 当前没有 backend 或 frontend 自动测试；任何后续功能修改都需要优先恢复真实的
   unit、full-application、PostgreSQL integration 与 frontend component tests。
2. Vite main chunk 约 1.2 MB，后续可按 route/feature dynamic import 拆包。
3. Docling 依赖安装较重，建议 CI 固定并缓存已验证 Python 小版本与 wheels。
4. Task 19 部署阶段只新增 hardened sandbox image/policy，不恢复 legacy Docker socket、
   host network、all-GPU 或全局 prune 行为。

## 10. Git 状态约束

初始审计未提交；后续用户明确要求删除测试并执行 `git commit` 与 `git push`。
