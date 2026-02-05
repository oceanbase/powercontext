"""
Rerank factory for creating rerank instances

This module provides a factory for creating different rerank instances.
"""

import importlib
from typing import Optional, Union

from powermem.integrations.rerank.config.base import BaseRerankConfig
# Trigger automatic registration by importing provider configs
from powermem.integrations.rerank.config.providers import (
    QwenRerankConfig,
    JinaRerankConfig,
    ZaiRerankConfig,
    GenericRerankConfig,
)


class RerankFactory:
    """Factory class for creating rerank model instances"""

    @classmethod
    def create(
        cls,
        provider_name: str = "qwen",
        config: Optional[Union[dict, BaseRerankConfig]] = None
    ):
        """
        Create a rerank instance based on provider name.

        Args:
            provider_name (str): Name of the rerank provider. Defaults to "qwen"
            config (Optional[Union[dict, BaseRerankConfig]]): 
                Configuration dictionary or BaseRerankConfig instance for the rerank model

        Returns:
            RerankBase: An instance of the requested rerank provider

        Raises:
            ValueError: If the provider is not supported
            TypeError: If config type is invalid
        """
        # Get config class from registry
        config_cls = BaseRerankConfig.get_provider_config_cls(provider_name)
        if not config_cls:
            supported = ", ".join(BaseRerankConfig._registry.keys())
            raise ValueError(
                f"Unsupported rerank provider: {provider_name}. "
                f"Supported providers: {supported}"
            )

        # Get class path from registry
        class_path = BaseRerankConfig.get_provider_class_path(provider_name)
        if not class_path:
            raise ValueError(f"No class path registered for provider: {provider_name}")

        # Load reranker class
        module_path, class_name = class_path.rsplit(".", 1)
        module = importlib.import_module(module_path)
        reranker_class = getattr(module, class_name)

        # Create config instance
        if config is None:
            # Use default config
            config_instance = config_cls()
        elif isinstance(config, dict):
            # Create config from dict
            config_instance = config_cls(**config)
        elif isinstance(config, BaseRerankConfig):
            # Use provided config instance directly
            config_instance = config
        else:
            raise TypeError(
                f"config must be dict or BaseRerankConfig, got {type(config)}"
            )

        # Create and return reranker instance
        return reranker_class(config_instance)

    @classmethod
    def list_providers(cls) -> list:
        """
        List all supported rerank providers.

        Returns:
            list: List of supported provider names
        """
        return list(BaseRerankConfig._registry.keys())

