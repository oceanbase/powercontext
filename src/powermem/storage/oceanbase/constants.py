"""
OceanBase storage constants

This module contains all constants used across OceanBase storage implementations.
"""

try:
    from pyobvector import VecIndexType
except ImportError:
    # Fallback for when pyobvector is not available
    class VecIndexType:
        HNSW = "HNSW"
        HNSW_SQ = "HNSW_SQ"
        IVFFLAT = "IVFFLAT"
        IVFSQ = "IVFSQ"
        IVFPQ = "IVFPQ"


# =============================================================================
# Connection Defaults
# =============================================================================

DEFAULT_OCEANBASE_CONNECTION = {
    "host": "127.0.0.1",
    "port": "2881",
    "user": "root@test",
    "password": "",
    "db_name": "test",
}


# =============================================================================
# Vector Index Configuration
# =============================================================================

# Index type groupings for easier management
INDEX_TYPE_HNSW = ("HNSW", "HNSW_SQ")
INDEX_TYPE_IVF = ("IVF", "IVF_FLAT", "IVF_SQ")
INDEX_TYPE_IVF_PQ = "IVF_PQ"

# Default vector metric type
DEFAULT_OCEANBASE_VECTOR_METRIC_TYPE = "l2"
DEFAULT_VIDX_NAME = "vidx"
DEFAULT_INDEX_TYPE = "HNSW"

# Default parameters for different index types
DEFAULT_OCEANBASE_HNSW_BUILD_PARAM = {"M": 16, "efConstruction": 200}
DEFAULT_OCEANBASE_HNSW_SEARCH_PARAM = {"efSearch": 64}
DEFAULT_OCEANBASE_IVF_BUILD_PARAM = {"nlist": 128}
DEFAULT_OCEANBASE_IVF_SEARCH_PARAM = {}
DEFAULT_OCEANBASE_IVF_PQ_BUILD_PARAM = {"nlist": 128, "m": 3}
DEFAULT_OCEANBASE_FLAT_BUILD_PARAM = {}
DEFAULT_OCEANBASE_FLAT_SEARCH_PARAM = {}

# Supported vector index types mapping
OCEANBASE_SUPPORTED_VECTOR_INDEX_TYPES = {
    "HNSW": VecIndexType.HNSW,
    "HNSW_SQ": VecIndexType.HNSW_SQ,
    "IVF": VecIndexType.IVFFLAT,
    "IVF_FLAT": VecIndexType.IVFFLAT,
    "IVF_SQ": VecIndexType.IVFSQ,
    "IVF_PQ": VecIndexType.IVFPQ,
    "FLAT": VecIndexType.IVFFLAT,
}

# Index type to build parameters mapping
OCEANBASE_BUILD_PARAMS_MAPPING = {
    "HNSW": DEFAULT_OCEANBASE_HNSW_BUILD_PARAM,
    "HNSW_SQ": DEFAULT_OCEANBASE_HNSW_BUILD_PARAM,
    "IVF": DEFAULT_OCEANBASE_IVF_BUILD_PARAM,
    "IVF_FLAT": DEFAULT_OCEANBASE_IVF_BUILD_PARAM,
    "IVF_SQ": DEFAULT_OCEANBASE_IVF_BUILD_PARAM,
    "IVF_PQ": DEFAULT_OCEANBASE_IVF_PQ_BUILD_PARAM,
    "FLAT": DEFAULT_OCEANBASE_FLAT_BUILD_PARAM,
}

# Index type to search parameters mapping
OCEANBASE_SEARCH_PARAMS_MAPPING = {
    "HNSW": DEFAULT_OCEANBASE_HNSW_SEARCH_PARAM,
    "HNSW_SQ": DEFAULT_OCEANBASE_HNSW_SEARCH_PARAM,
    "IVF": DEFAULT_OCEANBASE_IVF_SEARCH_PARAM,
    "IVF_FLAT": DEFAULT_OCEANBASE_IVF_SEARCH_PARAM,
    "IVF_SQ": DEFAULT_OCEANBASE_IVF_SEARCH_PARAM,
    "IVF_PQ": DEFAULT_OCEANBASE_IVF_SEARCH_PARAM,
    "FLAT": DEFAULT_OCEANBASE_FLAT_SEARCH_PARAM,
}


# =============================================================================
# Field Names
# =============================================================================

DEFAULT_METADATA_FIELD = "metadata"
DEFAULT_PRIMARY_FIELD = "id"
DEFAULT_VECTOR_FIELD = "embedding"
DEFAULT_TEXT_FIELD = "document"


# =============================================================================
# Fulltext Search
# =============================================================================

# Supported fulltext parsers
OCEANBASE_SUPPORTED_FULLTEXT_PARSERS = ["ik", "ngram", "ngram2", "beng", "space"]
DEFAULT_FULLTEXT_PARSER = "ik"


# =============================================================================
# Graph Storage Configuration
# =============================================================================

# Table names for graph storage
TABLE_ENTITIES = "graph_entities"
TABLE_RELATIONSHIPS = "graph_relationships"

# Graph search parameters
DEFAULT_SIMILARITY_THRESHOLD = 0.7
DEFAULT_PATH_STRING_LENGTH = 500
DEFAULT_SEARCH_LIMIT = 100
DEFAULT_BM25_TOP_N = 15


# =============================================================================
# LLM Configuration
# =============================================================================

# Default LLM provider
DEFAULT_LLM_PROVIDER = "openai"

# Structured LLM providers
STRUCTURED_LLM_PROVIDERS = ["openai_structured"]


# =============================================================================
# Helper Functions
# =============================================================================

def get_default_build_params(index_type: str) -> dict:
    """Get default build parameters for the given index type."""
    if index_type in INDEX_TYPE_HNSW:
        return DEFAULT_OCEANBASE_HNSW_BUILD_PARAM.copy()
    elif index_type in INDEX_TYPE_IVF:
        return DEFAULT_OCEANBASE_IVF_BUILD_PARAM.copy()
    elif index_type == INDEX_TYPE_IVF_PQ:
        return DEFAULT_OCEANBASE_IVF_PQ_BUILD_PARAM.copy()
    else:
        return DEFAULT_OCEANBASE_FLAT_BUILD_PARAM.copy()


def get_default_search_params(index_type: str) -> dict:
    """Get default search parameters for the given index type."""
    if index_type in INDEX_TYPE_HNSW:
        return DEFAULT_OCEANBASE_HNSW_SEARCH_PARAM.copy()
    elif index_type in INDEX_TYPE_IVF:
        return DEFAULT_OCEANBASE_IVF_SEARCH_PARAM.copy()
    else:
        return DEFAULT_OCEANBASE_FLAT_SEARCH_PARAM.copy()


def is_structured_llm_provider(provider: str) -> bool:
    """Check if the given provider is a structured LLM provider."""
    return provider in STRUCTURED_LLM_PROVIDERS
