from typing import Optional

from pydantic import AliasChoices, Field
from powermem.settings import settings_config

from powermem.storage.config.base import BaseVectorStoreConfig


class SQLiteConfig(BaseVectorStoreConfig):
    """Configuration for SQLite vector store."""
    
    _provider_name = "sqlite"
    _class_path = "powermem.storage.sqlite.sqlite_vector_store.SQLiteVectorStore"
    
    model_config = settings_config("VECTOR_STORE_", extra="forbid", env_file=None)
    
    database_path: str = Field(
        default="./data/powermem_dev.db",
        validation_alias=AliasChoices(
            "database_path",
            "SQLITE_PATH",
        ),
        description="Path to SQLite database file"
    )
    
    collection_name: str = Field(
        default="memories",
        validation_alias=AliasChoices(
            "collection_name",
            "SQLITE_COLLECTION",
        ),
        description="Name of the collection/table"
    )
    
    enable_wal: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "enable_wal",
            "SQLITE_ENABLE_WAL",
        ),
        description="Enable Write-Ahead Logging for better concurrency"
    )
    
    timeout: int = Field(
        default=30,
        validation_alias=AliasChoices(
            "timeout",
            "SQLITE_TIMEOUT",
        ),
        description="Connection timeout in seconds"
    )

