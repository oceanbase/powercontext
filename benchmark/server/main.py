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
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field
from powermem import Memory

# Configure logging first
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# Load environment variables
# Try to load from .env file in the same directory as this script
# If not found, load_dotenv() will use the default behavior (current directory or parent directories)
env_path = os.path.join(os.path.dirname(__file__), ".env")
if os.path.exists(env_path):
    load_dotenv(dotenv_path=env_path)
    logging.info(f"Loaded environment variables from {env_path}")
else:
    # Fallback to default behavior
    load_dotenv()
    logging.info("Loaded environment variables from default locations")

# ============================================================================
# Configuration - Load from environment variables
# ============================================================================

# OpenAI Configuration
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise ValueError("OPENAI_API_KEY environment variable is required")

LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o")
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.2"))

# Embedder Configuration (can use different API key than LLM)
EMBEDDER_MODEL_BASE_URL = os.getenv("EMBEDDER_MODEL_BASE_URL", OPENAI_BASE_URL)
EMBEDDER_API_KEY = os.getenv("EMBEDDER_API_KEY", OPENAI_API_KEY)  # Fallback to OPENAI_API_KEY if not set
EMBEDDER_MODEL = os.getenv("EMBEDDER_MODEL", "text-embedding-3-small")
EMBEDDER_DIMS = int(os.getenv("EMBEDDER_DIMS", "1536"))

# Database Configuration
DB_TYPE = os.getenv("DB_TYPE", "oceanbase").lower()
VECTOR_WEIGHT = float(os.getenv("VECTOR_WEIGHT", "0.5"))
FTS_WEIGHT = float(os.getenv("FTS_WEIGHT", "0.5"))

# Token Counting Configuration
TOKEN_COUNTING = os.getenv("TOKEN_COUNTING", "true").lower() in ("true", "1", "yes")

# Application Configuration
HISTORY_DB_PATH = os.getenv("HISTORY_DB_PATH", "history.db")
CONFIG_VERSION = os.getenv("CONFIG_VERSION", "v1.1")


def load_config() -> Dict[str, Any]:
    """Load and build configuration dictionary from environment variables."""
    # Select vector store based on DB_TYPE
    if DB_TYPE == "oceanbase":
        vector_store = {
            "provider": "oceanbase",
            "config": {
                "host": os.getenv("OCEANBASE_HOST", "127.0.0.1"),
                "port": os.getenv("OCEANBASE_PORT", "2881"),
                "user": os.getenv("OCEANBASE_USER", "root"),
                "password": os.getenv("OCEANBASE_PASSWORD", ""),
                "db_name": os.getenv("OCEANBASE_DB_NAME", "ai_work"),
                "collection_name": os.getenv("OCEANBASE_COLLECTION_NAME", "powermem_collection"),
                "embedding_model_dims": int(os.getenv("OCEANBASE_EMBEDDING_DIMS", str(EMBEDDER_DIMS))),
                "index_type": os.getenv("OCEANBASE_INDEX_TYPE", "HNSW"),
                "vidx_metric_type": os.getenv("OCEANBASE_VIDX_METRIC_TYPE", "l2"),
                "vector_weight": VECTOR_WEIGHT,
                "fts_weight": FTS_WEIGHT,
                'include_sparse': os.getenv('SPARSE_VECTOR_ENABLE', 'false').lower() == 'true'
            },
        }
    elif DB_TYPE == "postgres":
        vector_store = {
            "provider": "pgvector",
            "config": {
                "host": os.getenv("POSTGRES_HOST", "127.0.0.1"),
                "port": os.getenv("POSTGRES_PORT", "5432"),
                "user": os.getenv("POSTGRES_USER", "postgres"),
                "password": os.getenv("POSTGRES_PASSWORD", ""),
                "dbname": os.getenv("POSTGRES_DBNAME", "ai_work"),
                "collection_name": os.getenv("POSTGRES_COLLECTION_NAME", "memories"),
                "embedding_model_dims": int(os.getenv("POSTGRES_EMBEDDING_DIMS", str(EMBEDDER_DIMS))),
                "diskann": os.getenv("POSTGRES_DISKANN", "true").lower() in ("true", "1", "yes"),
                "hnsw": os.getenv("POSTGRES_HNSW", "true").lower() in ("true", "1", "yes"),
            },
        }
    else:
        raise ValueError(f"Unsupported DB_TYPE: {DB_TYPE}. Must be 'oceanbase' or 'postgres'")

    # Build configuration
    return {
        "version": CONFIG_VERSION,
        "vector_store": vector_store,
        "llm": {
            "provider": "openai",
            "config": {
                "openai_base_url": OPENAI_BASE_URL,
                "api_key": OPENAI_API_KEY,
                "temperature": LLM_TEMPERATURE,
                "model": LLM_MODEL
            }
        },
        "embedder": {
            "provider": "openai",
            "config": {
                "openai_base_url": EMBEDDER_MODEL_BASE_URL,
                "api_key": EMBEDDER_API_KEY,
                "model": EMBEDDER_MODEL,
                "embedding_dims": EMBEDDER_DIMS
            }
        },
        "history_db_path": HISTORY_DB_PATH,
        "reranker": {
            "enabled": True,
            "provider": "qwen",
            "config": {
                "api_key": EMBEDDER_API_KEY,
                "model": "qwen3-rerank",
            }
        },
        "sparse_embedder": {
            "provider": "qwen",
            "config": {
                'api_key': EMBEDDER_API_KEY,
                'model': EMBEDDER_MODEL,
                'embedding_dims': int(os.getenv('SPARSE_EMBEDDER_DIMS', '1536')),
            }
        }
    }


