from typing import Any, ClassVar, Dict, Optional

from pydantic import Field, model_validator

from powermem.storage.config.base import BaseVectorStoreConfig


class OceanBaseConfig(BaseVectorStoreConfig):
    try:
        from pyobvector import ObVecClient
    except ImportError:
        raise ImportError("The 'pyobvector' library is required. Please install it using 'pip install pyobvector'.")
    ObVecClient: ClassVar[type] = ObVecClient

    collection_name: str = Field("power_mem", description="Default name for the collection")

    # Connection parameters
    host: str = Field("127.0.0.1", description="OceanBase server host")
    port: str = Field("2881", description="OceanBase server port")
    user: str = Field("root@test", description="OceanBase username")
    password: str = Field("", description="OceanBase password")
    db_name: str = Field("test", description="OceanBase database name")

    # Vector index parameters
    index_type: str = Field("HNSW", description="Type of vector index (HNSW, IVF, FLAT, etc.)")
    vidx_metric_type: str = Field("l2", description="Distance metric (l2, inner_product, cosine)")
    embedding_model_dims: Optional[int] = Field(None, description="Dimension of vectors")

    # Advanced parameters
    vidx_algo_params: Optional[Dict[str, Any]] = Field(None, description="Index algorithm parameters")
    normalize: bool = Field(False, description="Whether to normalize vectors")
    include_sparse: bool = Field(False, description="Whether to include sparse vector support")
    hybrid_search: bool = Field(True, description="Whether to enable hybrid search")
    auto_configure_vector_index: bool = Field(True,
                                              description="Whether to automatically configure vector index settings")

    # Fulltext search parameters
    fulltext_parser: str = Field("ik", description="Fulltext parser type (ik, ngram, ngram2, beng, space)")

    # Field names
    primary_field: str = Field("id", description="Primary key field name")
    vector_field: str = Field("embedding", description="Vector field name")
    text_field: str = Field("document", description="Text field name")
    metadata_field: str = Field("metadata", description="Metadata field name")
    vidx_name: str = Field("vidx", description="Vector index name")

    vector_weight: float = Field(0.5, description="Weight for vector search")
    fts_weight: float = Field(0.5, description="Weight for fulltext search")

    model_config = {
        "arbitrary_types_allowed": True,
    }



class OceanBaseGraphConfig(OceanBaseConfig):
    # Graph search parameters
    max_hops: int = Field(3, description="Maximum number of hops for multi-hop graph search")
