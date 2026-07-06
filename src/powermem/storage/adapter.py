"""
Storage adapter for Memory class

This module provides an adapter that bridges the VectorStoreBase interface
with the interface expected by the Memory class.
"""

import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from powermem.storage.base import VectorStoreBase
from powermem.utils.utils import serialize_datetime, get_current_datetime

logger = logging.getLogger(__name__)

_FILTER_VALUE_MISSING = object()


class StorageAdapter:
    """Adapter that bridges VectorStoreBase interface with Memory class expectations."""

    _SYSTEM_FILTER_KEYS = {"user_id", "agent_id", "run_id"}
    # Promoted payload fields addressed by bare filter keys on nested stores
    # (SQLite / PGVector). On OceanBase-like stores bare keys map to the top-level
    # payload instead; see _metadata_filter_key_for_store().
    #
    # hash/data are intentionally excluded from this set: on nested stores unqualified
    # hash/data filters target metadata.{key}; on OceanBase they target the top-level
    # payload field. Use payload.hash / payload.data or metadata.hash / metadata.data
    # when you need explicit cross-store semantics.
    _PAYLOAD_FILTER_KEYS = {
        "actor_id",
        "category",
        "created_at",
        "role",
        "sparse_embedding",
        "type",
        "updated_at",
    }
    _INTERNAL_SEARCH_PAYLOAD_KEYS = {
        "_vector_similarity",
        "_fts_score",
        "_fts_quality_score",
        "_sparse_similarity",
        "_quality_score",
        "_fusion_score",
        "_fusion_info",
        "_rerank_score",
    }
    
    def __init__(self, vector_store: VectorStoreBase, embedding_service=None, sparse_embedder_service=None):
        """Initialize the adapter with a vector store and embedding service."""
        self.vector_store = vector_store
        self.embedding_service = embedding_service
        self.sparse_embedder_service = sparse_embedder_service
        # get collection name from vector store attribute collection_name
        self.collection_name = getattr(vector_store, 'collection_name', 'memories')

        # Sub stores support (optional, for multi-table routing)
        self.sub_stores: Dict[str, 'SubStoreConfig'] = {}
        self.migration_manager = None

        # Ensure collection exists (will be created with actual vector size when first vector is added)
        # self.vector_store.create_col(self.collection_name, vector_size=1536, distance="cosine")
    
    def _generate_sparse_embedding(self, content: str, memory_action: str) -> Optional[Any]:
        """
        Generate sparse embedding for given content.

        Args:
            content: The text content to generate embedding for
            memory_action: The action context ("add", "search", "update")

        Returns:
            Sparse embedding if successful, None otherwise
        """
        if not self.sparse_embedder_service or not content:
            return None

        try:
            return self.sparse_embedder_service.embed_sparse(content, memory_action=memory_action)
        except Exception as e:
            logger.warning(f"Failed to generate sparse embedding ({memory_action}): {e}")
            return None

    def _supports_text_search_without_vector(self, target_store: VectorStoreBase) -> bool:
        """Return whether a backend can search by text when embeddings are absent."""
        store_module = target_store.__class__.__module__
        if store_module.endswith("sqlite.sqlite_vector_store"):
            return True
        if ".oceanbase." in store_module:
            return bool(getattr(target_store, "hybrid_search", False))
        return False

    def _oceanbase_like_store(self, store: VectorStoreBase) -> bool:
        """Return whether filters should follow OceanBase flat-table semantics."""
        return ".oceanbase." in store.__class__.__module__

    def _oceanbase_text_field(self, store: VectorStoreBase) -> str:
        """Return the OceanBase column name used for memory text content."""
        return getattr(store, "text_field", "document")

    def _metadata_filter_key_for_store(
        self,
        key: str,
        target_store: Optional[VectorStoreBase] = None,
    ) -> str:
        """Translate logical metadata filters to backend-specific payload paths."""
        store = target_store or self.vector_store
        store_module = store.__class__.__module__
        payload_nested_store = (
            store_module.endswith("sqlite.sqlite_vector_store")
            or ".pgvector." in store_module
        )
        oceanbase_like_store = self._oceanbase_like_store(store)

        if key in self._SYSTEM_FILTER_KEYS:
            return key
        if key.startswith("payload."):
            bare_key = key[len("payload."):]
            if oceanbase_like_store and bare_key == "data":
                return self._oceanbase_text_field(store)
            return bare_key
        if key in self._PAYLOAD_FILTER_KEYS:
            return key
        if key.startswith("metadata."):
            return key if (payload_nested_store or oceanbase_like_store) else key[len("metadata."):]
        if payload_nested_store:
            return f"metadata.{key}"
        if oceanbase_like_store and key == "data":
            return self._oceanbase_text_field(store)
        return key

    def _build_db_filters(
        self,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
        filters: Optional[Dict[str, Any]] = None,
        target_store: Optional[VectorStoreBase] = None,
    ) -> Dict[str, Any]:
        """Build filters that can be executed by the vector store."""
        db_filters: Dict[str, Any] = {}
        if user_id:
            db_filters["user_id"] = user_id
        if agent_id:
            db_filters["agent_id"] = agent_id
        if run_id:
            db_filters["run_id"] = run_id
        if filters:
            for key, value in filters.items():
                db_key = self._metadata_filter_key_for_store(key, target_store)
                if db_key not in db_filters:
                    db_filters[db_key] = value
        return db_filters

    def _normalized_filter_value(
        self,
        memory: Dict[str, Any],
        db_key: str,
    ) -> Any:
        """Read a translated filter key from a normalized memory row."""
        if db_key.startswith("metadata."):
            metadata = memory.get("metadata")
            if not isinstance(metadata, dict):
                return _FILTER_VALUE_MISSING
            nested_key = db_key[len("metadata."):]
            if nested_key not in metadata:
                return _FILTER_VALUE_MISSING
            return metadata[nested_key]

        if db_key not in memory:
            return _FILTER_VALUE_MISSING
        return memory[db_key]

    def _filter_match_view_from_payload(
        self,
        payload: Dict[str, Any],
        *,
        memory_id: Any,
        created_at: Any,
        updated_at: Any,
    ) -> Dict[str, Any]:
        """Build an internal row shape for list post-filter matching only."""
        text_content = payload.get("data", "")
        return {
            "id": memory_id,
            "memory": text_content,
            "data": text_content,
            "document": text_content,
            "hash": payload.get("hash"),
            "user_id": payload.get("user_id"),
            "agent_id": payload.get("agent_id"),
            "run_id": payload.get("run_id"),
            "category": payload.get("category"),
            "actor_id": payload.get("actor_id"),
            "role": payload.get("role"),
            "type": payload.get("type"),
            "metadata": payload.get("metadata", {}),
            "created_at": created_at,
            "updated_at": updated_at,
        }

    def _memory_matches_filter(
        self,
        memory: Dict[str, Any],
        key: str,
        expected: Any,
        target_store: Optional[VectorStoreBase] = None,
    ) -> bool:
        """Match logical filters against normalized memory payloads."""
        if key.startswith("metadata."):
            metadata = memory.get("metadata")
            if not isinstance(metadata, dict):
                return False
            nested_key = key[len("metadata."):]
            if nested_key not in metadata:
                return False
            return metadata[nested_key] == expected

        db_key = self._metadata_filter_key_for_store(key, target_store)
        actual = self._normalized_filter_value(memory, db_key)
        if actual is _FILTER_VALUE_MISSING:
            metadata = memory.get("metadata")
            if (
                isinstance(metadata, dict)
                and key not in self._PAYLOAD_FILTER_KEYS
                and key not in self._SYSTEM_FILTER_KEYS
                and not key.startswith("payload.")
                and key in metadata
            ):
                actual = metadata[key]

        if actual is _FILTER_VALUE_MISSING:
            return False
        return actual == expected

    def add_memory(self, memory_data: Dict[str, Any]) -> int:
        """Add a memory to the store."""
        # ID will be generated using Snowflake algorithm before insertion
        
        # Create vector from content using embedding service
        content = memory_data.get("content", "")
        metadata = memory_data.get("metadata", {})

        # Route to target store (main or sub store)
        target_store = self._route_to_store(metadata)

        # Check if embedding is already provided (preferred way)
        vector = memory_data.get("embedding")

        if not vector:
            # No embedding provided (or noop returned []), generate using embedding service
            if self.embedding_service and not getattr(self.embedding_service, "is_noop", False):
                try:
                    vector = self.embedding_service.embed(content, memory_action="add")
                except Exception as e:
                    logger.warning(f"Failed to generate embedding, using mock vector: {e}")
                    vector = [0.1] * 1536  # Use 1536 dimensions for OceanBase compatibility
            else:
                # No embedding service available (or disabled), use mock vector
                vector = [0.1] * 1536

        # Generate sparse embedding if sparse embedder service is available
        sparse_embedding = memory_data.get("sparse_embedding")
        if sparse_embedding is None:
            sparse_embedding = self._generate_sparse_embedding(content, "add")

        # Create collection with actual vector size if not exists
        collection_name = getattr(target_store, 'collection_name', self.collection_name)
        if not hasattr(self, '_collection_created'):
            target_store.create_col(collection_name, vector_size=len(vector), distance="cosine")
            self._collection_created = True
        
        # Store the memory data as payload - unified format based on OceanBase
        payload = {
            "data": content,  # Unified field name for text content
            "user_id": memory_data.get("user_id", ""),
            "agent_id": memory_data.get("agent_id", ""),
            "run_id": memory_data.get("run_id", ""),
            "actor_id": memory_data.get("actor_id", ""),
            "hash": memory_data.get("hash", ""),
            "created_at": serialize_datetime(memory_data.get("created_at", "")),
            "updated_at": serialize_datetime(memory_data.get("updated_at", "")),
            "category": memory_data.get("category", ""),
            "fulltext_content": content,  # For full-text search
        }
        
        # Add sparse embedding to payload if available
        if sparse_embedding is not None:
            payload["sparse_embedding"] = sparse_embedding

        # Add only user-defined metadata (not system fields)
        user_metadata = memory_data.get("metadata", {})
        payload["metadata"] = serialize_datetime(user_metadata) if user_metadata else {}
        
        # Add any extra fields (excluding system fields and embedding)
        excluded_fields = ["id", "content", "data", "user_id", "agent_id", "run_id", "metadata", "filters", 
                          "created_at", "updated_at", "actor_id", "hash", "category", "embedding", "sparse_embedding"]
        for key, value in memory_data.items():
            if key not in excluded_fields:
                payload[key] = serialize_datetime(value)
        
        # Insert and get generated Snowflake ID
        generated_ids = target_store.insert([vector], [payload])
        if not generated_ids:
            raise ValueError("Failed to insert memory: no ID returned from vector store")
        memory_id = generated_ids[0]  # Get the first (and only) generated Snowflake ID
        return memory_id
    
    def search_memories(
        self,
        query_embedding: Optional[List[float]],
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
        filters: Optional[Dict[str, Any]] = None,
        limit: int = 30,
        query: Optional[str] = None,
        threshold: Optional[float] = None,
        retrieval_mode: str = "auto",
        fusion: str = "rrf",
        vector_weight: Optional[float] = None,
        fts_weight: Optional[float] = None,
        rrf_k: int = 60,
        candidate_limit: Optional[int] = None,
        include_explanation: bool = False,
    ) -> List[Dict[str, Any]]:
        """Search for memories."""
        mode = (retrieval_mode or "auto").lower()
        search_query = query if query else ""
        has_query = bool(search_query.strip())
        query_vector = query_embedding if query_embedding else None

        if mode not in {"auto", "fts", "vector", "hybrid"}:
            raise ValueError(f"Invalid retrieval mode: {retrieval_mode}")
        fusion = (fusion or "rrf").lower()
        if fusion not in {"rrf", "weighted"}:
            raise ValueError(f"Invalid fusion method: {fusion}")
        if vector_weight is not None and not 0.0 <= vector_weight <= 1.0:
            raise ValueError("vector_weight must be between 0.0 and 1.0")
        if fts_weight is not None and not 0.0 <= fts_weight <= 1.0:
            raise ValueError("fts_weight must be between 0.0 and 1.0")
        if rrf_k < 1:
            raise ValueError("rrf_k must be >= 1")
        if candidate_limit is not None and candidate_limit < 1:
            raise ValueError("candidate_limit must be >= 1")

        if mode == "fts":
            query_vector = None
        elif mode == "vector":
            search_query = ""
            has_query = False
            if query_vector is None:
                logger.warning("Vector retrieval requested without query embedding")
                return []
        elif mode == "hybrid" and query_vector is None:
            if has_query:
                logger.warning("Hybrid retrieval missing query embedding; falling back to FTS")
            else:
                logger.warning("Hybrid retrieval requested without query or query embedding")
                return []
        elif mode == "auto" and query_vector is None and not has_query:
            logger.warning("No query embedding or text query provided for search")
            return []
        
        # Generate sparse embedding if sparse embedder service is available and query is provided
        sparse_embedding = (
            self._generate_sparse_embedding(search_query, "search")
            if has_query
            else None
        )

        # Route with logical filters, then translate them for the selected store.
        routing_filters = filters.copy() if filters else {}
        if user_id is not None:
            routing_filters["user_id"] = user_id
        if agent_id is not None:
            routing_filters["agent_id"] = agent_id
        if run_id is not None:
            routing_filters["run_id"] = run_id

        target_store = self._route_to_store(routing_filters)
        effective_filters = self._build_db_filters(
            user_id,
            agent_id,
            run_id,
            filters,
            target_store=target_store,
        )
        if (
            query_vector is None
            and has_query
            and not self._supports_text_search_without_vector(target_store)
        ):
            logger.warning(
                "No query embedding provided and target store does not support "
                "text-only search fallback"
            )
            return []

        # Unified search method - try OceanBase format first, fallback to SQLite
        # Pass query text to enable hybrid search (vector + full-text search)
        search_limit = candidate_limit if candidate_limit is not None else limit
        search_vectors = query_vector
        try:
            # Try OceanBase format first - pass query text for hybrid search
            # Check if target_store.search supports sparse_embedding and threshold parameters
            import inspect
            search_sig = inspect.signature(target_store.search)
            search_params = search_sig.parameters

            # Build search kwargs based on supported parameters
            search_kwargs = {
                "query": search_query,
                "vectors": search_vectors,
                "limit": search_limit,
                "filters": effective_filters or None,
            }
            if 'sparse_embedding' in search_params:
                search_kwargs["sparse_embedding"] = sparse_embedding
            if 'threshold' in search_params:
                search_kwargs["threshold"] = threshold
            optional_search_kwargs = {
                "retrieval_mode": mode,
                "fusion": fusion,
                "vector_weight": vector_weight,
                "fts_weight": fts_weight,
                "rrf_k": rrf_k,
                "candidate_limit": search_limit,
                "include_explanation": include_explanation,
            }
            for key, value in optional_search_kwargs.items():
                if key in search_params:
                    search_kwargs[key] = value

            results = target_store.search(**search_kwargs)
        except TypeError:
            # Fallback to SQLite format (doesn't support query text parameter)
            # Pass filters to ensure filtering works correctly
            results = target_store.search(
                search_query,
                vectors=search_vectors,
                limit=search_limit,
                filters=effective_filters or None,
            )
        
        # Convert results to unified format
        memories = []
        for result in results:
            # Handle different result formats
            if hasattr(result, 'payload') and result.payload:
                # Result with payload attribute
                payload = result.payload
                memory_id = result.id
                # Extract score - use 0.0 as default instead of 1.0 to avoid false high scores
                # Score should always exist from vector search, but handle None case gracefully
                score = getattr(result, 'score', None)
                if score is None:
                    logger.warning(f"Result {memory_id} missing score, using 0.0")
                    score = 0.0
            elif hasattr(result, 'payload') and isinstance(result.payload, dict):
                # Result with dict payload
                payload = result.payload
                memory_id = result.id
                # Extract score - use 0.0 as default instead of 1.0
                score = getattr(result, 'score', None)
                if score is None:
                    logger.warning(f"Result {memory_id} missing score, using 0.0")
                    score = 0.0
            elif isinstance(result, dict):
                # Direct dict result
                payload = result
                memory_id = result.get("id")
                # Extract score - use 0.0 as default instead of 1.0
                score = result.get("score")
                if score is None:
                    logger.warning(f"Result {memory_id} missing score, using 0.0")
                    score = 0.0
            else:
                continue
            
            # Extract unified fields
            # Core and promoted keys that should not be in metadata
            promoted_payload_keys = [
                "user_id",
                "agent_id",
                "run_id",
                "actor_id",
                "role",
            ]
            core_and_promoted_keys = {
                "data",
                "hash",
                "created_at",
                "updated_at",
                "fulltext_content",
                "sparse_embedding",
                "id",
                "metadata",
                *promoted_payload_keys,
            }
            
            # Extract core fields
            content = payload.get("data", "")
            created_at = payload.get("created_at")
            updated_at = payload.get("updated_at")
            
            # Extract promoted fields
            promoted_fields = {}
            for key in promoted_payload_keys:
                if key in payload:
                    promoted_fields[key] = payload[key]
            
            # Extract user metadata from payload
            # If payload contains "metadata" field (nested user metadata), use it directly
            # Otherwise, extract additional metadata from other fields
            if "metadata" in payload:
                user_metadata = payload["metadata"].copy() if payload["metadata"] else {}
            else:
                # Extract additional metadata (all fields not in core_and_promoted_keys)
                user_metadata = {
                    k: v
                    for k, v in payload.items()
                    if (
                        k not in core_and_promoted_keys
                        and k not in self._INTERNAL_SEARCH_PAYLOAD_KEYS
                        and not k.startswith("_")
                    )
                }
            
            # Merge any user-defined fields from payload top-level into metadata
            # These fields (like "category") were extracted from metadata for filtering purposes
            # but should still be visible in the returned metadata
            for key, value in payload.items():
                if (
                    key not in core_and_promoted_keys
                    and key not in user_metadata
                    and key not in self._INTERNAL_SEARCH_PAYLOAD_KEYS
                    and not key.startswith("_")
                ):
                    if value is not None and (value or value == 0 or value == 0.0):
                        user_metadata[key] = value
            
            memory = {
                "id": memory_id,
                "memory": content,
                "created_at": created_at,
                "updated_at": updated_at,
                "score": score,
                **promoted_fields,  # Add promoted fields at top level
                "metadata": user_metadata if user_metadata else {},  # Add user metadata
            }
            if "_quality_score" in payload:
                memory["_quality_score"] = payload.get("_quality_score")
            if include_explanation:
                explanation = {
                    "ranking_score": score,
                    "fusion_method": fusion,
                    "retrieval_mode": mode,
                    "vector_weight": vector_weight
                    if vector_weight is not None
                    else 0.5,
                    "fts_weight": fts_weight if fts_weight is not None else 0.5,
                    "rrf_k": rrf_k,
                }
                fusion_info = payload.get("_fusion_info")
                if isinstance(fusion_info, dict):
                    explanation.update(fusion_info)
                if "_vector_similarity" in payload:
                    explanation["vector_similarity"] = payload.get("_vector_similarity")
                if "_fts_score" in payload:
                    explanation["fts_score"] = payload.get("_fts_score")
                memory["metadata"]["search_explanation"] = explanation

            if filters:
                filter_view = self._filter_match_view_from_payload(
                    payload,
                    memory_id=memory_id,
                    created_at=created_at,
                    updated_at=updated_at,
                )
                for key, expected in filters.items():
                    if key in ("user_id", "agent_id", "run_id"):
                        continue
                    if not self._memory_matches_filter(
                        filter_view,
                        key,
                        expected,
                        target_store=target_store,
                    ):
                        break
                else:
                    memories.append(memory)
                    continue
                continue

            memories.append(memory)
        
        return memories[:limit]
    
    def get_memory(
        self,
        memory_id: int,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Get a specific memory by ID."""
        result = self.vector_store.get(memory_id)
        
        if result and result.payload:
            content = result.payload.get("data") or result.payload.get("content") or ""
            memory = {
                "id": result.id,
                "content": content,
                "user_id": result.payload.get("user_id"),
                "agent_id": result.payload.get("agent_id"),
                "run_id": result.payload.get("run_id"),
                "metadata": result.payload.get("metadata", {}),
                "created_at": result.payload.get("created_at"),
                "updated_at": result.payload.get("updated_at"),
            }
            
            # Check access control
            if user_id and memory.get("user_id") != user_id:
                return None
            if agent_id and memory.get("agent_id") != agent_id:
                return None
            
            return memory
        
        # If not found in main store and sub stores exist, search sub stores
        if self.sub_stores:
            for sub_config in self.sub_stores.values():
                try:
                    result = sub_config.vector_store.get(memory_id)
                    if result and result.payload:
                        content = result.payload.get("data") or result.payload.get("content") or ""
                        memory = {
                            "id": result.id,
                             "content": content,
                            "user_id": result.payload.get("user_id"),
                            "agent_id": result.payload.get("agent_id"),
                            "run_id": result.payload.get("run_id"),
                            "metadata": result.payload.get("metadata", {}),
                            "created_at": result.payload.get("created_at"),
                            "updated_at": result.payload.get("updated_at"),
                        }

                        # Check access control
                        if user_id and memory.get("user_id") != user_id:
                            continue
                        if agent_id and memory.get("agent_id") != agent_id:
                            continue

                        return memory
                except Exception as e:
                    logger.debug(f"Error searching in sub store {sub_config.name}: {e}")
                    continue

        return None
    
    def update_memory(
        self,
        memory_id: int,
        update_data: Dict[str, Any],
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Update a memory."""
        # First check if memory exists and user has access (get_memory returns dict)
        existing_memory_dict = self.get_memory(memory_id, user_id, agent_id)
        if not existing_memory_dict:
            logger.warning(f"Memory {memory_id} not found or access denied")
            return None
        
        # Get raw OutputData object from vector store to access payload
        existing_result = self.vector_store.get(memory_id)
        target_store = self.vector_store

        # If not found in main store, search sub stores
        if (not existing_result or not existing_result.payload) and self.sub_stores:
            for sub_config in self.sub_stores.values():
                try:
                    sub_result = sub_config.vector_store.get(memory_id)
                    if sub_result and sub_result.payload:
                        # Verify access control matches
                        sub_payload = sub_result.payload
                        if user_id and sub_payload.get("user_id") != user_id:
                            continue
                        if agent_id and sub_payload.get("agent_id") != agent_id:
                            continue
                        existing_result = sub_result
                        target_store = sub_config.vector_store
                        break
                except Exception as e:
                    logger.debug(f"Error searching in sub store {sub_config.name}: {e}")
                    continue

        if not existing_result or not existing_result.payload:
            logger.warning(f"Memory {memory_id} not found in vector store")
            return None
        
        # Get existing payload
        existing_payload = existing_result.payload
        
        # Merge update_data into payload
        updated_payload = existing_payload.copy()
        
        # Handle content field - map to "data" in payload
        if "content" in update_data:
            updated_payload["data"] = update_data["content"]
            updated_payload["fulltext_content"] = update_data["content"]

            # Generate sparse embedding if sparse embedder service is available and content is updated
            sparse_embedding = self._generate_sparse_embedding(update_data["content"], "update")
            if sparse_embedding is not None:
                updated_payload["sparse_embedding"] = sparse_embedding

            # Remove content from update_data to avoid confusion
            update_data = update_data.copy()
            del update_data["content"]
        
        # Serialize datetime objects in update_data before merging
        serialized_update_data = serialize_datetime(update_data)
        
        # Special handling for metadata: merge instead of replace
        if "metadata" in serialized_update_data:
            existing_metadata = updated_payload.get("metadata", {})
            new_metadata = serialized_update_data.get("metadata", {})
            # Merge: existing metadata + new metadata (new takes precedence)
            merged_metadata = {**existing_metadata, **new_metadata}
            serialized_update_data = serialized_update_data.copy()
            serialized_update_data["metadata"] = merged_metadata

        # Update other fields
        updated_payload.update(serialized_update_data)
        
        # Ensure datetime fields are serialized as ISO format strings
        if "updated_at" in updated_payload:
            updated_at = updated_payload["updated_at"]
            if isinstance(updated_at, datetime):
                updated_payload["updated_at"] = updated_at.isoformat()
        if "created_at" in updated_payload:
            created_at = updated_payload["created_at"]
            if isinstance(created_at, datetime):
                updated_payload["created_at"] = created_at.isoformat()

        # Update updated_at if not provided
        if "updated_at" not in updated_payload:
            updated_payload["updated_at"] = get_current_datetime().isoformat()
        
        # Update in vector store with proper payload
        target_store.update(memory_id, vector=update_data.get("embedding"), payload=updated_payload)
        
        return updated_payload
    
    def delete_memory(
        self,
        memory_id: int,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> bool:
        """Delete a memory."""
        # Check if memory exists and user has access
        existing = self.get_memory(memory_id, user_id, agent_id)
        if not existing:
            return False

        # Try to delete from main store
        try:
            self.vector_store.delete(memory_id)
            return True
        except Exception as e:
            logger.debug(f"Memory {memory_id} not in main store: {e}")

        # If not in main store, try sub stores
        if self.sub_stores:
            for sub_config in self.sub_stores.values():
                try:
                    sub_config.vector_store.delete(memory_id)
                    logger.debug(f"Deleted memory {memory_id} from sub store {sub_config.name}")
                    return True
                except Exception as e:
                    logger.debug(f"Memory {memory_id} not in sub store {sub_config.name}: {e}")
                    continue

        # If we reach here, memory existed in get_memory but couldn't be deleted
        logger.warning(f"Failed to delete memory {memory_id}")
        return False
    
    def get_all_memories(
        self,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
        sort_by: Optional[str] = None,
        order: str = "desc",
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """Get all memories with optional filtering and sorting."""
        db_filters = self._build_db_filters(user_id, agent_id, run_id, filters)

        results = self.vector_store.list(
            filters=db_filters if db_filters else None,
            limit=limit,
            offset=offset,
            order_by=sort_by,
            order=order
        )
        
        # OceanBase returns [memories], SQLite/PGVector return memories directly
        if results and isinstance(results[0], list):
            raw_results = results[0]
        else:
            raw_results = results
        
        # Convert to expected format and apply filters
        memories = []
        for result in raw_results:
            # Handle different result formats
            if hasattr(result, 'payload') and result.payload:
                # Result with payload attribute (e.g., from OceanBase OutputData)
                payload = result.payload
                memory_id = result.id
            elif isinstance(result, dict):
                # Direct dict result (e.g., from SQLite)
                payload = result
                memory_id = result.get("id")
            else:
                continue
            
            # Convert datetime objects to ISO format strings
            created_at = payload.get("created_at")
            if created_at is not None:
                from datetime import datetime
                if isinstance(created_at, datetime):
                    created_at = created_at.isoformat()
            
            updated_at = payload.get("updated_at")
            if updated_at is not None:
                from datetime import datetime
                if isinstance(updated_at, datetime):
                    updated_at = updated_at.isoformat()
            
            memory = {
                "id": memory_id,
                "memory": payload.get("data", ""),  # Unified field name to match search_memories format
                "user_id": payload.get("user_id"),
                "agent_id": payload.get("agent_id"),
                "run_id": payload.get("run_id"),
                "category": payload.get("category"),
                "metadata": payload.get("metadata", {}),
                "created_at": created_at,
                "updated_at": updated_at,
            }
            filter_view = self._filter_match_view_from_payload(
                payload,
                memory_id=memory_id,
                created_at=created_at,
                updated_at=updated_at,
            )
            
            # Apply filters (as double-check if database didn't filter)
            # Note: If filters were applied at database level, these will all pass
            if user_id and memory.get("user_id") != user_id:
                continue
            if agent_id and memory.get("agent_id") != agent_id:
                continue
            if run_id and memory.get("run_id") != run_id:
                continue
            # Apply extra filters (e.g. metadata or payload fields backend may not have filtered)
            if filters:
                for key, expected in filters.items():
                    if key in ("user_id", "agent_id", "run_id"):
                        continue
                    if not self._memory_matches_filter(
                        filter_view,
                        key,
                        expected,
                        target_store=self.vector_store,
                    ):
                        break
                else:
                    pass  # all extra filters matched
                    memories.append(memory)
                    continue
                continue  # one extra filter did not match, skip this memory
            
            memories.append(memory)

        return memories

    def count_all_memories(
        self,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
        filters: Optional[Dict[str, Any]] = None,
    ) -> int:
        """Count all memories with optional filtering."""
        db_filters = self._build_db_filters(
            user_id,
            agent_id,
            run_id,
            filters,
            target_store=self.vector_store,
        )

        try:
            if hasattr(self.vector_store, "count"):
                try:
                    return int(self.vector_store.count(filters=db_filters or None))
                except TypeError:
                    # Backward compatibility for implementations using positional args.
                    return int(self.vector_store.count(db_filters or None))

            # Fallback for stores without explicit count(): list and count in memory.
            results = self.vector_store.list(filters=db_filters or None, limit=None, offset=0)
            if results and isinstance(results[0], list):
                return len(results[0])
            return len(results or [])
        except Exception as e:
            logger.error(f"Failed to count memories with filters {db_filters}: {e}", exc_info=True)
            return 0
    
    def clear_memories(
        self,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
    ) -> bool:
        """Clear all memories for a user or agent or run."""
        # Build filters for database query
        filters = {}
        if user_id:
            filters["user_id"] = user_id
        if agent_id:
            filters["agent_id"] = agent_id
        if run_id:
            filters["run_id"] = run_id
        
        # Use batch processing to avoid timeout
        batch_size = 1000
        deleted_count = 0
        
        while True:
            # Get a batch of memories with filtering
            batch = self.get_all_memories(user_id, agent_id, run_id, limit=batch_size, offset=deleted_count)
            
            # If no more records, we're done
            if not batch:
                break
            
            # Delete each memory in the batch
            for memory in batch:
                try:
                    self.vector_store.delete(memory["id"])
                except Exception as e:
                    logger.warning(f"Failed to delete memory {memory.get('id')}: {e}")
            
            deleted_count += len(batch)
            
            # If we got fewer records than batch_size, we've reached the end
            if len(batch) < batch_size:
                break
        
        logger.info(f"Deleted {deleted_count} memories with filters: {filters}")
        return True
    
    async def get_all_memories_async(
        self,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
        sort_by: Optional[str] = None,
        order: str = "desc",
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """Get all memories with optional filtering and sorting asynchronously."""
        import asyncio
        return await asyncio.to_thread(
            self.get_all_memories, user_id, agent_id, run_id, limit, offset, sort_by, order, filters
        )
    
    async def clear_memories_async(
        self,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
    ) -> bool:
        """Clear all memories for a user or agent or run asynchronously."""
        import asyncio
        return await asyncio.to_thread(self.clear_memories, user_id, agent_id, run_id)
    
    async def initialize_async(self):
        """Initialize storage asynchronously."""
        # No-op for now
        pass
    
    async def add_memory_async(self, memory_data: Dict[str, Any]) -> int:
        """Add a memory to the store asynchronously."""
        import asyncio
        return await asyncio.to_thread(self.add_memory, memory_data)
    
    async def search_memories_async(
        self,
        query_embedding: Optional[List[float]],
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
        filters: Optional[Dict[str, Any]] = None,
        limit: int = 30,
        query: Optional[str] = None,
        threshold: Optional[float] = None,
        retrieval_mode: str = "auto",
        fusion: str = "rrf",
        vector_weight: Optional[float] = None,
        fts_weight: Optional[float] = None,
        rrf_k: int = 60,
        candidate_limit: Optional[int] = None,
        include_explanation: bool = False,
    ) -> List[Dict[str, Any]]:
        """Search for memories asynchronously."""
        import asyncio
        return await asyncio.to_thread(
            self.search_memories,
            query_embedding=query_embedding,
            user_id=user_id,
            agent_id=agent_id,
            run_id=run_id,
            filters=filters,
            limit=limit,
            query=query,
            threshold=threshold,
            retrieval_mode=retrieval_mode,
            fusion=fusion,
            vector_weight=vector_weight,
            fts_weight=fts_weight,
            rrf_k=rrf_k,
            candidate_limit=candidate_limit,
            include_explanation=include_explanation,
        )
    
    async def get_memory_async(
        self,
        memory_id: int,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Get a specific memory by ID asynchronously."""
        import asyncio
        return await asyncio.to_thread(self.get_memory, memory_id, user_id, agent_id)
    
    async def delete_memory_async(
        self,
        memory_id: int,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> bool:
        """Delete a memory asynchronously."""
        import asyncio
        return await asyncio.to_thread(self.delete_memory, memory_id, user_id, agent_id)
    
    async def update_memory_async(
        self,
        memory_id: int,
        update_data: Dict[str, Any],
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Update a memory asynchronously."""
        import asyncio
        return await asyncio.to_thread(self.update_memory, memory_id, update_data, user_id, agent_id)

    # ==================== Routing Support Methods ====================

    def _route_to_store(self, filters_or_metadata: Optional[Dict] = None) -> VectorStoreBase:
        """
        Route to correct storage instance (main or sub store).

        Args:
            filters_or_metadata: Query conditions or memory metadata

        Returns:
            Target VectorStoreBase instance
        """
        # If no sub stores configured, always use main store
        if not self.sub_stores:
            return self.vector_store

        # Try to find matching sub store
        if filters_or_metadata:
            for sub_config in self.sub_stores.values():
                # Check if sub store is ready (only for query operations)
                if self.migration_manager and not self.migration_manager.is_ready(sub_config.name):
                    continue

                # Check if filters_or_metadata matches routing rules
                routing_filter = sub_config.routing_filter
                if all(
                    key in filters_or_metadata and filters_or_metadata[key] == value
                    for key, value in routing_filter.items()
                ):
                    logger.debug(f"Routing to sub store: {sub_config.name}")
                    return sub_config.vector_store

        # Default to main store
        logger.debug("Routing to main store")
        return self.vector_store

    def get_target_store_name(self, filters_or_metadata: Optional[Dict] = None) -> str:
        """
        Get target store name for given filters/metadata.

        Args:
            filters_or_metadata: Query conditions or memory metadata

        Returns:
            Target storage name
        """
        target_store = self._route_to_store(filters_or_metadata)
        return getattr(target_store, 'collection_name', self.collection_name)

    def is_sub_store_ready(self, store_name: str) -> bool:
        """
        Check if sub store is ready (migration completed).

        Args:
            store_name: Sub store name

        Returns:
            True if ready, False otherwise (or if no migration manager)
        """
        if self.migration_manager:
            return self.migration_manager.is_ready(store_name)
        return False


# ==================== Sub Store Configuration ====================

class SubStoreConfig:
    """Configuration for a sub store."""
    def __init__(self, name: str, routing_filter: Dict, vector_store: VectorStoreBase, embedding_service=None):
        self.name = name
        self.routing_filter = routing_filter
        self.vector_store = vector_store
        self.embedding_service = embedding_service


class SubStorageAdapter(StorageAdapter):
    """
    Extended storage adapter with sub-store management capabilities.

    This adapter extends the basic StorageAdapter to support multiple sub-stores
    with intelligent routing based on metadata filters. All CRUD operations are
    inherited from the parent class and automatically support routing.

    This class only contains sub-store management methods.
    """

    def __init__(self, vector_store: VectorStoreBase, embedding_service=None, sparse_embedder_service=None):
        """
        Initialize the sub-storage adapter.

        Args:
            vector_store: The main vector store instance
            embedding_service: Optional embedding service for generating vectors
            sparse_embedder_service: Optional sparse embedder service for generating sparse embeddings
        """
        # Initialize parent class
        super().__init__(vector_store, embedding_service, sparse_embedder_service)

        # Initialize migration status management (database-backed)
        from powermem.storage.migration_manager import SubStoreMigrationManager
        self.migration_manager = SubStoreMigrationManager(vector_store, self.collection_name)

    # ==================== Sub Store Management Methods ====================

    def register_sub_store(
        self,
        store_name: str,
        routing_filter: Dict,
        vector_store: VectorStoreBase,
        embedding_service=None,
    ):
        """
        Register a sub store for routing.

        Args:
            store_name: Name of the sub store
            routing_filter: Dictionary of metadata conditions for routing
            vector_store: Vector store instance for the sub store
            embedding_service: Optional embedding service for this sub store (for migration)
        """
        sub_config = SubStoreConfig(
            name=store_name,
            routing_filter=routing_filter,
            vector_store=vector_store,
            embedding_service=embedding_service
        )
        self.sub_stores[store_name] = sub_config

        # Register in migration manager
        if self.migration_manager:
            self.migration_manager.register_sub_store(
                sub_store_name=store_name,
                routing_filter=routing_filter
            )

        logger.info(f"Registered sub store: {store_name} with filter: {routing_filter}")

    def migrate_to_sub_store(
        self,
        store_name: str,
        delete_source: bool = False,
        batch_size: int = 100
    ) -> int:
        """
        Migrate data from main store to sub store based on routing filter.

        Args:
            store_name: Name of the sub store to migrate to
            delete_source: Whether to delete source data after migration
            batch_size: Number of records to process in each batch

        Returns:
            Number of records migrated

        Raises:
            ValueError: If sub store not found or not registered
        """
        if store_name not in self.sub_stores:
            raise ValueError(f"Sub store '{store_name}' not found. Please register it first.")

        sub_config = self.sub_stores[store_name]
        routing_filter = sub_config.routing_filter
        target_store = sub_config.vector_store
        sub_embedding_service = sub_config.embedding_service

        # Validate that embedding service is provided
        if not sub_embedding_service:
            raise ValueError(f"Sub store '{store_name}' does not have an embedding service configured. "
                           "Cannot migrate without re-embedding the data.")

        # Mark migration as started
        if self.migration_manager:
            self.migration_manager.mark_migrating(store_name, 0)

        try:
            # Query all matching records from main store
            migrated_count = 0

            # Get all memories that match the routing filter
            from powermem.storage.oceanbase.oceanbase import OceanBaseVectorStore

            if isinstance(self.vector_store, OceanBaseVectorStore):
                # Use OceanBase specific query
                # Build SQL query to find matching records (only need ID and content fields)
                filter_conditions = " AND ".join([
                    f"JSON_EXTRACT(metadata, '$.{key}') = '{value}'"
                    for key, value in routing_filter.items()
                ])

                # Query only IDs first for efficiency
                id_query_sql = f"""
                SELECT id
                FROM {self.collection_name}
                WHERE {filter_conditions}
                LIMIT {batch_size}
                """

                while True:
                    id_results = self.vector_store.execute_sql(id_query_sql)
                    if not id_results:
                        break

                    for id_record in id_results:
                        record_id = id_record['id']

                        # Use get() method to retrieve the full record
                        result = self.vector_store.get(record_id)
                        if not result or not result.payload:
                            logger.warning(f"Record {record_id} not found, skipping")
                            continue

                        # Use payload from result
                        payload = result.payload.copy()
                        payload['id'] = record_id

                        # Extract content for re-embedding
                        content = payload.get('data', '')
                        if not content:
                            logger.warning(f"Record {record_id} has no content, skipping")
                            continue

                        # Re-generate vector using sub store's embedding service
                        try:
                            vector = sub_embedding_service.embed(content, memory_action="add")
                            logger.debug(f"Re-embedded record {record_id} with dimension {len(vector)}")
                        except Exception as embed_error:
                            logger.error(f"Failed to re-embed record {record_id}: {embed_error}")
                            continue

                        try:
                            target_store.insert([vector], [payload])
                            migrated_count += 1

                            # Delete from source if requested
                            if delete_source:
                                self.vector_store.delete(record_id)

                            # Update progress
                            if self.migration_manager and migrated_count % 10 == 0:
                                self.migration_manager.update_progress(
                                    store_name,
                                    migrated_count,
                                    migrated_count  # Total is unknown in this approach
                                )
                        except Exception as e:
                            logger.error(f"Error migrating record {record_id}: {e}")
                            continue

                    # If we got fewer results than batch_size, we're done
                    if len(id_results) < batch_size:
                        break
            else:
                logger.warning(f"Migration not fully supported for {type(self.vector_store).__name__}")

            # Mark migration as completed
            if self.migration_manager:
                self.migration_manager.mark_completed(store_name, migrated_count)

            logger.info(f"Migration completed: {migrated_count} records migrated to {store_name}")
            return migrated_count

        except Exception as e:
            # Mark migration as failed
            if self.migration_manager:
                self.migration_manager.mark_failed(store_name, str(e))
            logger.error(f"Migration failed: {e}")
            raise

    def get_migration_status(self, store_name: str) -> Optional[Dict[str, Any]]:
        """
        Get migration status for a sub store.

        Args:
            store_name: Sub store name

        Returns:
            Migration status dict or None if not found
        """
        if self.migration_manager:
            return self.migration_manager.get_status(store_name)
        return None

    def list_sub_stores(self) -> List[str]:
        """
        List all registered sub stores.

        Returns:
            List of sub store names
        """
        return list(self.sub_stores.keys())
