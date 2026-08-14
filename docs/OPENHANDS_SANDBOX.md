# OpenHands Sandbox 边界

Task 10 保留 Curie Technician/Patcher → `CodingAgentPort` 链路，并实现 `OpenHandsCodingAgentAdapter`。OpenHands 不是 Docker controller：

- controller entrypoint 位于 digest-pinned sandbox image；
- 通过 `LinuxSandboxManager.exec()` 在当前 experiment container 内运行；
- workspace 固定为 `/workspace/repository`；
- 不获得 Docker/containerd/CRI socket、host path、shared env RW、dataset RW 或 sibling-run volume；
- command 使用结构化 program/argv，不使用 host shell 或 `shell=True`；
- mount、device、network、capability policy 在容器创建前已由 HostMutationGuard 锁定，OpenHands output 不能修改。

`SandboxedOpenHandsController` 向 image 内受信任 wrapper 传递 JSON request：instruction、允许变更类别、locked constraint keys、workspace 和 max iterations。Wrapper 负责当前部署的 OpenHands SDK 兼容性，返回 patch ID、summary、changed paths/categories、proposed values 和可选 patch path。所有 changed path 必须位于 sandbox workspace；patch artifact 必须位于 `.paperrepro`。返回 mount 或 Docker-socket 请求会失败。

OpenHands 可以修改 run-private repository copy，不能修改 RO repository snapshot、dataset、checkpoint 或 shared environment。即使生成 `rm -rf /`，read-only private container root、non-root UID、drop ALL、no-new-privileges、seccomp 和 mount policy 仍把影响限制在 sandbox；Patcher 之后仍经过 Task 09 `ExperimentSpecificationGuard`，不能改变 scientific locked constraints。

Secret 不放入 OpenHands prompt。需要模型 credential 时，由受信任 controller/SecretProvider 按 run 注入并脱敏，不允许读取 host `.env`、SSH/AWS config。stdout/stderr 有上限且 secret value 被 command adapter redaction。

后续 OpenHands 集成验证只应在 Linux 和安装 controller wrapper 的 digest image 上显式启用，并覆盖 workspace edit、host/shared resources 写失败、Docker socket 不存在、不能创建 sibling container、timeout/failure translation 和 `CodingResult` mapping。不得采用 Docker-socket mount 作为临时绕过。
