# 安全环境复用

Agent 只提交 `EnvironmentRequirement`，不能指定或看到 host environment path。`EnvironmentBroker` 是 deterministic trusted service：

```text
EnvironmentRequirement
  → HostEnvironmentCatalog
  → compatibility / restricted probe
  → SandboxEnvironmentPlan
```

## Catalog 与静态 inventory

`HostEnvironmentCatalog` 仅支持管理员显式 `STATIC_REGISTRY`，或只扫描配置好的 allowlisted roots 的 `ADMIN_DISCOVERY`。它不扫描 `/`、`/home` 或其他用户目录。`StaticEnvironmentInspector` 只读取 `conda-meta/*.json`、`pyvenv.cfg` 和 `dist-info/METADATA`；不 activate、不 import package、不运行环境 Python，也不调用 pip/conda。

对 Agent 可见的 `EnvironmentDescriptor` 不含 path。真实 prefix 只存在于 trusted resource registry，最终只能作为 `REGISTERED_ENV_READ_ONLY` 精确 leaf mount。

## Fingerprint 与兼容性

`EnvironmentFingerprint` 覆盖 platform、architecture、Python major/minor/implementation、package set、framework versions、CUDA runtime、ABI metadata 和 canonical content digest。Broker 不按 `base`、`pytorch` 等名字猜测兼容性。

Compatibility 检查 Python specifier、required package/version、framework、architecture/platform 和 CUDA hint。Read-only prefix 即使静态匹配也必须经过 `SandboxEnvironmentProbe`：probe 在 offline、non-root、RO env mount 的 sandbox 中执行；HOME、XDG、pycache、Matplotlib、HF、Torch、pip/conda caches 全指向 run-private volume。prefix/ABI/import/probe 失败直接 fallback，绝不修改共享环境。

## 四级策略

1. `REUSED_IMAGE`：优先复用兼容的 trusted OCI image，必须记录 `image@sha256:digest` 和 fingerprint。
2. `REUSED_READ_ONLY_ENV`：精确注册的 Conda/Python prefix 只读 mount，经过 sandbox probe；缺包时不允许 pip install 到该 prefix。
3. `SEEDED_FROM_PACKAGE_CACHE`：host pip/Conda/model cache 只读 mount；安装目标和 writable cache 位于 `/sandbox-env`、`/cache`。
4. `BUILT_IN_SANDBOX`：cache miss 时仅 provisioning sandbox 可使用过滤 egress，依赖安装到 sandbox-private venv。

`SandboxEnvironmentPlan` 保存 strategy、base image digest、environment ID/fingerprint、cache source IDs、private env path、downloads、compatibility、warnings 和 provenance。它不保存 host path。

## Provisioning

`EnvironmentProvisioner` 仅执行 PaperReproAgent 生成的 argv，在 provisioning sandbox 内建立 `/sandbox-env/venv` 并安装结构化 dependency list。`DependencyManifestParser` 静态支持 `requirements.txt`、PEP 621 `pyproject.toml`、`environment.yml`；拒绝 requirements indirection/installer options 和无法解析的条目。

Repository 的 `install.sh`、setup shell、README 命令、Dockerfile 永远不自动执行；Dockerfile 只作 evidence。System dependency 必须已存在于 compatible image/env，或由 `TrustedSystemPackageResolver` 映射为管理员审核过的 artifact，并由 image 内 `/opt/paperrepro/bin/materialize-system-packages` 解包到 `/sandbox-env/sysroot`。未注册 system package 直接失败；实现不会执行 apt/sudo 或修改 host。

## Reuse 与 provenance audit

RunResult metadata 包含 `environment_provenance`：strategy、fingerprint、base image digest、cache IDs、required downloads 和选择原因；`SandboxAuditRecord` 同时记录实际 strategy/environment ID。`ReusableEnvironmentArtifact` 支持 OCI image、Conda archive、read-only prefix、package-cache source；非 image artifact 必须引用 trusted registry ID。

后续环境复用 Linux 验证必须显式 opt-in 并配置管理员 environment registry；验证应在 probe/provision 前后对 shared prefix 做 hash，证明缺包和 probe 都不会改变 host env。
