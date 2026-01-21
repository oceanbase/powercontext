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
    
    provider_to_class = {
        "qwen": "powermem.integrations.embeddings.qwen_sparse.QwenSparseEmbedding",
    }

    @classmethod
    def create(cls, provider_name: str, config):
        """
        Create a sparse embedding instance.
        
        Args:
            provider_name: Name of the sparse embedding provider (e.g., 'qwen')
            config: Configuration dictionary, BaseSparseEmbedderConfig object, or SparseEmbedderConfig object
            
        Returns:
            Sparse embedding instance
        """
        class_type = cls.provider_to_class.get(provider_name)
        if class_type:
            # Handle different config types
            if isinstance(config, dict):
                # Filter out 'provider' if present in dict
                config_dict = {k: v for k, v in config.items() if k != 'provider'}
                config_obj = BaseSparseEmbedderConfig(**config_dict)
            elif hasattr(config, 'provider') and hasattr(config, 'config'):
                # It's a SparseEmbedderConfig object, extract the inner config
                inner_config = config.config if isinstance(config.config, dict) else config.model_dump().get('config', {})
                config_obj = BaseSparseEmbedderConfig(**inner_config)
            elif hasattr(config, 'model') or hasattr(config, 'api_key'):
                # It's already a BaseSparseEmbedderConfig object, use it directly
                config_obj = config
            else:
                # Try to convert to dict (e.g., Pydantic model)
                config_dict = config.model_dump() if hasattr(config, 'model_dump') else {}
                # Filter out 'provider' if present
                config_dict = {k: v for k, v in config_dict.items() if k != 'provider'}
                config_obj = BaseSparseEmbedderConfig(**config_dict)
            
            sparse_embedder_class = load_class(class_type)
            return sparse_embedder_class(config_obj)
        else:
            raise ValueError(f"Unsupported SparseEmbedder provider: {provider_name}")
