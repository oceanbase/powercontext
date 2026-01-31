"""
Storage factory for creating storage instances

This module provides a factory for creating different storage backends.
"""

import importlib


def load_class(class_type):
    module_path, class_name = class_type.rsplit(".", 1)
    module = importlib.import_module(module_path)
    return getattr(module, class_name)

class VectorStoreFactory:
    provider_to_class = {
        "oceanbase": "powermem.storage.oceanbase.oceanbase.OceanBaseVectorStore",
        "pyseekdb": "powermem.storage.pyseekdb.pyseekdb_store.PySeekDBVectorStore",
        "sqlite": "powermem.storage.sqlite.sqlite_vector_store.SQLiteVectorStore",
        "pgvector": "powermem.storage.pgvector.pgvector.PGVectorStore",
        "postgres": "powermem.storage.pgvector.pgvector.PGVectorStore",  # Alias for pgvector
    }

    @classmethod
    def create(cls, provider_name, config):
        class_type = cls.provider_to_class.get(provider_name)
        if class_type:
            if not isinstance(config, dict):
                config = config.model_dump()
            vector_store_instance = load_class(class_type)
            return vector_store_instance(**config)
        else:
            raise ValueError(f"Unsupported VectorStore provider: {provider_name}")

    @classmethod
    def reset(cls, instance):
        instance.reset()
        return instance


class GraphStoreFactory:
    """
    Factory for creating MemoryGraph instances for different graph store providers.
    Usage: GraphStoreFactory.create(provider_name, config)
    """

    provider_to_class = {
        "oceanbase": "powermem.storage.oceanbase.oceanbase_graph.MemoryGraph",
        "default": "powermem.storage.oceanbase.oceanbase_graph.MemoryGraph",
    }

    @classmethod
    def create(cls, provider_name, config):
        class_type = cls.provider_to_class.get(provider_name, cls.provider_to_class["default"])
        try:
            GraphClass = load_class(class_type)
        except (ImportError, AttributeError) as e:
            raise ImportError(f"Could not import MemoryGraph for provider '{provider_name}': {e}")
        return GraphClass(config)

