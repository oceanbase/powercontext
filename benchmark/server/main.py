"""
PowerMem Benchmark Server

⚠️ IMPORTANT: This server is NOT the official PowerMem server API.
It is only used for benchmark testing scenarios and should not be used in production.

This server provides REST APIs for managing and searching memories.

Configuration:
    Use the project root .env (same as PowerMem). Configure it first, then:
        cd benchmark/server && uvicorn main:app --host 0.0.0.0 --port 8000
"""

import asyncio
import logging
import sys
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, status
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field, field_validator
from powermem import Memory, auto_config

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
# Configuration - from project root .env (user must configure before starting)
# ============================================================================

DEFAULT_CONFIG = auto_config()




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
    """Setup token counting for OpenAI API (always on for benchmark server)."""
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
    vs = DEFAULT_CONFIG.get("vector_store") or {}
    llm = DEFAULT_CONFIG.get("llm") or {}
    embedder = DEFAULT_CONFIG.get("embedder") or {}
    return {
        "status": "healthy",
        "version": DEFAULT_CONFIG.get("version", "v1.1"),
        "database_provider": vs.get("provider", ""),
        "llm_provider": llm.get("provider", ""),
        "embedding_provider": embedder.get("provider", ""),
    }
