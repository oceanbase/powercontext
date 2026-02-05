"""
Storage factory for creating storage instances

This module provides a factory for creating different storage backends.
"""

import importlib

# Import all provider configs to trigger auto-registration
from powermem.storage.config.base import BaseVectorStoreConfig, BaseGraphStoreConfig
from powermem.storage.config.oceanbase import OceanBaseConfig, OceanBaseGraphConfig
from powermem.storage.config.pgvector import PGVectorConfig
from powermem.storage.config.sqlite import SQLiteConfig


def load_class(class_type):
    module_path, class_name = class_type.rsplit(".", 1)
    module = importlib.import_module(module_path)
    return getattr(module, class_name)

class VectorStoreFactory:
    @classmethod
    def create(cls, provider_name, config):
        """
        Create a VectorStore instance with the appropriate configuration.

        Args:
            provider_name (str): The provider name (e.g., 'oceanbase', 'pgvector', 'sqlite')
            config: Configuration object or dict. If dict, will convert to provider config

        Returns:
            Configured VectorStore instance

        Raises:
            ValueError: If provider is not supported
        """
        # Handle postgres alias
        if provider_name == "postgres":
            provider_name = "pgvector"

        # 1. Get class_path from registry
        class_path = BaseVectorStoreConfig.get_provider_class_path(provider_name)
        if not class_path:
            raise ValueError(f"Unsupported VectorStore provider: {provider_name}")

        # 2. Get config_cls from registry
        config_cls = BaseVectorStoreConfig.get_provider_config_cls(provider_name) or BaseVectorStoreConfig

        # 3. Handle config parameter
        if isinstance(config, dict):
            # Convert dict to provider config instance
            provider_config = config_cls(**config)
        elif isinstance(config, BaseVectorStoreConfig):
            # Use config instance directly
            provider_config = config
        else:
            raise TypeError(f"config must be BaseVectorStoreConfig or dict, got {type(config)}")

        # 4. Export to dict for VectorStore constructor
        config_dict = provider_config.model_dump(exclude_none=True)

        # 5. Create VectorStore instance
        vector_store_class = load_class(class_path)
        return vector_store_class(**config_dict)

    @classmethod
    def register_provider(cls, name: str, class_path: str, config_class=None):
        """
        Register a new vector store provider.

        Args:
            name (str): Provider name
            class_path (str): Full path to VectorStore class
            config_class: Configuration class for the provider (defaults to BaseVectorStoreConfig)
        """
        if config_class is None:
            config_class = BaseVectorStoreConfig

        # Register directly in BaseVectorStoreConfig registry
        BaseVectorStoreConfig._registry[name] = config_class
        BaseVectorStoreConfig._class_paths[name] = class_path

    @classmethod
    def get_supported_providers(cls) -> list:
        """
        Get list of supported providers.

        Returns:
            list: List of supported provider names
        """
        return list(BaseVectorStoreConfig._registry.keys())

    @classmethod
    def reset(cls, instance):
        instance.reset()
        return instance


class GraphStoreFactory:
    """
    Factory for creating MemoryGraph instances for different graph store providers.
    Usage: GraphStoreFactory.create(provider_name, config)
    """

    @classmethod
    def create(cls, provider_name, config):
        """
        Create a GraphStore instance with the appropriate configuration.

        Args:
            provider_name (str): The provider name (e.g., 'oceanbase')
            config: Configuration object or dict. If dict, will convert to provider config

        Returns:
            Configured GraphStore instance

        Raises:
            ValueError: If provider is not supported
        """
        # 1. Get class_path from registry
        class_path = BaseGraphStoreConfig.get_provider_class_path(provider_name)
        if not class_path:
            raise ValueError(f"Unsupported GraphStore provider: {provider_name}")

        # 2. Get config_cls from registry
        config_cls = BaseGraphStoreConfig.get_provider_config_cls(provider_name) or BaseGraphStoreConfig

        # 3. Handle config parameter
        if isinstance(config, dict):
            # Convert dict to provider config instance
            provider_config = config_cls(**config)
        elif isinstance(config, BaseGraphStoreConfig):
            # Use config instance directly
            provider_config = config
        else:
            raise TypeError(f"config must be BaseGraphStoreConfig or dict, got {type(config)}")

        # 4. Export to dict for GraphStore constructor
        config_dict = provider_config.model_dump(exclude_none=True)

        # 5. Create GraphStore instance
        graph_store_class = load_class(class_path)
        return graph_store_class(config_dict)

    @classmethod
    def register_provider(cls, name: str, class_path: str, config_class=None):
        """
        Register a new graph store provider.

        Args:
            name (str): Provider name
            class_path (str): Full path to GraphStore class
            config_class: Configuration class for the provider (defaults to BaseGraphStoreConfig)
        """
        if config_class is None:
            config_class = BaseGraphStoreConfig

        # Register directly in BaseGraphStoreConfig registry
        BaseGraphStoreConfig._registry[name] = config_class
        BaseGraphStoreConfig._class_paths[name] = class_path

    @classmethod
    def get_supported_providers(cls) -> list:
        """
        Get list of supported providers.

        Returns:
            list: List of supported provider names
        """
        return list(BaseGraphStoreConfig._registry.keys())

