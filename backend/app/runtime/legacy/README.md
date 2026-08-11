# LEGACY_RUNTIME

此目录隔离原 Curie 的宿主 Docker 启动逻辑，仅为后续 Linux GPU Server integration 提供迁移参考。

它仍包含不适合多租户平台的明文 `.setup/env.sh`、Docker socket、宿主根目录只读挂载、host network、`--gpus all`、全局 Docker prune 等行为。新 Platform code 禁止直接 import 本包；本实现没有注册为 `ExperimentRuntime` provider，也没有 CLI entrypoint。

Task 01 已删除自动安装/启动 Docker 的行为，并删除所有 Curie telemetry。完整替换将在后续 Sandbox Runtime task 完成。
