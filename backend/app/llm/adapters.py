"""Production DeepSeek and Qwen structured-output adapters."""

from __future__ import annotations

import base64
import json
import mimetypes
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from .config import ProviderConfig
from .contracts import (
    LLMCallMetadata,
    LLMCallSettings,
    LLMProviderError,
    LLMRole,
    StructuredLLMClient,
    StructuredLLMResponse,
    StructuredOutputError,
)

T = TypeVar("T", bound=BaseModel)


class _OpenAICompatibleStructuredAdapter(StructuredLLMClient):
    def __init__(self, config: ProviderConfig, *, supports_images: bool, http: httpx.Client | None = None) -> None:
        self.config = config
        self.supports_images = supports_images
        self._http = http or httpx.Client()

    def generate_structured(
        self,
        *,
        role: LLMRole,
        system_prompt: str,
        content: str,
        output_schema: type[T],
        prompt_name: str,
        prompt_version: str,
        images: tuple[str, ...] = (),
        settings: LLMCallSettings | None = None,
    ) -> StructuredLLMResponse[T]:
        call_settings = settings or LLMCallSettings()
        if images and not self.supports_images:
            raise LLMProviderError(f"{self.config.provider} adapter does not accept images")
        if self.config.api_key is None:
            raise LLMProviderError(f"missing API key for {self.config.provider}")
        started = datetime.now(timezone.utc)
        schema_json = json.dumps(output_schema.model_json_schema(), ensure_ascii=False)
        system = (
            f"{system_prompt}\nReturn JSON only. The JSON must validate against this schema:\n{schema_json}"
        )
        user_content: str | list[dict[str, Any]] = content
        if images:
            user_content = [{"type": "text", "text": content}]
            user_content.extend(self._image_part(image) for image in images)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_content},
        ]
        last_error: Exception | None = None
        total_input = total_output = 0
        for attempt in range(call_settings.max_retries + 1):
            payload = self._payload(messages, call_settings)
            try:
                response = self._http.post(
                    f"{self.config.base_url.rstrip('/')}/chat/completions",
                    headers={"Authorization": f"Bearer {self.config.api_key.get_secret_value()}", "Content-Type": "application/json"},
                    json=payload,
                    timeout=call_settings.timeout_seconds,
                )
                response.raise_for_status()
                body = response.json()
                usage = body.get("usage") or {}
                total_input += int(usage.get("prompt_tokens") or 0)
                total_output += int(usage.get("completion_tokens") or 0)
                raw = body["choices"][0]["message"]["content"]
                if not raw:
                    raise StructuredOutputError("provider returned empty structured content")
                value = output_schema.model_validate_json(raw)
                finished = datetime.now(timezone.utc)
                return StructuredLLMResponse[T](
                    value=value,
                    metadata=LLMCallMetadata(
                        provider=self.config.provider,
                        model=self.config.model,
                        role=role,
                        started_at=started,
                        finished_at=finished,
                        input_tokens=total_input,
                        output_tokens=total_output,
                        retry_count=attempt,
                        prompt_name=prompt_name,
                        prompt_version=prompt_version,
                    ),
                )
            except (httpx.HTTPError, KeyError, TypeError, ValueError, ValidationError, StructuredOutputError) as exc:
                last_error = exc
                if attempt >= call_settings.max_retries:
                    break
                messages.append({"role": "assistant", "content": "The previous response did not validate."})
                messages.append({"role": "user", "content": f"Repair the JSON to exactly match the supplied schema. Validation issue: {str(exc)[:500]}"})
                time.sleep(min(0.25 * (2**attempt), 2.0))
        if isinstance(last_error, (ValidationError, StructuredOutputError, KeyError, TypeError, ValueError)):
            raise StructuredOutputError(
                f"{self.config.provider} structured output failed after {call_settings.max_retries + 1} attempts: {last_error}"
            ) from last_error
        raise LLMProviderError(
            f"{self.config.provider} request failed after {call_settings.max_retries + 1} attempts: {last_error}"
        ) from last_error

    def _payload(self, messages: list[dict[str, Any]], settings: LLMCallSettings) -> dict[str, Any]:
        return {
            "model": self.config.model,
            "messages": messages,
            "response_format": {"type": "json_object"},
            "temperature": settings.temperature,
            "max_completion_tokens": settings.max_output_tokens,
        }

    @staticmethod
    def _image_part(image_path: str) -> dict[str, Any]:
        path = Path(image_path)
        if not path.is_file():
            raise LLMProviderError(f"image does not exist: {path}")
        mime = mimetypes.guess_type(path.name)[0] or "image/png"
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}}


class DeepSeekStructuredLLMAdapter(_OpenAICompatibleStructuredAdapter):
    def __init__(self, config: ProviderConfig, *, http: httpx.Client | None = None) -> None:
        super().__init__(config, supports_images=False, http=http)

    def _payload(self, messages: list[dict[str, Any]], settings: LLMCallSettings) -> dict[str, Any]:
        payload = super()._payload(messages, settings)
        # DeepSeek currently documents max_tokens for Chat Completions.
        payload["max_tokens"] = payload.pop("max_completion_tokens")
        if settings.thinking_enabled is not None:
            payload["thinking"] = {"type": "enabled" if settings.thinking_enabled else "disabled"}
        if settings.reasoning_effort is not None:
            payload["reasoning_effort"] = settings.reasoning_effort
        return payload


class QwenStructuredLLMAdapter(_OpenAICompatibleStructuredAdapter):
    def __init__(self, config: ProviderConfig, *, http: httpx.Client | None = None) -> None:
        super().__init__(config, supports_images=True, http=http)

    def _payload(self, messages: list[dict[str, Any]], settings: LLMCallSettings) -> dict[str, Any]:
        payload = super()._payload(messages, settings)
        # FAST/VISION defaults to non-thinking for predictable latency and cost.
        payload["enable_thinking"] = bool(settings.thinking_enabled) if settings.thinking_enabled is not None else False
        return payload
