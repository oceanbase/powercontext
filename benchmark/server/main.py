"""
PowerMem Benchmark Server

⚠️ IMPORTANT: This server is NOT the official PowerMem server API.
It is only used for benchmark testing scenarios and should not be used in production.

This server provides REST APIs for managing and searching memories.

Configuration:
    All configuration is done through environment variables. 
    Copy .env.example to .env and modify the values as needed.
    
    Example:
        cp benchmark/server/.env.example benchmark/server/.env
        # Then edit benchmark/server/.env with your settings
"""

import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, status
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from powermem import Memory

# ============================================================================
# Logging Configuration
# ============================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ]
)
logger = logging.getLogger(__name__)

# ============================================================================
# Configuration - Using Pydantic Settings (similar to src/server/config.py)
# ============================================================================

def _parse_boolish(value: object) -> object:
    """
    Backward-compatible boolean parsing.

    Historically we accepted values like: true/1/yes/on/enabled.
    `pydantic` already accepts many truthy strings, but "enabled"/"disabled" are not
    guaranteed across versions, so we normalize explicitly.
    """
    if value is None or isinstance(value, bool):
        return value

    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"1", "true", "t", "yes", "y", "on", "enabled"}:
            return True
        if text in {"0", "false", "f", "no", "n", "off", "disabled"}:
            return False

    return value


