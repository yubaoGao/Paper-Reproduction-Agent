"""LangChain-compatible Curie models sourced from platform LLM settings."""

from .config import LLMPlatformSettings
from .contracts import LLMRole


class CurieLLMFactory:
    """Create Curie chat models from the platform-owned provider configuration."""

    def __init__(self, settings: LLMPlatformSettings) -> None:
        self.settings = settings

    def config_for(
        self,
        component_name: str,
        role: LLMRole = LLMRole.PRIMARY,
    ):
        del component_name
        return self.settings.primary if role is LLMRole.PRIMARY else self.settings.fast

    def create_chat_model(
        self,
        component_name: str,
        role: LLMRole = LLMRole.PRIMARY,
    ):
        from langchain_community.chat_models import ChatLiteLLM

        config = self.config_for(component_name, role)
        kwargs = {
            "model": f"{config.provider}/{config.model}",
            "api_base": config.base_url,
        }
        if config.api_key is not None:
            kwargs["api_key"] = config.api_key.get_secret_value()
        return ChatLiteLLM(**kwargs)
