"""
Sparse embedding factory for creating sparse embedding instances

This module provides a factory for creating different sparse embedding backends.
"""

import importlib

from powermem.integrations.embeddings.config.sparse_base import BaseSparseEmbedderConfig


def load_class(class_type):
    module_path, class_name = class_type.rsplit(".", 1)
    module = importlib.import_module(module_path)
    return getattr(module, class_name)


class SparseEmbedderFactory:
    """Factory for creating sparse embedding instances."""

    @classmethod
    def create(cls, provider_name: str, config):
        """
        Create a sparse embedding instance.

        Args:
            provider_name: Name of the sparse embedding provider (e.g., 'qwen')
            config: Configuration dictionary or BaseSparseEmbedderConfig object

        Returns:
            Sparse embedding instance
        """
        provider = provider_name.lower()
        class_type = BaseSparseEmbedderConfig.get_provider_class_path(provider)
        if not class_type:
            raise ValueError(f"Unsupported SparseEmbedder provider: {provider_name}")

        if isinstance(config, BaseSparseEmbedderConfig):
            config_obj = config
        else:
            if isinstance(config, dict):
                if isinstance(config.get("config"), dict):
                    config_dict = config.get("config", {})
                else:
                    config_dict = {k: v for k, v in config.items() if k != "provider"}
            elif hasattr(config, "provider") and hasattr(config, "config"):
                config_dict = (
                    config.config
                    if isinstance(config.config, dict)
                    else config.model_dump().get("config", {})
                )
            elif hasattr(config, "model_dump"):
                config_dict = config.model_dump()
            else:
                config_dict = {}
            config_cls = (
                BaseSparseEmbedderConfig.get_provider_config_cls(provider)
                or BaseSparseEmbedderConfig
            )
            config_obj = config_cls(**config_dict)

        sparse_embedder_class = load_class(class_type)
        return sparse_embedder_class(config_obj)
