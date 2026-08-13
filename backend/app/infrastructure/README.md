# Infrastructure

当前包含 production PostgreSQL persistence、paper/repository ingestion 和 sandbox adapters。
PostgreSQL schema 由 Alembic migration 管理；Redis、Celery、queue worker 与 GPU scheduler 仍未实现。
