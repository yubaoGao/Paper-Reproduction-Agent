# Infrastructure

本层包含 production PostgreSQL persistence、paper/repository ingestion、GPU
scheduler 与 sandbox adapters。PostgreSQL schema 由 Alembic migration 管理。

Task 15 的 GPU scheduler 使用 PostgreSQL 保存 inventory、request 和 lease；
NVIDIA adapter 仅进行只读 inventory discovery。Sandbox 只能消费未过期
`GPULease` 中明确分配的 device IDs。Redis、Celery 与集群调度器不在本层实现。
