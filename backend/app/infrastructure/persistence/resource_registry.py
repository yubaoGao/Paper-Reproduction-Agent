"""PostgreSQL adapter for validated external scientific resources."""

from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError

from backend.app.domain import (
    ExternalResourceType, ResourceBinding, normalize_resource_name,
)
from backend.app.services.external_resources import (
    ExternalResourcePathValidator, ResourceAccessDeniedError, ResourceRegistryError,
)
from backend.app.services.persistence import PersistenceEntityNotFoundError

from .models import ExternalResourceBindingRow
from .repositories import _Repository
from .serialization import deserialize_domain, serialize_domain


class PostgresResourceRegistry(_Repository):
    def __init__(self, session_factory, session=None, *, path_validator=None):
        super().__init__(session_factory, session)
        self.path_validator = path_validator or ExternalResourcePathValidator()

    def register(self, binding: ResourceBinding) -> None:
        self._validate_path(binding)
        try:
            with self._write() as session:
                duplicate = session.scalar(
                    select(ExternalResourceBindingRow.resource_id).where(
                        ExternalResourceBindingRow.resource_type == binding.resource_type.value,
                        ExternalResourceBindingRow.canonical_key
                        == normalize_resource_name(binding.canonical_name),
                        ExternalResourceBindingRow.shared == binding.shared,
                        ExternalResourceBindingRow.owner_principal == binding.owner_principal,
                    )
                )
                if duplicate is not None:
                    raise ResourceRegistryError(
                        "resource canonical identity is already registered for this owner"
                    )
                session.add(self._row(binding))
                session.flush()
        except IntegrityError as exc:
            raise ResourceRegistryError("resource binding already exists") from exc

    def get(self, resource_id: str, principal: str) -> ResourceBinding:
        with self._read() as session:
            row = session.get(ExternalResourceBindingRow, resource_id)
            if row is None:
                raise PersistenceEntityNotFoundError("external resource is not registered")
            binding = self._binding(row)
            self.validate_access(binding, principal)
            return binding

    def get_internal(self, resource_id: str) -> ResourceBinding:
        """Infrastructure-only lookup for an already-authorized runtime reference."""
        with self._read() as session:
            row = session.get(ExternalResourceBindingRow, resource_id)
            if row is None:
                raise PersistenceEntityNotFoundError("external resource is not registered")
            binding = self._binding(row)
            self._validate_path(binding)
            return binding

    def get_by_identity(self, resource_type, canonical_name, principal):
        key = normalize_resource_name(canonical_name)
        with self._read() as session:
            rows = tuple(session.scalars(
                select(ExternalResourceBindingRow)
                .where(
                    ExternalResourceBindingRow.resource_type == resource_type.value,
                    ExternalResourceBindingRow.canonical_key == key,
                    or_(
                        ExternalResourceBindingRow.shared.is_(True),
                        ExternalResourceBindingRow.owner_principal == principal,
                    ),
                )
                .order_by(
                    ExternalResourceBindingRow.shared,
                    ExternalResourceBindingRow.created_at,
                    ExternalResourceBindingRow.resource_id,
                )
            ))
            return None if not rows else self._binding(rows[0])

    def list_accessible(self, principal: str) -> tuple[ResourceBinding, ...]:
        with self._read() as session:
            rows = session.scalars(
                select(ExternalResourceBindingRow)
                .where(or_(
                    ExternalResourceBindingRow.shared.is_(True),
                    ExternalResourceBindingRow.owner_principal == principal,
                ))
                .order_by(
                    ExternalResourceBindingRow.resource_type,
                    ExternalResourceBindingRow.canonical_key,
                    ExternalResourceBindingRow.resource_id,
                )
            )
            return tuple(self._binding(row) for row in rows)

    @staticmethod
    def validate_access(binding: ResourceBinding, principal: str) -> None:
        if not binding.accessible_to(principal):
            # Do not expose another principal's host path or owner identity.
            raise ResourceAccessDeniedError("external resource is not accessible")

    def _validate_path(self, binding: ResourceBinding) -> None:
        canonical = self.path_validator.validate(
            binding.host_path,
            principal=binding.owner_principal or "administrator",
            shared=binding.shared,
        )
        if canonical != binding.host_path:
            raise ResourceRegistryError("resource binding host path is not canonical")

    @staticmethod
    def _row(binding):
        return ExternalResourceBindingRow(
            resource_id=binding.resource_id,
            canonical_name=binding.canonical_name,
            canonical_key=normalize_resource_name(binding.canonical_name),
            resource_type=binding.resource_type.value,
            host_path=binding.host_path,
            access=binding.access.value,
            owner_principal=binding.owner_principal,
            shared=binding.shared,
            validation_status=binding.validation_status.value,
            binding_json=serialize_domain(binding),
            created_at=binding.created_at,
            updated_at=binding.updated_at,
        )

    @staticmethod
    def _binding(row):
        payload = dict(row.binding_json)
        payload.update(
            canonical_name=row.canonical_name,
            resource_type=row.resource_type,
            host_path=row.host_path,
            access=row.access,
            owner_principal=row.owner_principal,
            shared=row.shared,
            validation_status=row.validation_status,
            updated_at=row.updated_at,
        )
        return deserialize_domain(payload, ResourceBinding)
