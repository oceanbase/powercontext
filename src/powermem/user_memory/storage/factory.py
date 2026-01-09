"""
User Profile Store factory for creating storage instances

This module provides a factory for creating different user profile storage backends.
"""

import importlib
import logging
from typing import Dict

logger = logging.getLogger(__name__)


def load_class(class_type):
    """Load a class from a module path string."""
    module_path, class_name = class_type.rsplit(".", 1)
    module = importlib.import_module(module_path)
    return getattr(module, class_name)


class UserProfileStoreFactory:
    """
    Factory for creating UserProfileStore instances for different storage providers.
    Usage: UserProfileStoreFactory.create(provider_name, config)
    """
    
    provider_to_class = {
        "oceanbase": "powermem.user_memory.storage.user_profile.OceanBaseUserProfileStore",
        "sqlite": "powermem.user_memory.storage.user_profile_sqlite.SQLiteUserProfileStore",
    }

    @classmethod
    def create(cls, provider_name: str, config: Dict):
        """
        Create a UserProfileStore instance for the given provider.

        Args:
            provider_name: Name of the storage provider (e.g., "oceanbase")
            config: Configuration dictionary for the storage provider

        Returns:
            UserProfileStore instance

        Raises:
            ValueError: If the provider is not supported
        """
        provider_name = provider_name.lower()
        class_type = cls.provider_to_class.get(provider_name)
        
        if not class_type:
            supported_providers = ", ".join(cls.provider_to_class.keys())
            raise ValueError(
                f"Unsupported UserProfileStore provider: {provider_name}. "
                f"Currently supported providers are: {supported_providers}. "
                f"If you're using a different storage provider for Memory, please use one of the supported providers for UserMemory "
                f"or implement a UserProfileStore for your storage provider."
            )
        
        try:
            ProfileStoreClass = load_class(class_type)
            return ProfileStoreClass(**config)
        except (ImportError, AttributeError) as e:
            raise ImportError(
                f"Could not import UserProfileStore for provider '{provider_name}': {e}"
            ) from e
        except Exception as e:
            raise ValueError(
                f"Failed to create UserProfileStore for provider '{provider_name}': {e}"
            ) from e