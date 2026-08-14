# Linux 实验沙箱运行时

Task 10 将 Task 09 的 ports 接入受信任的 Linux/Docker 基础设施。Docker API 只由 `LinuxSandboxManager` 在 sandbox 外调用；Curie、OpenHands、论文代码和用户仓库永远拿不到 Docker socket、host path 或 daemon 权限。

```text
ExperimentSpecification
        ↓
Curie Core
        ↓
Command / Coding / Workspace / Artifact Ports
        ↓
┌──────────────────────────────────┐
│ Trusted Sandbox Infrastructure   │
│ EnvironmentBroker                │
│ HostMutationGuard                │
│ LinuxSandboxManager              │
│ RunResourceRegistry              │
└────────────────┬─────────────────┘
                 ↓
        Experiment Sandbox
       Workspace / Env / Output RW

Host: Repository / Dataset / Shared Env / Cache RO
OpenHands: only Sandbox Workspace
No Docker Socket / Host RW / Host Python mutation
```

## 安全不变量

`SandboxSpec` 只保存 trusted resource ID 和容器内路径，不保存 Agent 提供的 host bind source。`TrustedResourceRegistry` 在 infrastructure 层解析真实路径；`HostMutationGuard` 在 Docker create 之前确定性拒绝：

- 未注册资源、任意 host bind、shared resource RW mount；
- `/`、`/home`（目录本身）、`/etc` 及其子树、`/usr` 及其子树、`/opt` 及其子树、`/var/run`、`/var/lib/docker`、Docker/containerd/CRI socket、credential/config 目录；
- 不在已配置 allowed host root（`REPROPILOT_DATA_ROOT` 的严格子路径）内的 HOST_PATH；
- `privileged`、host network/PID/IPC、root UID、writable rootfs；
- capability 未 drop ALL、缺少 no-new-privileges、unconfined seccomp；
- mutable image tag、all-GPU、重复 mount target、path traversal；
- 未经管理员注册和标记的 egress network。

实验阶段使用 non-root `65532:65532`、read-only rootfs、Docker default 或管理员审核 seccomp、private namespaces、PID/CPU/memory/swap/SHM limits。`/workspace`、`/sandbox-env`、`/cache`、`/output` 是当前 run 独占、quota volume driver 管理的 volume；`/tmp` 与 `/home/sandbox` 是受限 tmpfs。生产启动必须配置支持配额且能设置 UID/GID 的 Docker volume driver，否则 manager 明确拒绝创建 volume。

## Allowed host roots

HOST_PATH bind 必须同时满足：已在 `TrustedResourceRegistry` 注册、解析后不是危险系统路径、并且是 bootstrap 注入的 `allowed_host_roots` 的**严格子目录**（不能直接挂整个 data root）。生产 worker 从 `REPROPILOT_DATA_ROOT` 读取该 root，例如 `/home/gyb/ReproPilotData`。未配置时 fail-closed：拒绝一切 host bind。`/home` 本身仍禁止；`/home/...` 是否允许只由 allowed root 决定，不会因为位于 `/home` 下就自动放行或自动拒绝。

不要把 `REPROPILOT_DATA_ROOT` 设为 `/`、`/home`、`/etc` 或用户家目录本身。实验室部署应提前创建：

```text
$REPROPILOT_DATA_ROOT/repositories
$REPROPILOT_DATA_ROOT/datasets
$REPROPILOT_DATA_ROOT/checkpoints
$REPROPILOT_DATA_ROOT/runs
$REPROPILOT_DATA_ROOT/artifacts
$REPROPILOT_DATA_ROOT/cache
```

## Workspace、Dataset 与 Checkpoint

RepositorySnapshot 以 RO resource mount 到 `/source/repository`，启动后复制到当前 run 的 `/workspace/repository`；Technician/Patcher/OpenHands 只修改副本。Dataset 默认 RO mount 到 `/datasets/input`，预处理结果写 `/workspace` 或 `/output`。共享 checkpoint 必须注册为 `CHECKPOINT_READ_ONLY`；新 checkpoint 只能写 output volume。

Artifact collector 不 follow symlink，只枚举 `/output` 和 `/workspace/repository/.paperrepro` 中的普通文件，并返回 `sandbox://<run_id>/...` 引用和 metadata，不读取任意路径。

## 网络、Secret 与 GPU

Execution 默认 `OFFLINE`。Provisioning/Restricted egress 必须绑定管理员注册的过滤网络；其 registry metadata 必须声明阻断 private CIDR、link-local、cloud metadata，并关闭 container-to-container communication。禁止 host network。

Secret 只能由 `SecretProvider` 按 run 和明确变量名注入；secret value 不进入 prompt、audit、artifact 或 event，command output 会进行值替换脱敏。不挂载 `.ssh`、`.aws`、`.config`、`.env`。

GPU Scheduler 不在 Task 10。Sandbox 仅接受 `AssignedDeviceSet` 的明确 device ID/UUID；空集合表示 GPU 不可见，`all/*/-1` 被模型拒绝。CUDA userspace 来自 digest-pinned image/env，绝不修改 host driver 或 NVIDIA runtime 配置。

## 生命周期、审计与清理

`RunResourceRegistry` 记录当前 run 创建的 exact container/volume/network/temporary-image ID。Cleanup 逐 ID 删除，禁止模糊名称删除和任何 prune。`SandboxAuditRecord` 记录 image digest、环境策略、mount 类别、limits、network policy、assigned GPU、安全选项、时间和 cleanup 结果，不记录 secret。

`DockerSandboxWorkspaceAdapter.cleanup(run_id)` 应在正式 artifact storage 完成引用持久化后调用。失败的 provisioning/materialization 会立即清理当前 run 资源。Rootless Docker 或 userns-remap 优先；`capabilities()` 会报告部署模式、seccomp、cgroup 和 NVIDIA runtime 能力，不会为了 GPU 自动降低安全设置。

## Linux 验证

普通 unit tests 不需要 Docker。实验室 Linux 使用 `SANDBOX_LINUX_INTEGRATION=1` 并配置 digest image、quota volume driver、管理员 fixture；NVIDIA 使用独立 `NVIDIA_SANDBOX_INTEGRATION=1`。这些测试不在 Windows 自动执行。
