# PaperReproAgent 项目结构

## Production structure

```text
PaperReproAgent/
├── backend/app/
│   ├── agents/                  # 论文、仓库、对齐与规划 Agent
│   ├── api/                     # 独立 FastAPI production entrypoint
│   ├── curie_core/              # production 轻量科学推理
│   ├── domain/                  # 纯领域模型
│   ├── infrastructure/
│   │   ├── gpu/                 # NVIDIA inventory adapter
│   │   ├── paper/               # Docling/pypdf 与安全 source resolver
│   │   ├── persistence/         # PostgreSQL repositories、queue、migrations
│   │   ├── repository/          # 安全解析、snapshot 与静态分析
│   │   └── sandbox/             # Linux sandbox 与 OpenHands adapter
│   ├── llm/                     # 平台结构化 LLM adapters/router
│   ├── orchestration/           # run state machine/orchestrator
│   ├── runtime/                 # provider-neutral contracts 与执行模型
│   ├── services/                # 应用用例、结果/比较与产品事件
│   └── worker/                  # 独立 production worker entrypoint
├── frontend/                    # React/Vite production frontend
├── infra/
│   ├── deployment/              # Linux deployment 预留位置
│   └── docker/                  # production sandbox image 预留位置
├── docs/                        # 架构、安全与运维文档
├── scripts/                     # 可重复、非交互开发/运维脚本
└── workspace/                   # runtime 生成目录；内容不进入 Git
```

## Production entrypoints

- API：`python -m backend.app.api`
- Worker：`python -m backend.app.worker`
- Database：`alembic.ini` 指向的单一 migration chain
- Frontend：`frontend/package.json` 中的 `pnpm run build`

API 只执行 intake/query/cancel 等应用用例，不直接调用 Docker、GPU 或实验命令。
Worker 从 PostgreSQL durable GPU queue 领取任务，经 GPU scheduler、
`ReproductionOrchestrator` 和 `infrastructure/sandbox` 执行，再持久化规范化结果、
比较报告和产品事件。

## Curie 与 runtime 边界

`curie_core/reproduction.py` 是唯一 production Curie 实现，仅提供 Architect plan、
partition、structured command、analysis 和 conclusion 的无 IO 函数。

`runtime/` 定义结构化执行模型、guard 和 ports，不拥有 Docker。
`infrastructure/sandbox/` 是 production Linux container lifecycle、安全策略、资源注册、
命令、workspace、artifact 与 OpenHands adapter 的唯一实现。

旧 LangGraph graph/nodes、测试兼容 adapter、host shell/OpenHands tools、reporter、
prompts/configs、host Docker runtime 与 legacy images 均已删除。历史事实仍记录在
`CURIE_CODEBASE_ANALYSIS.md`。

## Repository hygiene

PostgreSQL migrations、docs、前端源码、Alembic 配置与 deployment 说明属于完整
production repository。`workspace/`、`dist/`、`node_modules/`、`__pycache__/`、logs
与本地 credentials 由 `.gitignore` 排除。
