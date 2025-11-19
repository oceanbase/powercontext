"""
Rerank factory for creating rerank instances

This module provides a factory for creating different rerank instances.
"""

import importlib
from typing import Optional

from powermem.integrations.rerank.config.base import BaseRerankConfig


def load_class(class_type):
    """Dynamically load a class from a string path"""
    module_path, class_name = class_type.rsplit(".", 1)
    module = importlib.import_module(module_path)
    return getattr(module, class_name)


class RerankFactory:
    """Factory class for creating rerank model instances
    """
    
    provider_to_class = {
        "qwen": "powermem.integrations.rerank.qwen.QwenRerank",
        "jina": "powermem.integrations.rerank.jina.JinaRerank",
        "generic": "powermem.integrations.rerank.generic.GenericRerank",
    }

    @classmethod
    def create(cls, provider_name: str = "qwen", config: Optional[dict] = None):
        """
        Create a rerank instance based on provider name.

        Args:
            provider_name (str): Name of the rerank provider. Defaults to "qwen"
            config (Optional[dict]): Configuration dictionary for the rerank model

        Returns:
            RerankBase: An instance of the requested rerank provider

        Raises:
            ValueError: If the provider is not supported
        """
        class_type = cls.provider_to_class.get(provider_name)
        if class_type:
            reranker_class = load_class(class_type)
            # Create config if provided
            if config:
                base_config = BaseRerankConfig(**config)
                return reranker_class(base_config)
            else:
                return reranker_class()
        else:
            supported = ", ".join(cls.provider_to_class.keys())
            raise ValueError(
                f"Unsupported rerank provider: {provider_name}. "
                f"Supported providers: {supported}"
            )

    @classmethod
    def list_providers(cls) -> list:
        """
        List all supported rerank providers.

        Returns:
            list: List of supported provider names
        """
        return list(cls.provider_to_class.keys())

