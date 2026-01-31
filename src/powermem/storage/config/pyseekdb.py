from typing import Optional
from pydantic import Field
from .base import BaseVectorStoreConfig

class PySeekDBConfig(BaseVectorStoreConfig):
    """Configuration for PySeekDB vector store"""
    host: str = Field(default="127.0.0.1", description="OceanBase host")
    port: int = Field(default=2881, description="OceanBase port")
    user: str = Field(default="root@test", description="OceanBase user")
    password: str = Field(default="", description="OceanBase password")
    db_name: str = Field(default="test", description="Database name")

    collection_name: str = Field(default="powermem", description="Vector collection name")
    dimension: int = Field(default=1536, description="Vector dimension")
    metric_type: str = Field(default="l2", description="Metric type: l2, inner_product, cosine")