# ============================================================================
# Token Tracking
# ============================================================================


class AsyncTokenTracker:
    """Track token usage for OpenAI API calls."""

    def __init__(self):
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0
        self.cached_tokens = 0
        self._lock = asyncio.Lock()

    async def track_token(self, usage):
        """Asynchronously update token count."""
        async with self._lock:
            self.prompt_tokens += usage.prompt_tokens
            self.completion_tokens += usage.completion_tokens
            self.total_tokens += usage.total_tokens
            if hasattr(usage, 'prompt_tokens_details') and usage.prompt_tokens_details:
                if hasattr(usage.prompt_tokens_details, 'cached_tokens'):
                    self.cached_tokens += usage.prompt_tokens_details.cached_tokens

    def get_token_count(self):
        """Get total token count."""
        return self.total_tokens

    def get_detailed_stats(self):
        """Get detailed token statistics."""
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "cached_tokens": self.cached_tokens
        }

    def reset(self):
        """Reset all token counts."""
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0
        self.cached_tokens = 0


async_token_tracker = AsyncTokenTracker()


def setup_token_counting():
    """Setup token counting for OpenAI API if enabled."""
    if not TOKEN_COUNTING:
        return

    print("Token counting enabled")
    from openai.resources.chat.completions.completions import Completions
    import openai.resources.chat.chat

    class CountCompletions(Completions):
        """Wrapper for OpenAI Completions to track token usage."""

        def _safe_async_track_token(self, token_count):
            """Thread-safe async token tracking."""
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.create_task(async_token_tracker.track_token(token_count))
                else:
                    loop.run_until_complete(async_token_tracker.track_token(token_count))
            except RuntimeError:
                asyncio.run(async_token_tracker.track_token(token_count))

        def create(self, *args, **kwargs):
            """Override create to track token usage."""
            resp = super().create(*args, **kwargs)
            if hasattr(resp, 'usage') and resp.usage:
                self._safe_async_track_token(resp.usage)
            return resp

    module = sys.modules['openai.resources.chat.chat']
    setattr(module, 'Completions', CountCompletions)


# ============================================================================
# Initialize Application
# ============================================================================

# Initialize configuration and memory instance
DEFAULT_CONFIG = load_config()
MEMORY_INSTANCE = Memory.from_config(DEFAULT_CONFIG)

# Setup token counting
setup_token_counting()

# ============================================================================
# FastAPI Application
# ============================================================================

app = FastAPI(
    title="PowerMem REST APIs",
    description="A REST API for managing and searching memories for your AI Agents and Apps.",
    version="1.0.0",
)


# ============================================================================
# Request/Response Models
# ============================================================================


class Message(BaseModel):
    """Message model for memory storage."""
    role: str = Field(..., description="Role of the message (user or assistant).")
    content: str = Field(..., description="Message content.")


class MemoryCreate(BaseModel):
    """Request model for creating memories."""
    messages: List[Message] = Field(..., description="List of messages to store.")
    user_id: Optional[str] = None
    agent_id: Optional[str] = None
    run_id: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class SearchRequest(BaseModel):
    """Request model for searching memories."""
    query: str = Field(..., description="Search query.")
    user_id: Optional[str] = None
    run_id: Optional[str] = None
    agent_id: Optional[str] = None
    filters: Optional[Dict[str, Any]] = None


# ============================================================================
# Helper Functions
# ============================================================================


def validate_identifiers(user_id: Optional[str] = None, 
                         run_id: Optional[str] = None, 
                         agent_id: Optional[str] = None):
    """Validate that at least one identifier is provided."""
    if not any([user_id, run_id, agent_id]):
        raise HTTPException(
            status_code=400, 
            detail="At least one identifier (user_id, agent_id, run_id) is required."
        )


