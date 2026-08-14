# 安全环境复用

Agent 只提交 `EnvironmentRequirement`，不能指定或看到 host environment path。`EnvironmentBroker` 是 deterministic trusted service：

```text
EnvironmentRequirement
  → HostEnvironmentCatalog / PreparedEnvironmentRegistry / PersistentPackageCache
  → compatibility / restricted probe
  → SandboxEnvironmentPlan
```

## Catalog 与静态 inventory

`HostEnvironmentCatalog` 仅支持管理员显式 `STATIC_REGISTRY`，或只扫描配置好的 allowlisted roots 的 `ADMIN_DISCOVERY`。它不扫描 `/`、`/home` 或其他用户目录。`StaticEnvironmentInspector` 只读取 `conda-meta/*.json`、`pyvenv.cfg` 和 `dist-info/METADATA`；不 activate、不 import package、不运行环境 Python，也不调用 pip/conda。

对 Agent 可见的 `EnvironmentDescriptor` 不含 path。真实 prefix 只存在于 trusted resource registry，最终只能作为 `REGISTERED_ENV_READ_ONLY` 精确 leaf mount。

## Fingerprint 与兼容性

`EnvironmentFingerprint` 使用 SHA-256 canonical digest（禁止 Python `hash()`），覆盖：

- platform / architecture / Python version / implementation
- package set、framework versions、system packages
- CUDA runtime、ABI metadata
- base image digest
- dependency specification hash（requirements / pyproject / conda / structured deps）
- relevant install command hash

任一项变化都会得到不同 fingerprint，不会错误复用。相同环境输入得到确定性相同 digest。

Compatibility 检查 Python specifier、required package/version、framework、architecture/platform 和 CUDA hint。Read-only prefix 即使静态匹配也必须经过 `SandboxEnvironmentProbe`：probe 在 offline、non-root、RO env mount 的 sandbox 中执行；HOME、XDG、pycache、Matplotlib、HF、Torch、pip/conda caches 全指向 run-private volume。prefix/ABI/import/probe 失败会 invalidate 并 fallback，绝不静默使用损坏环境，也绝不修改共享环境。

## 复用优先级

1. `REUSED_IMAGE`：优先复用兼容的 trusted OCI image，必须记录 `image@sha256:digest` 和 fingerprint。
2. Prepared immutable environment：第一次成功 provision 并 probe 通过后导出的 `PREPARED_ENVIRONMENT` artifact；只读 mount 到 `/sandbox-env`，不再联网下载，也不再完整 `pip install`。
3. `REUSED_READ_ONLY_ENV`：管理员精确注册的 Conda/Python prefix 只读 mount，经过 sandbox probe；缺包时不允许 pip install 到该 prefix。
4. `SEEDED_FROM_PACKAGE_CACHE`：principal + fingerprint 隔离的 persistent pip/Conda cache 只读 mount；安装目标和 writable cache 位于 sandbox-private `/sandbox-env`、`/cache`。后续相同依赖优先 `--no-index` 离线复用。
5. `BUILT_IN_SANDBOX`：cache miss 时仅 provisioning sandbox 可使用过滤 egress，依赖安装到 sandbox-private venv。

Prepared environment 与 persistent package cache 位于 `$REPROPILOT_DATA_ROOT/environments/prepared` 和 `$REPROPILOT_DATA_ROOT/cache/packages`。它们没有 `owner_run_id`，Sandbox cleanup 只删除 run-private volume，不会删除这些资产。Sandbox 中的 `/cache` 仍是 ephemeral run volume，与 persistent cache 分离。

禁止把 sandbox-private writable venv 直接注册为共享环境。Promote 流程是：

```text
sandbox-private environment
  → validation/probe
  → immutable export/copy（sandbox cleanup 之前，经 Docker API 导出，不是 writable host bind）
  → atomic publish（temp → validated → final）
  → registry registration
  → 后续 read-only reuse
```

失败、中断、probe 失败或 export 失败不会注册 artifact。并发相同 fingerprint 使用 build lock；loser 等待已发布 artifact，或 fallback 到自己的 sandbox-private build，但不能覆盖已发布环境。半构建 staging 目录从不挂载、从不注册。

Session 不持有唯一 environment。同一 Session 的 Experiment A/B 可以共享 fingerprint X，Experiment C 可以构建 fingerprint Y。

`SandboxEnvironmentPlan` 保存 strategy、base image digest、environment ID/fingerprint、cache source IDs、private env path、downloads、compatibility、warnings 和 provenance。它不保存 host path。

## Provisioning

`EnvironmentProvisioner` 仅执行 PaperReproAgent 生成的 argv，在 provisioning sandbox 内建立 `/sandbox-env/venv` 并安装结构化 dependency list。`DependencyManifestParser` 静态支持 `requirements.txt`、PEP 621 `pyproject.toml`、`environment.yml`；拒绝 requirements indirection/installer options 和无法解析的条目。

Repository 的 `install.sh`、setup shell、README 命令、Dockerfile 永远不自动执行；Dockerfile 只作 evidence。System dependency 必须已存在于 compatible image/env，或由 `TrustedSystemPackageResolver` 映射为管理员审核过的 artifact，并由 image 内 `/opt/paperrepro/bin/materialize-system-packages` 解包到 `/sandbox-env/sysroot`。未注册 system package 直接失败；实现不会执行 apt/sudo 或修改 host。

## Reuse 与 provenance audit

RunResult metadata 包含 `environment_provenance`：strategy、fingerprint、base image digest、cache IDs、required downloads、reuse layer 和选择原因；`SandboxAuditRecord` 同时记录实际 strategy/environment ID。`ReusableEnvironmentArtifact` 支持 OCI image、Conda archive、read-only prefix、package-cache source、prepared environment；记录 artifact id、fingerprint、Python version、base image digest、dependency specification hash、CUDA 兼容性、resource id、created_at、ownership 和 validation state。非 image artifact 必须引用 trusted registry ID。

Prepared Environment Artifact 不能绕过 `HostMutationGuard`：host persistence 路径必须注册，只能 RO mount，且必须是 allowed host root 的严格子目录。

后续环境复用 Linux 验证必须显式 opt-in 并配置管理员 environment registry；验证应在 probe/provision 前后对 shared prefix 做 hash，证明缺包和 probe 都不会改变 host env。
