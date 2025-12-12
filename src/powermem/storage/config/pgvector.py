from typing import Any, Optional

from pydantic import Field, model_validator

from powermem.storage.config.base import BaseVectorStoreConfig


class PGVectorConfig(BaseVectorStoreConfig):
    dbname: str = Field("postgres", description="Default name for the database")
    collection_name: str = Field("power_mem", description="Default name for the collection")
    embedding_model_dims: Optional[int] = Field(1536, description="Dimensions of the embedding model")
    user: Optional[str] = Field(None, description="Database user")
    password: Optional[str] = Field(None, description="Database password")
    host: Optional[str] = Field(None, description="Database host. Default is 127.0.0.1")
    port: Optional[int] = Field(None, description="Database port. Default is 1536")
    diskann: Optional[bool] = Field(False, description="Use diskann for approximate nearest neighbors search")
    hnsw: Optional[bool] = Field(True, description="Use hnsw for faster search")
    minconn: Optional[int] = Field(1, description="Minimum number of connections in the pool")
    maxconn: Optional[int] = Field(5, description="Maximum number of connections in the pool")
    # New SSL and connection options
    sslmode: Optional[str] = Field(None,
                                   description="SSL mode for PostgreSQL connection (e.g., 'require', 'prefer', 'disable')")
    connection_string: Optional[str] = Field(None,
                                             description="PostgreSQL connection string (overrides individual connection parameters)")
    connection_pool: Optional[Any] = Field(None,
                                           description="psycopg connection pool object (overrides connection string and individual parameters)")

    @model_validator(mode="before")
    @classmethod
    def check_auth_and_connection(cls, values):
        # If connection_pool is provided, skip validation of individual connection parameters
        if values.get("connection_pool") is not None:
            return values

        # If connection_string is provided, skip validation of individual connection parameters
        if values.get("connection_string") is not None:
            return values

        # Otherwise, validate individual connection parameters
        user, password = values.get("user"), values.get("password")
        host, port = values.get("host"), values.get("port")
        
        # Only validate if user explicitly provided values (not using defaults)
        if user is not None or password is not None:
            if not user or not password:
                raise ValueError("Both 'user' and 'password' must be provided when not using connection_string.")
        
        if host is not None or port is not None:
            if not host or not port:
                raise ValueError("Both 'host' and 'port' must be provided when not using connection_string.")
        
        return values
