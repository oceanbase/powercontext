import importlib
from typing import Dict, Optional, Union

from powermem.integrations.llm.config.anthropic import AnthropicConfig
from powermem.integrations.llm.config.azure import AzureOpenAIConfig
from powermem.integrations.llm.config.base import BaseLLMConfig
from powermem.integrations.llm.config.deepseek import DeepSeekConfig
from powermem.integrations.llm.config.ollama import OllamaConfig
from powermem.integrations.llm.config.openai import OpenAIConfig
from powermem.integrations.llm.config.qwen import QwenConfig
from powermem.integrations.llm.config.qwen_asr import QwenASRConfig
from powermem.integrations.llm.config.vllm import VllmConfig


def load_class(class_type):
    module_path, class_name = class_type.rsplit(".", 1)
    module = importlib.import_module(module_path)
    return getattr(module, class_name)


class LLMFactory:
    """
    Factory for creating LLM instances with appropriate configurations.
    Supports both old-style BaseLLMConfig and new provider-specific configs.
    """

    # Provider mappings with their config classes
    provider_to_class = {
        "ollama": ("powermem.integrations.llm.ollama.OllamaLLM", OllamaConfig),
        "openai": ("powermem.integrations.llm.openai.OpenAILLM", OpenAIConfig),
        "openai_structured": ("powermem.integrations.llm.openai_structured.OpenAIStructuredLLM", OpenAIConfig),
        "anthropic": ("powermem.integrations.llm.anthropic.AnthropicLLM", AnthropicConfig),
        "azure": ("powermem.integrations.llm.azure.AzureLLM", AzureOpenAIConfig),
        "gemini": ("powermem.integrations.llm.gemini.GeminiLLM", BaseLLMConfig),
        "deepseek": ("powermem.integrations.llm.deepseek.DeepSeekLLM", DeepSeekConfig),
        "vllm": ("powermem.integrations.llm.vllm.VllmLLM", VllmConfig),
        "langchain": ("powermem.integrations.llm.langchain.LangchainLLM", BaseLLMConfig),
        "qwen": ("powermem.integrations.llm.qwen.QwenLLM", QwenConfig),
        "qwen_asr": ("powermem.integrations.llm.qwen_asr.QwenASR", QwenASRConfig),
    }

    @classmethod
    def create(cls, provider_name: str, config: Optional[Union[BaseLLMConfig, Dict]] = None, **kwargs):
        """
        Create an LLM instance with the appropriate configuration.

        Args:
            provider_name (str): The provider name (e.g., 'openai', 'anthropic')
            config: Configuration object or dict. If None, will create default config
            **kwargs: Additional configuration parameters

        Returns:
            Configured LLM instance

        Raises:
            ValueError: If provider is not supported
        """
        if provider_name not in cls.provider_to_class:
            raise ValueError(f"Unsupported Llm provider: {provider_name}")

        class_type, config_class = cls.provider_to_class[provider_name]
        llm_class = load_class(class_type)

        # Handle configuration
        if config is None:
            # Create default config with kwargs
            config = config_class(**kwargs)
        elif isinstance(config, dict):
            # Merge dict config with kwargs
            config.update(kwargs)
            config = config_class(**config)
        elif isinstance(config, BaseLLMConfig):
            # Convert base config to provider-specific config if needed
            if config_class != BaseLLMConfig:
                # Convert to provider-specific config
                config_dict = {
                    "model": config.model,
                    "temperature": config.temperature,
                    "api_key": config.api_key,
                    "max_tokens": config.max_tokens,
                    "top_p": config.top_p,
                    "top_k": config.top_k,
                    "enable_vision": config.enable_vision,
                    "vision_details": config.vision_details,
                    "http_client_proxies": config.http_client,
                }
                config_dict.update(kwargs)
                config = config_class(**config_dict)
            else:
                # Use base config as-is
                pass
        else:
            # Assume it's already the correct config type
            pass

        return llm_class(config)

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
        cls.provider_to_class[name] = (class_path, config_class)

    @classmethod
    def get_supported_providers(cls) -> list:
        """
        Get list of supported providers.

        Returns:
            list: List of supported provider names
        """
        return list(cls.provider_to_class.keys())