def build_identifier_params(user_id: Optional[str] = None,
                            run_id: Optional[str] = None,
                            agent_id: Optional[str] = None) -> Dict[str, Any]:
    """Build parameters dictionary from identifiers."""
    return {
        k: v for k, v in {
            "user_id": user_id,
            "run_id": run_id,
            "agent_id": agent_id
        }.items() if v is not None
    }


# ============================================================================
# API Routes
# ============================================================================


@app.post("/configure", summary="Configure PowerMem")
def set_config(config: Dict[str, Any]):
    """Set memory configuration."""
    global MEMORY_INSTANCE
    try:
        MEMORY_INSTANCE = Memory.from_config(config)
        return {"message": "Configuration set successfully"}
    except Exception as e:
        logging.exception("Error in set_config:")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/memories", summary="Create memories")
def add_memory(memory_create: MemoryCreate):
    """Store new memories."""
    validate_identifiers(
        memory_create.user_id,
        memory_create.run_id,
        memory_create.agent_id
    )
    
    try:
        params = {
            k: v for k, v in memory_create.model_dump().items() 
            if v is not None and k != "messages"
        }
        response = MEMORY_INSTANCE.add(
            messages=[m.model_dump() for m in memory_create.messages],
            **params
        )
        return JSONResponse(content=response)
    except Exception as e:
        logging.exception("Error in add_memory:")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/memories", summary="Get memories")
def get_all_memories(
    user_id: Optional[str] = None,
    run_id: Optional[str] = None,
    agent_id: Optional[str] = None,
):
    """Retrieve stored memories."""
    validate_identifiers(user_id, run_id, agent_id)
    
    try:
        params = build_identifier_params(user_id, run_id, agent_id)
        return MEMORY_INSTANCE.get_all(**params)
    except Exception as e:
        logging.exception("Error in get_all_memories:")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/memories/{memory_id}", summary="Get a memory")
def get_memory(memory_id: int):
    """Retrieve a specific memory by ID."""
    try:
        return MEMORY_INSTANCE.get(memory_id)
    except Exception as e:
        logging.exception("Error in get_memory:")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/search", summary="Search memories")
def search_memories(search_req: SearchRequest):
    """Search for memories based on a query."""
    try:
        params = {
            k: v for k, v in search_req.model_dump().items() 
            if v is not None and k != "query"
        }
        return MEMORY_INSTANCE.search(query=search_req.query, **params)
    except Exception as e:
        logging.exception("Error in search_memories:")
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/memories/{memory_id}", summary="Update a memory")
def update_memory(memory_id: int, updated_memory: Dict[str, Any]):
    """Update an existing memory with new content.
    
    Args:
        memory_id: ID of the memory to update
        updated_memory: New content to update the memory with
    
    Returns:
        dict: Success message indicating the memory was updated
    """
    try:
        return MEMORY_INSTANCE.update(memory_id=memory_id, data=updated_memory)
    except Exception as e:
        logging.exception("Error in update_memory:")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/memories/{memory_id}/history", summary="Get memory history")
def memory_history(memory_id: int):
    """Retrieve memory history."""
    try:
        return MEMORY_INSTANCE.history(memory_id=memory_id)
    except Exception as e:
        logging.exception("Error in memory_history:")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/memories/{memory_id}", summary="Delete a memory")
def delete_memory(memory_id: int):
    """Delete a specific memory by ID."""
    try:
        MEMORY_INSTANCE.delete(memory_id=memory_id)
        return {"message": "Memory deleted successfully"}
    except Exception as e:
        logging.exception("Error in delete_memory:")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/memories", summary="Delete all memories")
def delete_all_memories(
    user_id: Optional[str] = None,
    run_id: Optional[str] = None,
    agent_id: Optional[str] = None,
):
    """Delete all memories for a given identifier."""
    validate_identifiers(user_id, run_id, agent_id)
    
    try:
        params = build_identifier_params(user_id, run_id, agent_id)
        MEMORY_INSTANCE.delete_all(**params)
        return {"message": "All relevant memories deleted"}
    except Exception as e:
        logging.exception("Error in delete_all_memories:")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/reset", summary="Reset all memories")
def reset_memory():
    """Completely reset stored memories."""
    try:
        MEMORY_INSTANCE.reset()
        return {"message": "All memories reset"}
    except Exception as e:
        logging.exception("Error in reset_memory:")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/token_count", summary="Get token count")
def get_token_count_endpoint():
    """Get token count statistics."""
    return {"token_count": async_token_tracker.get_detailed_stats()}


@app.post("/reset_token_count", summary="Reset token count")
def reset_token_count():
    """Reset token count statistics."""
    async_token_tracker.reset()
    return {"message": "Token count reset"}