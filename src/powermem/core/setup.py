"""
Setup utilities for powermem

This module provides setup functions compatible with initialization,
"""

import json
import os
import uuid
from pathlib import Path
from typing import Optional, Dict, Any
import logging

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings

from ..settings import settings_config

logger = logging.getLogger(__name__)

# Set up the directory path
VECTOR_ID = str(uuid.uuid4())
class _LocalSettings(BaseSettings):
    model_config = settings_config()

    powermem_dir: Path = Field(
        default=Path.home() / ".powermem", validation_alias="POWERMEM_DIR"
    )

    @field_validator("powermem_dir", mode="before")
    @classmethod
    def expand_user_path(cls, value: object) -> object:
        if isinstance(value, str):
            return Path(os.path.expanduser(value))
        return value


def get_powermem_dir() -> Path:
    return _LocalSettings().powermem_dir


powermem_dir = get_powermem_dir()
os.makedirs(powermem_dir, exist_ok=True)


def setup_config():
    """Setup configuration file."""
    config_path = os.path.join(powermem_dir, "config.json")
    if not os.path.exists(config_path):
        user_id = str(uuid.uuid4())
        config = {"user_id": user_id}
        with open(config_path, "w") as config_file:
            json.dump(config, config_file, indent=4)


def get_user_id() -> str:
    """Get or create user ID."""
    config_path = os.path.join(powermem_dir, "config.json")
    if not os.path.exists(config_path):
        setup_config()
    
    try:
        with open(config_path, "r") as config_file:
            config = json.load(config_file)
            user_id = config.get("user_id")
            return user_id
    except Exception:
        return "anonymous_user"


def from_config(config: Optional[Dict[str, Any]] = None, **kwargs):
    """
    Create Memory instance from configuration.

    Deprecated: prefer `create_memory()` or `auto_config()`.
    
    powermem now uses field names natively: 'embedder' and 'vector_store'.
    
    Args:
        config: Configuration dictionary
               - llm: LLM provider configuration
               - embedder: Embedder configuration
               - vector_store: Vector store config (uses OceanBase)
        **kwargs: Additional parameters
    
    Returns:
        Memory instance
        
    Example:
        ```python
        from powermem import from_config
        
        memory = from_config({
            "llm": {"provider": "openai", "config": {"api_key": "..."}},
            "embedder": {"provider": "openai", "config": {"api_key": "..."}},
            "vector_store": {"provider": "oceanbase", "config": {...}},
        })
        ```
    """
    from ..core.memory import Memory
    
    if config is None:
        # Use auto config from environment
        from ..config_loader import auto_config
        config = auto_config()
    
    converted_config = _convert_legacy_to_mem_config(config)
    
    return Memory(config=converted_config, **kwargs)


def _convert_legacy_to_mem_config(config: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert legacy powermem config format.
    
    Now powermem uses field names natively, so we only convert legacy format.
    
    Args:
        config: Legacy powermem configuration dictionary
        
    Returns:
        configuration dictionary
    """
    if "embedder" in config or "vector_store" in config:
        return config
    
    converted = {}
    
    # LLM stays the same
    if "llm" in config:
        converted["llm"] = config["llm"]
    
    # Convert embedding to embedder
    if "embedding" in config:
        converted["embedder"] = config["embedding"]
    
    # Convert database to vector_store
    if "database" in config:
        db_config = config["database"]
        converted["vector_store"] = {
            "provider": db_config.get("provider", "oceanbase"),
            "config": db_config.get("config", {})
        }
    else:
        converted["vector_store"] = {
            "provider": "oceanbase",
            "config": {}
        }
    
    return converted


def get_or_create_user_id(vector_store=None) -> str:
    """
    Store user_id in vector store and return it.
    
    Args:
        vector_store: Optional vector store instance
        
    Returns:
        User ID
    """
    user_id = get_user_id()
    
    if vector_store is None:
        return user_id
    
    # Try to get existing user_id from vector store
    try:
        existing = vector_store.get(vector_id=user_id)
        if existing and hasattr(existing, "payload") and existing.payload and "user_id" in existing.payload:
            return existing.payload["user_id"]
    except Exception:
        pass
    
    # If we get here, we need to insert the user_id
    try:
        dims = getattr(vector_store, "embedding_model_dims", 1536)
        vector_store.insert(
            vectors=[[0.1] * dims], 
            payloads=[{"user_id": user_id, "type": "user_identity"}], 
            ids=[user_id]
        )
    except Exception:
        pass
    
    return user_id
