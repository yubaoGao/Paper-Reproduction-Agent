# PaperReproAgent

PaperReproAgent 是面向论文实验自动复现的 AI Agent 平台。当前仓库已完成 Task 05：论文实验智能理解、证据约束的 `PaperExperimentCatalog` 和复现目标解析，以及生产级 Paper Ingestion、论文复现领域模型和运行时边界。

## 当前能力

- 安全解析本地 PDF、上传 bytes/stream、URL 和 arXiv 来源；
- 以 Docling 恢复 reading order、章节层级、OCR、结构化表格和图片；
- 主解析不可用时使用 pypdf 保留 page-level text；
- 通过稳定、解析器无关的 `PaperDocument` 和 evidence locator 向后续 Agent 提供输入；
- 明确记录 parser/version、fallback、OCR、SUCCESS/PARTIAL_SUCCESS、warning、耗时和内容哈希；
- 集中限制文件大小、页数、下载和解析时间，并防护 URL SSRF。
- 通过 DeepSeek V4-Pro PRIMARY 与 Qwen3.6-Flash FAST/VISION 的固定角色完成分阶段实验抽取；
- 验证 evidence locator、文本与 numeric claim，保留冲突并生成可审计 ExtractionTrace；
- 将用户复现目标确定性解析为 Catalog 有界的 `ReproductionSpecification`。

详细设计、依赖和安全策略见 [Paper Ingestion](docs/PAPER_INGESTION.md)。
论文智能抽取架构见 [论文实验智能理解与抽取](docs/PAPER_EXPERIMENT_EXTRACTION.md)。

## 安装

项目要求 Python 3.11 或更高版本：

```powershell
python -m pip install -e .
```

Docling 首次运行可能下载模型权重；生产环境应在构建/部署阶段预取并固定缓存。选择 Tesseract OCR 时需额外安装系统级 Tesseract 并配置 `TESSDATA_PREFIX`。pypdf 始终保留为轻量 fallback。

## 分层边界

- `backend/app/domain/`：解析器无关的论文和实验领域模型；
- `backend/app/services/`：Paper Ingestion 应用契约与组合策略；
- `backend/app/infrastructure/paper/`：Docling、pypdf 与安全下载适配器；
- `backend/app/curie_core/`：科学实验推理和实验内部编排；
- `backend/app/runtime/`：平台运行时契约，不依赖 legacy runtime。

本阶段没有实现 Experiment Extraction Agent、自动生成 `ReproductionSpecification`、Repository Analyzer、Paper-Code Alignment、Planner、Result Comparator、FastAPI、PostgreSQL、Redis、GPU Scheduler、Docker Experiment Runtime 或 React。

## Windows 本地检查

```powershell
python -m compileall backend tests
python -m unittest discover -s tests/unit -v
```

## 文档

- [Paper Ingestion](docs/PAPER_INGESTION.md)
- [论文实验智能理解与抽取](docs/PAPER_EXPERIMENT_EXTRACTION.md)
- [项目结构与迁移说明](docs/PROJECT_STRUCTURE.md)
- [实验领域模型与运行时契约](docs/DOMAIN_MODEL.md)
- [论文复现任务规范](docs/REPRODUCTION_SPEC.md)
- [迁移前 Curie 源码分析](docs/CURIE_CODEBASE_ANALYSIS.md)

## Attribution 与许可证

Curie Core 源自 [Just-Curieous/Curie](https://github.com/Just-Curieous/Curie) 提交 `db1b1f56159b591515f77e03c55bf473d5c1c201`，并在 Apache License 2.0 下二次开发。原始许可与版权声明保留在 [LICENSE](LICENSE)。
