"""Regression coverage for the built-in 384-dim default embedder.

The main regression workflow pins Qwen text-embedding-v4 (1536 dims). This file
keeps explicit coverage for the zero-config local embedder on its own OceanBase
collection so table dimensions cannot conflict with the 1536-dim path.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List

from dotenv import load_dotenv
from powermem import create_memory


env_path = os.path.join(os.path.dirname(__file__), "..", "..", ".env")
if os.path.exists(env_path):
    print("Found .env file")
    load_dotenv(env_path, override=True)


USER_ID = "scenario9_default_embedder_user"
COLLECTION_NAME = "memories_384"


def _results(result: Any) -> List[Dict[str, Any]]:
    if isinstance(result, dict) and isinstance(result.get("results"), list):
        return result["results"]
    return []


def _memory_text(entry: Dict[str, Any]) -> str:
    return (
        entry.get("memory")
        or entry.get("content")
        or entry.get("text")
        or entry.get("data")
        or ""
    )


def _default_embedding_config() -> Dict[str, Any]:
    return {
        "llm": {
            "provider": os.getenv("LLM_PROVIDER", "siliconflow"),
            "config": {
                "model": os.getenv("LLM_MODEL", "THUDM/GLM-4-9B-0414"),
                "api_key": os.getenv("LLM_API_KEY"),
                "openai_base_url": os.getenv("SILICONFLOW_LLM_BASE_URL"),
            },
        },
        "vector_store": {
            "provider": "oceanbase",
            "config": {
                "collection_name": COLLECTION_NAME,
                "connection_args": {
                    "host": os.getenv("OCEANBASE_HOST", "127.0.0.1"),
                    "port": int(os.getenv("OCEANBASE_PORT", "10001")),
                    "user": os.getenv("OCEANBASE_USER", "root"),
                    "password": os.getenv("OCEANBASE_PASSWORD", ""),
                    "db_name": os.getenv("OCEANBASE_DATABASE", "powermem"),
                },
                "embedding_model_dims": 384,
                "vidx_metric_type": "cosine",
                "index_type": "HNSW",
                "primary_field": "id",
                "vector_field": "embedding",
                "text_field": "document",
                "metadata_field": "metadata",
                "vidx_name": "memories_384_vidx",
            },
        },
        "embedder": {
            "provider": "default",
            "config": {
                "model": "all-MiniLM-L6-v2",
                "embedding_dims": 384,
            },
        },
    }


def test_default_embedder_384_add_and_search() -> None:
    memory = create_memory(config=_default_embedding_config())
    try:
        memory.delete_all(user_id=USER_ID)
    except Exception:
        pass

    try:
        result = memory.add(
            "User likes local 384 dimension embeddings",
            user_id=USER_ID,
            metadata={"scenario": "default_embedding_384"},
            infer=False,
        )
        assert _results(result), "simple add should return one stored memory"

        search_result = memory.search(
            "local embeddings",
            user_id=USER_ID,
            limit=5,
        )
        matches = _results(search_result)
        assert matches, "search should return the memory stored with the 384-dim embedder"
        assert any("384 dimension" in _memory_text(item) for item in matches)
    finally:
        try:
            memory.delete_all(user_id=USER_ID)
        except Exception:
            pass