class BenchmarkSettings(BaseSettings):
    """Configuration settings for PowerMem Benchmark Server."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # LLM Configuration
    llm_provider: str = Field(default="openai")
    llm_api_key: str = Field(...)  # Required
    llm_model: str = Field(default="gpt-4o")
    llm_temperature: float = Field(default=0.2)
    llm_max_tokens: int = Field(default=1000)
    llm_top_p: float = Field(default=0.8)
    llm_top_k: int = Field(default=50)

    # LLM Base URLs
    openai_llm_base_url: str = Field(default="https://api.openai.com/v1")
    qwen_llm_base_url: str = Field(default="https://dashscope.aliyuncs.com/api/v1")
    siliconflow_llm_base_url: str = Field(default="https://api.siliconflow.cn/v1")
    ollama_llm_base_url: str = Field(default="")
    vllm_llm_base_url: str = Field(default="")
    anthropic_llm_base_url: str = Field(default="https://api.anthropic.com")
    deepseek_llm_base_url: str = Field(default="https://api.deepseek.com")

    # Embedding Configuration
    embedding_provider: str = Field(default="qwen")
    embedding_api_key: str = Field(...)  # Required
    embedding_model: str = Field(default="text-embedding-v4")
    embedding_dims: int = Field(default=1536)

    # Embedding Base URLs
    qwen_embedding_base_url: str = Field(default="https://dashscope.aliyuncs.com/api/v1")
    openai_embedding_base_url: str = Field(default="https://api.openai.com/v1")
    siliconflow_embedding_base_url: str = Field(default="https://api.siliconflow.cn/v1")
    huggingface_embedding_base_url: str = Field(default="")
    lmstudio_embedding_base_url: str = Field(default="")
    ollama_embedding_base_url: str = Field(default="")

    # Database Configuration
    database_provider: str = Field(default="oceanbase")
    vector_weight: float = Field(default=0.5)
    fts_weight: float = Field(default=0.5)

    # OceanBase Configuration
    oceanbase_host: str = Field(default="127.0.0.1")
    oceanbase_port: str = Field(default="2881")
    oceanbase_user: str = Field(default="root@sys")
    oceanbase_password: str = Field(default="")
    oceanbase_database: str = Field(default="ai_work")
    oceanbase_collection: str = Field(default="powermem_collection")
    oceanbase_embedding_model_dims: int = Field(default=1536)
    oceanbase_index_type: str = Field(default="HNSW")
    oceanbase_vector_metric_type: str = Field(default="l2")
    oceanbase_text_field: str = Field(default="document")
    oceanbase_vector_field: str = Field(default="embedding")
    oceanbase_primary_field: str = Field(default="id")
    oceanbase_metadata_field: str = Field(default="metadata")
    oceanbase_vidx_name: str = Field(default="memories_vidx")
    oceanbase_enable_native_hybrid: bool = Field(default=False)

    # PostgreSQL Configuration
    postgres_host: str = Field(default="127.0.0.1")
    postgres_port: str = Field(default="5432")
    postgres_user: str = Field(default="postgres")
    postgres_password: str = Field(default="")
    postgres_database: str = Field(default="ai_work")
    postgres_collection: str = Field(default="memories")
    postgres_embedding_model_dims: int = Field(default=1536)
    postgres_diskann: bool = Field(default=True)
    postgres_hnsw: bool = Field(default=True)

    # Token Counting Configuration
    token_counting: bool = Field(default=True)

    # Application Configuration
    history_db_path: str = Field(default="history.db")
    config_version: str = Field(default="v1.1")

    # Reranker Configuration
    reranker_enabled: bool = Field(default=True)
    reranker_provider: str = Field(default="qwen")
    reranker_model: str = Field(default="qwen3-rerank")
    reranker_api_key: Optional[str] = Field(default=None)  # Falls back to embedding_api_key
    reranker_base_url: str = Field(default="")

    # Sparse Embedding Configuration
    sparse_vector_enable: bool = Field(default=False)
    sparse_embedder_provider: str = Field(default="qwen")
    sparse_embedder_api_key: Optional[str] = Field(default=None)  # Falls back to embedding_api_key
    sparse_embedder_model: Optional[str] = Field(default=None)  # Falls back to embedding_model
    sparse_embedding_base_url: str = Field(default="")
    sparse_embedder_dims: int = Field(default=1536)

    @field_validator(
        "llm_provider",
        "embedding_provider",
        "database_provider",
        "reranker_provider",
        "sparse_embedder_provider",
        mode="before",
    )
    @classmethod
    def normalize_provider(cls, value: object) -> str:
        """Normalize provider names to lowercase."""
        if isinstance(value, str):
            return value.lower()
        return value

    @field_validator(
        "token_counting",
        "reranker_enabled",
        "sparse_vector_enable",
        "postgres_diskann",
        "postgres_hnsw",
        mode="before",
    )
    @classmethod
    def normalize_bool_fields(cls, value: object) -> object:
        """Normalize boolean fields."""
        return _parse_boolish(value)

    @model_validator(mode="after")
    def set_defaults(self) -> "BenchmarkSettings":
        """Set default values for fields that depend on other fields."""
        # Set reranker_api_key to embedding_api_key if not provided
        if not self.reranker_api_key:
            self.reranker_api_key = self.embedding_api_key

        # Set sparse_embedder_api_key to embedding_api_key if not provided
        if not self.sparse_embedder_api_key:
            self.sparse_embedder_api_key = self.embedding_api_key

        # Set sparse_embedder_model to embedding_model if not provided
        if not self.sparse_embedder_model:
            self.sparse_embedder_model = self.embedding_model

        # Set sparse_embedding_base_url based on provider if not provided
        if not self.sparse_embedding_base_url:
            base_url_map = {
                "qwen": self.qwen_embedding_base_url,
                "openai": self.openai_embedding_base_url,
                "siliconflow": self.siliconflow_embedding_base_url,
                "huggingface": self.huggingface_embedding_base_url,
                "lmstudio": self.lmstudio_embedding_base_url,
                "ollama": self.ollama_embedding_base_url,
            }
            self.sparse_embedding_base_url = base_url_map.get(self.sparse_embedder_provider, "")

        return self

    def model_post_init(self, __context: Any) -> None:
        """Post-initialization hook to set DASHSCOPE_BASE_URL for Qwen reranker."""
        super().model_post_init(__context)
        # For Qwen reranker, set DASHSCOPE_BASE_URL if RERANKER_BASE_URL is provided
        if self.reranker_base_url and self.reranker_provider == "qwen":
            os.environ["DASHSCOPE_BASE_URL"] = self.reranker_base_url

    def get_llm_base_url(self, provider: str) -> str:
        """Get base URL for LLM provider."""
        url_map = {
            "openai": self.openai_llm_base_url,
            "qwen": self.qwen_llm_base_url,
            "siliconflow": self.siliconflow_llm_base_url,
            "ollama": self.ollama_llm_base_url,
            "vllm": self.vllm_llm_base_url,
            "anthropic": self.anthropic_llm_base_url,
            "deepseek": self.deepseek_llm_base_url,
        }
        return url_map.get(provider, "")

    def get_embedding_base_url(self, provider: str) -> str:
        """Get base URL for embedding provider."""
        url_map = {
            "qwen": self.qwen_embedding_base_url,
            "openai": self.openai_embedding_base_url,
            "siliconflow": self.siliconflow_embedding_base_url,
            "huggingface": self.huggingface_embedding_base_url,
            "lmstudio": self.lmstudio_embedding_base_url,
            "ollama": self.ollama_embedding_base_url,
        }
        return url_map.get(provider, "")


# Load configuration with custom env file path
def load_benchmark_settings() -> BenchmarkSettings:
    """Load benchmark settings from .env file in the same directory as this script."""
    script_dir = Path(__file__).parent
    env_path = script_dir / ".env"

    if env_path.exists():
        logger.info(f"Loading environment variables from {env_path}")
        return BenchmarkSettings(_env_file=str(env_path))
    else:
        logger.info("Loading environment variables from default locations")
        return BenchmarkSettings()


# Initialize settings
settings = load_benchmark_settings()

# Backward compatibility: Create aliases for existing code
LLM_PROVIDER = settings.llm_provider
LLM_API_KEY = settings.llm_api_key
LLM_MODEL = settings.llm_model
LLM_TEMPERATURE = settings.llm_temperature
LLM_MAX_TOKENS = settings.llm_max_tokens
LLM_TOP_P = settings.llm_top_p
LLM_TOP_K = settings.llm_top_k

EMBEDDING_PROVIDER = settings.embedding_provider
EMBEDDING_API_KEY = settings.embedding_api_key
EMBEDDING_MODEL = settings.embedding_model
EMBEDDING_DIMS = settings.embedding_dims

DATABASE_PROVIDER = settings.database_provider
VECTOR_WEIGHT = settings.vector_weight
FTS_WEIGHT = settings.fts_weight

TOKEN_COUNTING = settings.token_counting
HISTORY_DB_PATH = settings.history_db_path
CONFIG_VERSION = settings.config_version

RERANKER_ENABLED = settings.reranker_enabled
RERANKER_PROVIDER = settings.reranker_provider
RERANKER_MODEL = settings.reranker_model
RERANKER_API_KEY = settings.reranker_api_key
RERANKER_BASE_URL = settings.reranker_base_url

SPARSE_VECTOR_ENABLE = settings.sparse_vector_enable
SPARSE_EMBEDDER_PROVIDER = settings.sparse_embedder_provider
SPARSE_EMBEDDER_API_KEY = settings.sparse_embedder_api_key
SPARSE_EMBEDDER_MODEL = settings.sparse_embedder_model
SPARSE_EMBEDDING_BASE_URL = settings.sparse_embedding_base_url
SPARSE_EMBEDDER_DIMS = settings.sparse_embedder_dims

OCEANBASE_ENABLE_NATIVE_HYBRID = settings.oceanbase_enable_native_hybrid


def load_config() -> Dict[str, Any]:
    """Load and build configuration dictionary from settings."""
    # Select vector store based on DATABASE_PROVIDER
    if DATABASE_PROVIDER == "oceanbase":
        vector_store_config = {
            "host": settings.oceanbase_host,
            "port": settings.oceanbase_port,
            "user": settings.oceanbase_user,
            "password": settings.oceanbase_password,
            "db_name": settings.oceanbase_database,
            "collection_name": settings.oceanbase_collection,
            "embedding_model_dims": settings.oceanbase_embedding_model_dims,
            "index_type": settings.oceanbase_index_type,
            "vidx_metric_type": settings.oceanbase_vector_metric_type,
            "vector_weight": VECTOR_WEIGHT,
            "fts_weight": FTS_WEIGHT,
            # Optional field names (use defaults if not explicitly set)
            "primary_field": settings.oceanbase_primary_field,
            "vector_field": settings.oceanbase_vector_field,
            "text_field": settings.oceanbase_text_field,
            "metadata_field": settings.oceanbase_metadata_field,
            "vidx_name": settings.oceanbase_vidx_name,
        }

        # Add sparse vector support if enabled
        if SPARSE_VECTOR_ENABLE:
            vector_store_config["include_sparse"] = True
        if OCEANBASE_ENABLE_NATIVE_HYBRID:
            vector_store_config['enable_native_hybrid'] = True

        vector_store = {
            "provider": "oceanbase",
            "config": vector_store_config,
        }
    elif DATABASE_PROVIDER == "postgres":
        vector_store = {
            "provider": "pgvector",
            "config": {
                "host": settings.postgres_host,
                "port": settings.postgres_port,
                "user": settings.postgres_user,
                "password": settings.postgres_password,
                "dbname": settings.postgres_database,
                "collection_name": settings.postgres_collection,
                "embedding_model_dims": settings.postgres_embedding_model_dims,
                "diskann": settings.postgres_diskann,
                "hnsw": settings.postgres_hnsw,
            },
        }
    else:
        raise ValueError(
            f"Unsupported DATABASE_PROVIDER: {DATABASE_PROVIDER}. "
            f"Must be 'oceanbase' or 'postgres'"
        )

    # Build LLM configuration
    llm_base_url = settings.get_llm_base_url(LLM_PROVIDER)
    llm_config = {
        "api_key": LLM_API_KEY,
        "temperature": LLM_TEMPERATURE,
        "model": LLM_MODEL,
    }

    # Add provider-specific base URL if available
    # Note: Each provider has its own specific field name for base URL
    if llm_base_url:
        if LLM_PROVIDER == "openai":
            llm_config["openai_base_url"] = llm_base_url
        elif LLM_PROVIDER == "qwen":
            # Qwen uses dashscope_base_url, not qwen_base_url
            llm_config["dashscope_base_url"] = llm_base_url
        elif LLM_PROVIDER == "deepseek":
            llm_config["deepseek_base_url"] = llm_base_url
        elif LLM_PROVIDER == "vllm":
            llm_config["vllm_base_url"] = llm_base_url
        elif LLM_PROVIDER == "ollama":
            llm_config["ollama_base_url"] = llm_base_url
        elif LLM_PROVIDER == "anthropic":
            llm_config["anthropic_base_url"] = llm_base_url
        elif LLM_PROVIDER == "siliconflow":
            # SiliconFlow may use a generic base_url or siliconflow_base_url
            # Using siliconflow_base_url as fallback
            llm_config["siliconflow_base_url"] = llm_base_url

    # Add optional parameters
    if LLM_MAX_TOKENS:
        llm_config["max_tokens"] = LLM_MAX_TOKENS
    if LLM_TOP_P:
        llm_config["top_p"] = LLM_TOP_P
    if LLM_TOP_K:
        llm_config["top_k"] = LLM_TOP_K

    # Build embedder configuration
    embedding_base_url = settings.get_embedding_base_url(EMBEDDING_PROVIDER)
    embedder_config = {
        "api_key": EMBEDDING_API_KEY,
        "model": EMBEDDING_MODEL,
        "embedding_dims": EMBEDDING_DIMS,
    }

    # Add provider-specific base URL if available
    # Note: Each provider has its own specific field name for base URL
    if embedding_base_url:
        if EMBEDDING_PROVIDER == "openai":
            embedder_config["openai_base_url"] = embedding_base_url
        elif EMBEDDING_PROVIDER == "qwen":
            # Qwen uses dashscope_base_url, not qwen_base_url
            embedder_config["dashscope_base_url"] = embedding_base_url
        elif EMBEDDING_PROVIDER == "siliconflow":
            embedder_config["siliconflow_base_url"] = embedding_base_url
        elif EMBEDDING_PROVIDER == "huggingface":
            embedder_config["huggingface_base_url"] = embedding_base_url
        elif EMBEDDING_PROVIDER == "lmstudio":
            embedder_config["lmstudio_base_url"] = embedding_base_url
        elif EMBEDDING_PROVIDER == "ollama":
            embedder_config["ollama_base_url"] = embedding_base_url

    # Build configuration dictionary
    config = {
        "version": CONFIG_VERSION,
        "vector_store": vector_store,
        "llm": {
            "provider": LLM_PROVIDER,
            "config": llm_config,
        },
        "embedder": {
            "provider": EMBEDDING_PROVIDER,
            "config": embedder_config,
        },
        "history_db_path": HISTORY_DB_PATH,
    }

    # Add reranker if enabled
    if RERANKER_ENABLED:
        reranker_config = {
            "api_key": RERANKER_API_KEY,
            "model": RERANKER_MODEL,
        }
        # Add base URL for Qwen reranker (uses dashscope_base_url)
        if RERANKER_BASE_URL and RERANKER_PROVIDER == "qwen":
            reranker_config["dashscope_base_url"] = RERANKER_BASE_URL

        config["reranker"] = {
            "enabled": True,
            "provider": RERANKER_PROVIDER,
            "config": reranker_config,
        }

    # Add sparse embedder if enabled
    if SPARSE_VECTOR_ENABLE:
        sparse_config = {
            "api_key": SPARSE_EMBEDDER_API_KEY,
            "model": SPARSE_EMBEDDER_MODEL,
            "embedding_dims": SPARSE_EMBEDDER_DIMS,
        }
        # Sparse embedder uses generic base_url field, not provider-specific
        if SPARSE_EMBEDDING_BASE_URL:
            sparse_config["base_url"] = SPARSE_EMBEDDING_BASE_URL

        config["sparse_embedder"] = {
            "provider": SPARSE_EMBEDDER_PROVIDER,
            "config": sparse_config,
        }

    return config


# ============================================================================
# Token Tracking
# ============================================================================


class AsyncTokenTracker:
    """Track token usage for LLM API calls."""

    def __init__(self):
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0
        self.cached_tokens = 0
        self._lock = asyncio.Lock()

    async def track_token(self, usage: Any) -> None:
        """Asynchronously update token count."""
        async with self._lock:
            if hasattr(usage, "prompt_tokens"):
                self.prompt_tokens += usage.prompt_tokens
            if hasattr(usage, "completion_tokens"):
                self.completion_tokens += usage.completion_tokens
            if hasattr(usage, "total_tokens"):
                self.total_tokens += usage.total_tokens
            if hasattr(usage, "prompt_tokens_details") and usage.prompt_tokens_details:
                if hasattr(usage.prompt_tokens_details, "cached_tokens"):
                    self.cached_tokens += usage.prompt_tokens_details.cached_tokens

    def get_token_count(self) -> int:
        """Get total token count."""
        return self.total_tokens

    def get_detailed_stats(self) -> Dict[str, int]:
        """Get detailed token statistics."""
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "cached_tokens": self.cached_tokens,
        }

    def reset(self) -> None:
        """Reset all token counts."""
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0
        self.cached_tokens = 0


async_token_tracker = AsyncTokenTracker()


def setup_token_counting() -> None:
    """Setup token counting for OpenAI API if enabled."""
    if not TOKEN_COUNTING:
        logger.info("Token counting is disabled")
        return

    try:
        logger.info("Token counting enabled")
        from openai.resources.chat.completions.completions import Completions
        import openai.resources.chat.chat

        class CountCompletions(Completions):
            """Wrapper for OpenAI Completions to track token usage."""

            def _safe_async_track_token(self, token_count: Any) -> None:
                """Thread-safe async token tracking."""
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        asyncio.create_task(async_token_tracker.track_token(token_count))
                    else:
                        loop.run_until_complete(async_token_tracker.track_token(token_count))
                except RuntimeError:
                    asyncio.run(async_token_tracker.track_token(token_count))

            def create(self, *args: Any, **kwargs: Any) -> Any:
                """Override create to track token usage."""
                resp = super().create(*args, **kwargs)
                if hasattr(resp, "usage") and resp.usage:
                    self._safe_async_track_token(resp.usage)
                return resp

        module = sys.modules["openai.resources.chat.chat"]
        setattr(module, "Completions", CountCompletions)
        logger.info("Token counting setup completed")
    except ImportError:
        logger.warning("OpenAI module not found, token counting will not work")
    except Exception as e:
        logger.error(f"Failed to setup token counting: {e}", exc_info=True)


# ============================================================================
# Initialize Application
# ============================================================================

try:
    DEFAULT_CONFIG = load_config()
    MEMORY_INSTANCE = Memory.from_config(DEFAULT_CONFIG)
    logger.info("Memory instance initialized successfully")
except Exception as e:
    logger.error(f"Failed to initialize memory instance: {e}", exc_info=True)
    raise

# Setup token counting
setup_token_counting()

# ============================================================================
# FastAPI Application
# ============================================================================

app = FastAPI(
    title="PowerMem Benchmark REST APIs",
    description="A REST API for managing and searching memories for benchmark testing scenarios.",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)


# ============================================================================
# Request/Response Models
# ============================================================================


class Message(BaseModel):
    """Message model for memory storage."""

    role: str = Field(..., description="Role of the message (user or assistant).")
    content: str = Field(..., description="Message content.")

    @field_validator("role")
    @classmethod
    def validate_role(cls, v: str) -> str:
        """Validate role value."""
        if v.lower() not in ("user", "assistant", "system"):
            raise ValueError("Role must be 'user', 'assistant', or 'system'")
        return v.lower()


class MemoryCreate(BaseModel):
    """Request model for creating memories."""

    messages: List[Message] = Field(..., description="List of messages to store.")
    user_id: Optional[str] = Field(None, description="User identifier.")
    agent_id: Optional[str] = Field(None, description="Agent identifier.")
    run_id: Optional[str] = Field(None, description="Run identifier.")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Additional metadata.")


class SearchRequest(BaseModel):
    """Request model for searching memories."""

    query: str = Field(..., description="Search query.")
    user_id: Optional[str] = Field(None, description="User identifier.")
    run_id: Optional[str] = Field(None, description="Run identifier.")
    agent_id: Optional[str] = Field(None, description="Agent identifier.")
    filters: Optional[Dict[str, Any]] = Field(None, description="Additional filters.")


# ============================================================================
# Helper Functions
# ============================================================================


def validate_identifiers(
    user_id: Optional[str] = None,
    run_id: Optional[str] = None,
    agent_id: Optional[str] = None,
) -> None:
    """Validate that at least one identifier is provided."""
    if not any([user_id, run_id, agent_id]):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one identifier (user_id, agent_id, run_id) is required.",
        )


def build_identifier_params(
    user_id: Optional[str] = None,
    run_id: Optional[str] = None,
    agent_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Build parameters dictionary from identifiers."""
    return {
        k: v
        for k, v in {
            "user_id": user_id,
            "run_id": run_id,
            "agent_id": agent_id,
        }.items()
        if v is not None
    }


