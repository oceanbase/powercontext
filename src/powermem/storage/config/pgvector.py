from typing import Any, Optional

from pydantic import AliasChoices, Field, model_validator
from powermem.settings import settings_config

from powermem.storage.config.base import BaseVectorStoreConfig


class PGVectorConfig(BaseVectorStoreConfig):
    _provider_name = "pgvector"
    _class_path = "powermem.storage.pgvector.pgvector.PGVectorStore"
    
    model_config = settings_config("VECTOR_STORE_", extra="forbid", env_file=None)

    dbname: str = Field(
        default="postgres",
        validation_alias=AliasChoices(
            "dbname",
            "POSTGRES_DATABASE",
        ),
        description="Default name for the database"
    )
    
    collection_name: str = Field(
        default="power_mem",
        validation_alias=AliasChoices(
            "collection_name",
            "POSTGRES_COLLECTION",
        ),
        description="Default name for the collection"
    )
    
    embedding_model_dims: Optional[int] = Field(
        default=1536,
        validation_alias=AliasChoices(
            "embedding_model_dims",
            "POSTGRES_EMBEDDING_MODEL_DIMS",
        ),
        description="Dimensions of the embedding model"
    )
    
    user: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices(
            "POSTGRES_USER",
            "user", # avoid using system USER environment variable first
        ),
        description="Database user"
    )
    
    password: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices(
            "password",
            "POSTGRES_PASSWORD",
        ),
        description="Database password"
    )
    
    host: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices(
            "host",
            "POSTGRES_HOST",
        ),
        description="Database host. Default is 127.0.0.1"
    )
    
    port: Optional[int] = Field(
        default=None,
        validation_alias=AliasChoices(
            "port",
            "POSTGRES_PORT",
        ),
        description="Database port. Default is 5432"
    )
    
    diskann: Optional[bool] = Field(
        default=False,
        validation_alias=AliasChoices(
            "diskann",
            "POSTGRES_DISKANN",
        ),
        description="Use diskann for approximate nearest neighbors search"
    )
    
    hnsw: Optional[bool] = Field(
        default=True,
        validation_alias=AliasChoices(
            "hnsw",
            "POSTGRES_HNSW",
        ),
        description="Use hnsw for faster search"
    )
    
    minconn: Optional[int] = Field(
        default=1,
        description="Minimum number of connections in the pool"
    )
    
    maxconn: Optional[int] = Field(
        default=5,
        description="Maximum number of connections in the pool"
    )
    
    sslmode: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices(
            "sslmode",
            "DATABASE_SSLMODE",
        ),
        description="SSL mode for PostgreSQL connection"
    )
    
    connection_string: Optional[str] = Field(
        default=None,
        description="PostgreSQL connection string"
    )
    
    connection_pool: Optional[Any] = Field(
        default=None,
        description="psycopg connection pool object"
    )

    @model_validator(mode="before")
    @classmethod
    def check_auth_and_connection(cls, values):
        if values.get("connection_pool") is not None:
            return values
        if values.get("connection_string") is not None:
            return values
        user, password = values.get("user"), values.get("password")
        host, port = values.get("host"), values.get("port")
        if user is not None or password is not None:
            if not user or not password:
                raise ValueError("Both 'user' and 'password' must be provided.")
        if host is not None or port is not None:
            if not host or not port:
                raise ValueError("Both 'host' and 'port' must be provided.")
        return values
