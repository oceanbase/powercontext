"""
Embedding factory for creating embedding instances

This module provides a factory for creating different embedding instances.
"""

import importlib
from typing import Optional

from powermem.integrations.embeddings.config.base import BaseEmbedderConfig
from powermem.integrations.embeddings.config.providers import CustomEmbeddingConfig
from powermem.integrations.embeddings.mock import MockEmbeddings


def load_class(class_type):
    module_path, class_name = class_type.rsplit(".", 1)
    module = importlib.import_module(module_path)
    return getattr(module, class_name)


class EmbedderFactory:
    @classmethod
    def create(cls, provider_name, config, vector_config: Optional[dict]):
        # Helper function to extract dimension from vector_config (handles both dict and object)
        def get_dimension_from_vector_config(vector_config, default=1536):
            if not vector_config:
                return default
            if isinstance(vector_config, dict):
                value = vector_config.get('embedding_model_dims', default)
                return default if value is None else value
            value = getattr(vector_config, 'embedding_model_dims', default)
            return default if value is None else value

        # Helper function to extract dimension from embedder config (handles dict and BaseSettings)
        def get_dimension_from_embedder_config(embedder_config, default=1536):
            if not embedder_config:
                return default
            if isinstance(embedder_config, dict):
                value = embedder_config.get('embedding_dims', default)
                return default if value is None else value
            value = getattr(embedder_config, 'embedding_dims', default)
            return default if value is None else value
        
        # Handle mock provider directly
        if provider_name == "mock":
            # Extract dimension from vector_config or embedder config, default to 1536
            dimension = 1536  # Default dimension
            dimension = get_dimension_from_vector_config(vector_config, dimension)
            dimension = get_dimension_from_embedder_config(config, dimension)
            return MockEmbeddings(dimension=dimension)
        if provider_name == "upstash_vector" and vector_config:
            # Check enable_embeddings (handles both dict and object)
            enable_embeddings = False
            if isinstance(vector_config, dict):
                enable_embeddings = vector_config.get('enable_embeddings', False)
            else:
                enable_embeddings = getattr(vector_config, 'enable_embeddings', False)
            
            if enable_embeddings:
                # Extract dimension from vector_config or embedder config, default to 1536
                dimension = 1536  # Default dimension
                dimension = get_dimension_from_vector_config(vector_config, dimension)
                dimension = get_dimension_from_embedder_config(config, dimension)
                return MockEmbeddings(dimension=dimension)
        class_type = BaseEmbedderConfig.get_provider_class_path(provider_name)
        if class_type:
            embedder_instance = load_class(class_type)
            if isinstance(config, BaseEmbedderConfig):
                base_config = config
            else:
                config_data = config or {}
                config_cls = (
                    BaseEmbedderConfig.get_provider_config_cls(provider_name)
                    or CustomEmbeddingConfig
                )
                base_config = config_cls(**config_data)
            return embedder_instance(base_config)
        raise ValueError(f"Unsupported Embedder provider: {provider_name}")
