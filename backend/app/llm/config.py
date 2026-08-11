"""Central LLM configuration; secrets are loaded only from environment."""

from __future__ import annotations

import os

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class ProviderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    provider: str
    model: str
    base_url: str
    api_key: SecretStr | None = Field(default=None, repr=False)


class LLMPlatformSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    primary: ProviderConfig
    fast: ProviderConfig

    @classmethod
    def from_env(cls) -> "LLMPlatformSettings":
        return cls(
            primary=ProviderConfig(
                provider="deepseek",
                model=os.getenv("PRIMARY_MODEL", "deepseek-v4-pro"),
                base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
                api_key=SecretStr(os.environ["DEEPSEEK_API_KEY"]) if os.getenv("DEEPSEEK_API_KEY") else None,
            ),
            fast=ProviderConfig(
                provider="qwen",
                model=os.getenv("FAST_MODEL", "qwen3.6-flash"),
                base_url=os.getenv("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
                api_key=SecretStr(os.environ["DASHSCOPE_API_KEY"]) if os.getenv("DASHSCOPE_API_KEY") else None,
            ),
        )
