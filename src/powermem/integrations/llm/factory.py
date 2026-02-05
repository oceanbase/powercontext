import importlib
from typing import Dict, Optional, Union

from powermem.integrations.llm.config.anthropic import AnthropicConfig
from powermem.integrations.llm.config.azure import AzureOpenAIConfig
from powermem.integrations.llm.config.base import BaseLLMConfig
from powermem.integrations.llm.config.deepseek import DeepSeekConfig
from powermem.integrations.llm.config.gemini import GeminiConfig
from powermem.integrations.llm.config.langchain import LangchainConfig
from powermem.integrations.llm.config.ollama import OllamaConfig
from powermem.integrations.llm.config.openai import OpenAIConfig
from powermem.integrations.llm.config.openai_structured import OpenAIStructuredConfig
from powermem.integrations.llm.config.qwen import QwenConfig
from powermem.integrations.llm.config.qwen_asr import QwenASRConfig
from powermem.integrations.llm.config.siliconflow import SiliconFlowConfig
from powermem.integrations.llm.config.vllm import VllmConfig
from powermem.integrations.llm.config.zai import ZaiConfig


def load_class(class_type):
    module_path, class_name = class_type.rsplit(".", 1)
    module = importlib.import_module(module_path)
    return getattr(module, class_name)


class LLMFactory:
    """
    Factory for creating LLM instances with appropriate configurations.
    Uses provider registration mechanism from BaseLLMConfig.
    """

    @classmethod
    def create(cls, provider_name: str, config: Optional[Union[BaseLLMConfig, Dict]] = None, **kwargs):
        """
        Create an LLM instance with the appropriate configuration.

        Args:
            provider_name (str): The provider name (e.g., 'openai', 'anthropic')
            config: Configuration object or dict. If None, will create default config from environment
            **kwargs: Additional configuration parameters (overrides)

        Returns:
            Configured LLM instance

        Raises:
            ValueError: If provider is not supported
        """
        # 1. Get class_path from registry
        class_path = BaseLLMConfig.get_provider_class_path(provider_name)
        if not class_path:
            raise ValueError(f"Unsupported Llm provider: {provider_name}")

        # 2. Get config_cls from registry
        config_cls = BaseLLMConfig.get_provider_config_cls(provider_name) or BaseLLMConfig

        # 3. Handle configuration
        if config is None:
            # Create default config from environment variables
            provider_settings = config_cls()
        elif isinstance(config, dict):
            # Create config from dict
            provider_settings = config_cls(**config)
        elif isinstance(config, BaseLLMConfig):
            # Use existing config as-is
            provider_settings = config
        else:
            raise TypeError(f"config must be BaseLLMConfig, dict, or None, got {type(config)}")

        # 4. Apply overrides (kwargs)
        if kwargs:
            provider_settings = provider_settings.model_copy(update=kwargs)

        # 5. Create LLM instance
        llm_class = load_class(class_path)
        return llm_class(provider_settings)

    @classmethod
    def register_provider(cls, name: str, class_path: str, config_class=None):
        """
        Register a new provider.

        Args:
            name (str): Provider name
            class_path (str): Full path to LLM class
            config_class: Configuration class for the provider (defaults to BaseLLMConfig)
        """
        if config_class is None:
            config_class = BaseLLMConfig

        # Register directly in BaseLLMConfig registry
        BaseLLMConfig._registry[name] = config_class
        BaseLLMConfig._class_paths[name] = class_path

    @classmethod
    def get_supported_providers(cls) -> list:
        """
        Get list of supported providers.

        Returns:
            list: List of supported provider names
        """
        return list(BaseLLMConfig._registry.keys())