# ============================================================================
# API Routes
# ============================================================================


@app.get("/", include_in_schema=False)
async def root():
    """Redirect root to API documentation."""
    return RedirectResponse(url="/docs")


@app.post("/configure", summary="Configure PowerMem", status_code=status.HTTP_200_OK)
def set_config(config: Dict[str, Any]) -> Dict[str, str]:
    """Set memory configuration."""
    global MEMORY_INSTANCE
    try:
        MEMORY_INSTANCE = Memory.from_config(config)
        logger.info("Configuration updated successfully")
        return {"message": "Configuration set successfully"}
    except Exception as e:
        logger.exception("Error in set_config:")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to set configuration: {str(e)}",
        )


@app.post("/memories", summary="Create memories", status_code=status.HTTP_201_CREATED)
def add_memory(memory_create: MemoryCreate) -> JSONResponse:
    """Store new memories."""
    validate_identifiers(
        memory_create.user_id,
        memory_create.run_id,
        memory_create.agent_id,
    )

    try:
        params = {
            k: v
            for k, v in memory_create.model_dump().items()
            if v is not None and k != "messages"
        }
        response = MEMORY_INSTANCE.add(
            messages=[m.model_dump() for m in memory_create.messages],
            **params,
        )
        logger.info(f"Memory added successfully: {len(memory_create.messages)} messages")
        return JSONResponse(content=response)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error in add_memory:")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to add memory: {str(e)}",
        )


