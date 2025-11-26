"""
Storage configuration management

This module handles storage configuration and validation.
"""

from typing import Dict, Optional, Union

from pydantic import BaseModel, Field, model_validator

from powermem.integrations.llm.configs import LLMConfig
from powermem.storage.config.oceanbase import OceanBaseGraphConfig
from powermem.storage.factory import VectorStoreFactory


class VectorStoreConfig(BaseModel):
    provider: str = Field(
        description="Provider of the vector store (e.g., 'oceanbase', 'pgvector')",
        default="oceanbase",
    )
    config: Optional[Dict] = Field(
        description="Configuration for the specific vector store",
        default=None
    )

    _provider_configs: Dict[str, str] = {
        "oceanbase": "OceanBaseConfig",
        "pgvector": "PGVectorConfig",
        "sqlite": "SQLiteConfig",
    }

    @model_validator(mode="after")
    def validate_config(self) -> "VectorStoreConfig":
        """
        Validate the configuration without converting to provider-specific config class.
        The conversion is handled by VectorStoreFactory.create() when needed.
        """
        provider = self.provider
        config = self.config

        if provider is not None and provider == "postgres":
            provider = "pgvector"

        # Check both initialized providers and Factory-registered providers
        if provider not in self._provider_configs and provider not in VectorStoreFactory.provider_to_class:
            raise ValueError(f"Unsupported vector store provider: {provider}")

        if config is None:
            self.config = {}
            return self

        if not isinstance(config, dict):
            raise ValueError(f"Config must be a dictionary, got {type(config)}")

        # Handle connection_args for backward compatibility
        # If connection_args exists, flatten it into the main config
        if "connection_args" in config:
            connection_args = config.pop("connection_args")
            if isinstance(connection_args, dict):
                # Merge connection_args into config (connection_args values take precedence)
                for key, value in connection_args.items():
                    if key not in config:
                        # Convert port to string if it's an int (for OceanBase compatibility)
                        if key == "port" and isinstance(value, int):
                            config[key] = str(value)
                        else:
                            config[key] = value
                self.config = config

        # Convert port to string if it's an int (for OceanBase compatibility)
        # This handles both direct port field and port from connection_args
        if "port" in config and isinstance(config["port"], int):
            config["port"] = str(config["port"])
            self.config = config

        # Validate config by attempting to create provider-specific config instance
        # This ensures the config has valid fields, but we don't store the converted object
        if provider in self._provider_configs:
            module = __import__(
                f"powermem.storage.config.{provider}",
                fromlist=[self._provider_configs[provider]],
            )
            config_class = getattr(module, self._provider_configs[provider])

            # Add default path if needed
            if "path" not in config and "path" in config_class.__annotations__:
                config["path"] = f"/tmp/{provider}"
                self.config = config

            # Validate by creating instance (throws error if invalid)
            try:
                config_class(**config)
            except Exception as e:
                raise ValueError(f"Invalid configuration for {provider}: {e}")

        # Keep config as dict, don't convert to config_class instance
        return self

class GraphStoreConfig(BaseModel):
    enabled: bool = Field(
        description="Whether to enable graph store",
        default=False,
    )
    provider: str = Field(
        description="Provider of the data store (e.g., 'oceanbase')",
        default="oceanbase",
    )
    config: Optional[Union[Dict, OceanBaseGraphConfig]] = Field(
        description="Configuration for the specific data store",
        default=None
    )
    llm: Optional[LLMConfig] = Field(
        description="LLM configuration for querying the graph store",
        default=None
    )
    custom_prompt: Optional[str] = Field(
        description="Custom prompt to fetch entities from the given text",
        default=None
    )
    custom_extract_relations_prompt: Optional[str] = Field(
        description="Custom prompt for extracting relations from text",
        default=None
    )
    custom_update_graph_prompt: Optional[str] = Field(
        description="Custom prompt for updating graph memories",
        default=None
    )
    custom_delete_relations_prompt: Optional[str] = Field(
        description="Custom prompt for deleting relations",
        default=None
    )

    @model_validator(mode="after")
    def validate_config(self) -> "GraphStoreConfig":
        """
        Validate the configuration without converting to provider-specific config class.
        Keep config as dict for consistency.
        """
        if self.config is None:
            self.config = {}
            return self

        # If config is a Pydantic BaseModel instance, convert it to dict
        if isinstance(self.config, BaseModel):
            self.config = self.config.model_dump()

        if not isinstance(self.config, dict):
            raise ValueError(f"Config must be a dictionary or BaseModel instance, got {type(self.config)}")


        # Validate config based on provider
        provider = self.provider
        if provider == "oceanbase":
            try:
                OceanBaseGraphConfig(**self.config)
            except Exception as e:
                raise ValueError(f"Invalid configuration for {provider}: {e}")
        else:
            raise ValueError(f"Unsupported graph store provider: {provider}")

        # Keep config as dict, don't convert
        return self