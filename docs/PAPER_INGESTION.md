# Paper Ingestion

Task 04 提供面向最终产品的论文摄取边界。后续 Agent 只能依赖 `PaperDocument`，不得依赖 Docling、pypdf 或其内部对象。

## 架构

```text
PaperReference + optional upload stream
  -> SecurePaperSourceResolver
  -> ResolvedPaperSource (validated PDF bytes + SHA-256)
  -> CompositePaperParser
       -> DoclingPaperParser (primary)
       -> PypdfPaperParser (hard-failure/unusable-result fallback)
  -> PaperDocument
```

领域模型位于 `backend/app/domain/paper.py`，应用契约与组合策略位于
`backend/app/services/paper_ingestion.py`，网络和解析器适配器位于
`backend/app/infrastructure/paper/`。领域和服务层不依赖 HTTP 框架、Docker 或解析器对象。

## PaperDocument 与 provenance

`PaperDocument` 包含稳定的 `document_id`、`PaperReference`、源、SHA-256、页数、页面、章节、表格、图片和解析元数据。页面同时保留全文和按 reading order 排列的 `ContentBlock`。block 类型统一为 text、heading、table、figure、list、equation 和 other；不为每种类型扩展解析器专属 class。

稳定 locator 使用可读的一基序号，而不是 Docling 的随机内部引用：

- `page:5`
- `page:5/block:p5-b12`
- `section:4.2`
- `table:2/row:DMSF/column:Accuracy`
- `figure:3`

重新解析同一内容和 reading order 时，这些 locator 可保持稳定。`content_hash` 同时出现在文档和解析元数据中，用于确认 provenance 对应同一 PDF 字节。

## Docling primary

Docling 开启 table structure、picture image generation 和 heading hierarchy。它输出的元素被立即转换为稳定 IR：页面文本用 `export_to_text(page_no=...)` 保存；reading-order 元素映射为 `ContentBlock`；heading level 映射为 `SectionBlock`；任何 Docling class 都不会越过 infrastructure 边界。

表格通过 `TableItem.export_to_dataframe(doc=...)` 转换为 `TableData(headers, rows)`，同时保留 CSV 风格的 `raw_text`、caption、页和 bbox。复杂 merged cell 无法可靠恢复时不猜测 cell：保留能够得到的 raw representation，并记录 warning，结果为 `PARTIAL_SUCCESS`。

图片通过 `PictureItem.get_image(document)` 导出到
`PaperIngestionSettings.figure_artifact_directory/<content-hash>/figure-N.png`。领域模型只保存相对稳定引用，不保存大图片 bytes。导出失败时引用为 `None`，caption/page/bbox 仍保留，并记录 warning；本阶段不使用 Vision LLM，也不解释图片语义。

## OCR

`ocr_mode` 支持 `auto`（默认）、`always` 和 `never`。auto 先以 pypdf 轻量采样前三页的文本层；可用文本不足时才启用 Docling OCR，避免对正常论文强制昂贵 OCR。`ParseMetadata.ocr_used` 记录本次是否启用了 OCR pipeline。Docling 默认 OCR 组件随 `docling` 安装；如部署选择 Tesseract，应按 Docling 官方方式额外安装 Tesseract，并设置以 `/` 结尾的 `TESSDATA_PREFIX`。缺少 Docling/OCR runtime 会显式产生解析错误，不会静默返回空内容。

Windows Python 3.11 与未来 Linux 服务端都使用同一 `docling>=2.70,<3` 依赖范围。模型权重应在部署阶段预取；运行时保持 `enable_remote_services=False` 和 `allow_external_plugins=False`，不会把论文发送给远程模型服务。

## Fallback 与状态

`CompositePaperParser` 仅在以下情况使用 pypdf：

1. primary 明确抛出 `PaperParsingError`；
2. primary 返回零可用文本和零内容 block 的不可用文档。

单个 table、figure、OCR 或 section 恢复失败，但正文仍可用时，Docling 结果保留为 `PARTIAL_SUCCESS + warnings`，不会被完整丢弃。pypdf 本身总是 `PARTIAL_SUCCESS`，因为它只能承诺 page-level text，并明确记录 hierarchy heuristic 和 table/figure 能力缺失。两个 parser 都失败时抛出 `PaperParsingError`；`FAILED` 不作为不可用的 `PaperDocument` 返回。

## Source Resolver 与安全

Resolver 支持 `LOCAL_FILE`、`PDF_UPLOAD`（bytes、bytearray、memoryview 或 binary stream）、`URL` 和 arXiv ID/标准 URL。arXiv 输入归一化为 `https://arxiv.org/pdf/<id>.pdf`。Parser 不执行网络访问。

URL 策略默认只允许 HTTPS；必须显式配置才能允许 HTTP。每个初始 URL 和 redirect 都执行：

- 禁止 URL credentials、localhost，以及 private、loopback、link-local、reserved 和其他非 global IP；
- DNS 的每一个解析地址都必须为 public global address；连接直接固定到已验证 IP，同时保留 hostname 做 TLS SNI/证书校验，消除二次解析造成的 DNS rebinding 竞态；
- 禁止 urllib 自动 redirect，逐跳重新做 DNS/IP 校验；
- 限制 redirect 数、连接/读取 timeout、Content-Length 和流式实际读取大小；
- 只接受 `application/pdf`，下载后再次验证 `%PDF-` magic header。

这些检查用于防止 SSRF 和内部网络访问。生产部署仍应以网络出口层 egress policy 作为纵深防御。

## 资源限制

`PaperIngestionSettings` 集中定义最大文件大小（默认 50 MiB）、最大页数（500）、解析 timeout（300 秒）、下载 timeout（20 秒）、redirect 上限、OCR 策略和图片目录。Docling 直接接收 file/page/`document_timeout` 限制；pypdf 每页检查 deadline。同步 service 适合由未来作业 worker 调用，不得直接放入同步 HTTP handler。

## 验证

生产源码可通过 `python -m compileall backend` 做结构检查。

完整 Docling 模型推理和扫描 PDF OCR 应在显式的本机或 CI 验证环境中执行，需要安装依赖和模型权重；它不是 fake production implementation。