@app.get("/memories", summary="Get memories", status_code=status.HTTP_200_OK)
def get_all_memories(
    user_id: Optional[str] = None,
    run_id: Optional[str] = None,
    agent_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Retrieve stored memories."""
    validate_identifiers(user_id, run_id, agent_id)

    try:
        params = build_identifier_params(user_id, run_id, agent_id)
        result = MEMORY_INSTANCE.get_all(**params)
        logger.info(f"Retrieved memories for identifiers: {params}")
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error in get_all_memories:")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve memories: {str(e)}",
        )


@app.get("/memories/{memory_id}", summary="Get a memory", status_code=status.HTTP_200_OK)
def get_memory(memory_id: int) -> Dict[str, Any]:
    """Retrieve a specific memory by ID."""
    try:
        result = MEMORY_INSTANCE.get(memory_id)
        logger.info(f"Retrieved memory with ID: {memory_id}")
        return result
    except Exception as e:
        logger.exception(f"Error in get_memory for ID {memory_id}:")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve memory: {str(e)}",
        )


@app.post("/search", summary="Search memories", status_code=status.HTTP_200_OK)
def search_memories(search_req: SearchRequest) -> Dict[str, Any]:
    """Search for memories based on a query."""
    try:
        params = {
            k: v
            for k, v in search_req.model_dump().items()
            if v is not None and k != "query"
        }
        result = MEMORY_INSTANCE.search(query=search_req.query, **params)
        logger.info(f"Search completed for query: {search_req.query[:50]}...")
        return result
    except Exception as e:
        logger.exception("Error in search_memories:")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to search memories: {str(e)}",
        )


@app.put("/memories/{memory_id}", summary="Update a memory", status_code=status.HTTP_200_OK)
def update_memory(memory_id: int, updated_memory: Dict[str, Any]) -> Dict[str, Any]:
    """Update an existing memory with new content.

    Args:
        memory_id: ID of the memory to update
        updated_memory: New content to update the memory with

    Returns:
        dict: Updated memory data
    """
    try:
        result = MEMORY_INSTANCE.update(memory_id=memory_id, data=updated_memory)
        logger.info(f"Memory updated successfully: ID {memory_id}")
        return result
    except Exception as e:
        logger.exception(f"Error in update_memory for ID {memory_id}:")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update memory: {str(e)}",
        )


@app.get(
    "/memories/{memory_id}/history",
    summary="Get memory history",
    status_code=status.HTTP_200_OK,
)
def memory_history(memory_id: int) -> Dict[str, Any]:
    """Retrieve memory history."""
    try:
        result = MEMORY_INSTANCE.history(memory_id=memory_id)
        logger.info(f"Retrieved history for memory ID: {memory_id}")
        return result
    except Exception as e:
        logger.exception(f"Error in memory_history for ID {memory_id}:")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve memory history: {str(e)}",
        )


@app.delete(
    "/memories/{memory_id}",
    summary="Delete a memory",
    status_code=status.HTTP_200_OK,
)
def delete_memory(memory_id: int) -> Dict[str, str]:
    """Delete a specific memory by ID."""
    try:
        MEMORY_INSTANCE.delete(memory_id=memory_id)
        logger.info(f"Memory deleted successfully: ID {memory_id}")
        return {"message": "Memory deleted successfully"}
    except Exception as e:
        logger.exception(f"Error in delete_memory for ID {memory_id}:")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete memory: {str(e)}",
        )


@app.delete("/memories", summary="Delete all memories", status_code=status.HTTP_200_OK)
def delete_all_memories(
    user_id: Optional[str] = None,
    run_id: Optional[str] = None,
    agent_id: Optional[str] = None,
) -> Dict[str, str]:
    """Delete all memories for a given identifier."""
    validate_identifiers(user_id, run_id, agent_id)

    try:
        params = build_identifier_params(user_id, run_id, agent_id)
        MEMORY_INSTANCE.delete_all(**params)
        logger.info(f"All memories deleted for identifiers: {params}")
        return {"message": "All relevant memories deleted"}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error in delete_all_memories:")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete memories: {str(e)}",
        )


@app.post("/reset", summary="Reset all memories", status_code=status.HTTP_200_OK)
def reset_memory() -> Dict[str, str]:
    """Completely reset stored memories."""
    try:
        MEMORY_INSTANCE.reset()
        logger.info("All memories reset successfully")
        return {"message": "All memories reset"}
    except Exception as e:
        logger.exception("Error in reset_memory:")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to reset memories: {str(e)}",
        )


@app.get("/token_count", summary="Get token count", status_code=status.HTTP_200_OK)
def get_token_count_endpoint() -> Dict[str, Dict[str, int]]:
    """Get token count statistics."""
    return {"token_count": async_token_tracker.get_detailed_stats()}


@app.post(
    "/reset_token_count",
    summary="Reset token count",
    status_code=status.HTTP_200_OK,
)
def reset_token_count() -> Dict[str, str]:
    """Reset token count statistics."""
    async_token_tracker.reset()
    logger.info("Token count reset successfully")
    return {"message": "Token count reset"}


@app.get("/health", summary="Health check", status_code=status.HTTP_200_OK)
def health_check() -> Dict[str, str]:
    """Health check endpoint."""
    return {
        "status": "healthy",
        "version": CONFIG_VERSION,
        "database_provider": DATABASE_PROVIDER,
        "llm_provider": LLM_PROVIDER,
        "embedding_provider": EMBEDDING_PROVIDER,
    }
