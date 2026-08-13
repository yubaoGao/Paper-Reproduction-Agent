"""Explicit, lossless-enough JSONB serialization for frozen Pydantic domain models."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any, TypeVar

from pydantic import BaseModel


ModelT = TypeVar("ModelT", bound=BaseModel)
_DECIMAL_TAG = "__repropilot_decimal__"


def serialize_domain(model: BaseModel) -> dict[str, Any]:
    """Serialize a domain model to a JSONB-compatible mapping.

    Tuple shape and enums are restored by the destination Pydantic model. Decimal
    values receive an explicit tag so they never pass through binary float.
    """

    return _encode(model.model_dump(mode="python"))


def deserialize_domain(payload: dict[str, Any], model_type: type[ModelT]) -> ModelT:
    return model_type.model_validate(_decode(payload))


def _encode(value: Any) -> Any:
    if isinstance(value, Decimal):
        return {_DECIMAL_TAG: str(value)}
    if isinstance(value, Enum):
        return _encode(value.value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, BaseModel):
        return _encode(value.model_dump(mode="python"))
    if isinstance(value, dict):
        return {str(key): _encode(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_encode(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"unsupported persistence value type: {type(value).__name__}")


def _decode(value: Any) -> Any:
    if isinstance(value, dict):
        if set(value) == {_DECIMAL_TAG}:
            return Decimal(value[_DECIMAL_TAG])
        return {key: _decode(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_decode(item) for item in value]
    return value
