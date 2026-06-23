"""Memory service for PowerMem API."""

import base64
import binascii
import json
import logging
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime, timezone
from pydantic import ValidationError
from powermem import Memory, auto_config
from ..models.errors import ErrorCode, APIError
from ..models.request import ObservationIngestRequest
from ..utils.converters import memory_dict_to_response
from ..utils.metrics import get_metrics_collector

logger = logging.getLogger("server")


OBSERVATION_SCHEMA = "powermem.coding_agent_observation.v1"
OBSERVATION_SOURCE = "coding_agent"
OBSERVATION_MEMORY_TYPE = "coding_agent_observation"
OBSERVATION_SCOPE = "coding_agent"
OBSERVATION_CONTROL_FIELDS = {
    "save_raw",
    "infer",
    "dedupe",
    "filters",
    "user_id",
    "agent_id",
    "run_id",
    "scope",
    "memory_type",
}
OBSERVATION_FLAT_FIELDS = (
    "source",
    "observation_id",
    "observation_kind",
    "observation_level",
    "observation_status",
    "repo",
    "branch",
    "commit_sha",
    "tool_name",
    "task_id",
    "thread_id",
    "session_id",
)
SESSION_TIMELINE_PRECISION = "memory_snapshot"
SESSION_TIMELINE_CAPABILITIES = {
    "event_log": False,
    "memory_snapshot": True,
    "before_after_diff": False,
    "source_on_demand": True,
}
SESSION_TIMELINE_FETCH_CAP = 5000
SESSION_TIMELINE_SOURCE_KEYS = {
    "content",
    "full_source",
    "raw_content",
    "raw_source",
    "source_body",
    "source_content",
    "source_text",
    "transcript",
    "transcript_content",
}
SESSION_TIMELINE_JSON_ENCODER = json.JSONEncoder(default=str, sort_keys=True)
SESSION_TIMELINE_CURSOR_ENCODER = json.JSONEncoder(
    separators=(",", ":"),
    sort_keys=True,
)


class MemoryService:
    """Service for memory management operations"""
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Initialize memory service.
        
        Args:
            config: PowerMem configuration (uses auto_config if None)
        """
        if config is None:
            config = auto_config()
        
        self.memory = Memory(config=config)
        logger.info("MemoryService initialized")

    def _observation_payload(
        self,
        request: ObservationIngestRequest,
    ) -> Dict[str, Any]:
        """Return only the structured observation payload, excluding ingest controls."""
        payload = request.model_dump(mode="json", exclude_none=True)
        return {
            key: value
            for key, value in payload.items()
            if key not in OBSERVATION_CONTROL_FIELDS
        }

    def _observation_flat_metadata(
        self,
        observation: Dict[str, Any],
        record_kind: str,
        scope: str,
        memory_type: str,
    ) -> Dict[str, Any]:
        """Build flat filterable metadata for an observation record."""
        flat = {
            "schema": OBSERVATION_SCHEMA,
            "source": observation.get("source") or OBSERVATION_SOURCE,
            "record_kind": record_kind,
            "memory_type": memory_type,
            "scope": scope,
        }
        for key in OBSERVATION_FLAT_FIELDS:
            value = observation.get(key)
            if value is not None:
                flat[key] = value
        return flat

    def _flat_user_metadata(
        self,
        metadata: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Keep user metadata filterable at the top level."""
        if not metadata:
            return {}
        return {
            key: value
            for key, value in metadata.items()
            if isinstance(value, (str, int, float, bool))
        }

    def _build_observation_metadata(
        self,
        request: ObservationIngestRequest,
        record_kind: str,
    ) -> Dict[str, Any]:
        """Build storage metadata with flat fields and the full structured payload."""
        observation = self._observation_payload(request)
        metadata = self._flat_user_metadata(request.metadata)
        flat = self._observation_flat_metadata(
            observation=observation,
            record_kind=record_kind,
            scope=request.scope or OBSERVATION_SCOPE,
            memory_type=request.memory_type or OBSERVATION_MEMORY_TYPE,
        )
        metadata.update(flat)
        metadata["observation"] = observation
        return metadata

    def _build_observation_filters(
        self,
        request: ObservationIngestRequest,
        record_kind: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Build metadata filters for observation lookup/search routing."""
        observation = self._observation_payload(request)
        filters = dict(request.filters or {})
        flat = self._observation_flat_metadata(
            observation=observation,
            record_kind=record_kind or "observation_raw",
            scope=request.scope or OBSERVATION_SCOPE,
            memory_type=request.memory_type or OBSERVATION_MEMORY_TYPE,
        )
        filters.update(flat)
        return filters

    def _find_existing_observation(
        self,
        request: ObservationIngestRequest,
        record_kind: str = "observation_raw",
    ) -> Optional[Dict[str, Any]]:
        """Find one existing observation record when dedupe is requested."""
        matches = self._find_existing_observations(
            request,
            record_kind=record_kind,
            limit=1,
        )
        return matches[0] if matches else None

    def _find_existing_observations(
        self,
        request: ObservationIngestRequest,
        record_kind: str,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Find existing observation records of one stored kind."""
        if not request.observation_id:
            return []

        filters = {
            "schema": OBSERVATION_SCHEMA,
            "source": request.source or OBSERVATION_SOURCE,
            "record_kind": record_kind,
            "observation_id": request.observation_id,
        }
        result = self.memory.get_all(
            user_id=request.user_id,
            agent_id=request.agent_id,
            run_id=request.run_id,
            filters=filters,
            limit=limit,
            offset=0,
        )
        matches = result.get("results", []) if isinstance(result, dict) else []
        return matches if isinstance(matches, list) else []

    def _coerce_observation_request(
        self,
        observation: Any,
    ) -> ObservationIngestRequest:
        """Validate one batch item as an observation ingest request."""
        if isinstance(observation, ObservationIngestRequest):
            return observation
        return ObservationIngestRequest.model_validate(observation)

    def _observation_id_from_item(self, observation: Any) -> Optional[str]:
        """Best-effort observation_id extraction for batch item errors."""
        if isinstance(observation, ObservationIngestRequest):
            return observation.observation_id
        if isinstance(observation, dict):
            value = observation.get("observation_id")
            return str(value) if value is not None else None
        return None

    def ingest_observation(
        self,
        request: ObservationIngestRequest,
    ) -> Dict[str, Any]:
        """
        Ingest a structured coding-agent observation.

        Raw observations are stored deterministically without LLM inference.
        When infer=True, semantic extraction is attempted after raw persistence
        and may return zero memories in noop/no-LLM configurations.
        """
        try:
            existing_raw = None
            existing_semantic_memories: List[Dict[str, Any]] = []
            if request.dedupe:
                if request.save_raw:
                    existing_raw = self._find_existing_observation(
                        request,
                        record_kind="observation_raw",
                    )
                if request.infer:
                    existing_semantic_memories = self._find_existing_observations(
                        request,
                        record_kind="observation_semantic",
                    )

            filters = self._build_observation_filters(request)
            raw_memories: List[Dict[str, Any]] = []
            semantic_memories: List[Dict[str, Any]] = []

            if existing_raw:
                raw_memories = [existing_raw]
            elif request.save_raw:
                raw_metadata = self._build_observation_metadata(
                    request,
                    record_kind="observation_raw",
                )
                raw_memories = self.create_memory(
                    content=request.content,
                    user_id=request.user_id,
                    agent_id=request.agent_id,
                    run_id=request.run_id,
                    metadata=raw_metadata,
                    filters=filters,
                    scope=request.scope or OBSERVATION_SCOPE,
                    memory_type=request.memory_type or OBSERVATION_MEMORY_TYPE,
                    infer=False,
                )
                if not raw_memories:
                    raise APIError(
                        code=ErrorCode.MEMORY_CREATE_FAILED,
                        message="Failed to store raw observation",
                        status_code=500,
                    )

            if request.infer and existing_semantic_memories:
                semantic_memories = existing_semantic_memories
            elif request.infer:
                semantic_metadata = self._build_observation_metadata(
                    request,
                    record_kind="observation_semantic",
                )
                semantic_memories = self.create_memory(
                    content=request.content,
                    user_id=request.user_id,
                    agent_id=request.agent_id,
                    run_id=request.run_id,
                    metadata=semantic_metadata,
                    filters=filters,
                    scope=request.scope or OBSERVATION_SCOPE,
                    memory_type=request.memory_type or OBSERVATION_MEMORY_TYPE,
                    infer=True,
                )

            return {
                "observation_id": request.observation_id,
                "raw_memory": raw_memories[0] if raw_memories else None,
                "semantic_memories": semantic_memories,
                "memories": raw_memories + semantic_memories,
                "saved_raw": bool(raw_memories),
                "inferred": request.infer,
                "deduped": existing_raw is not None or bool(existing_semantic_memories),
            }
        except APIError:
            raise
        except Exception as e:
            logger.error(f"Failed to ingest observation: {e}", exc_info=True)
            raise APIError(
                code=ErrorCode.MEMORY_CREATE_FAILED,
                message=f"Failed to ingest observation: {str(e)}",
                status_code=500,
            )

    def batch_ingest_observations(
        self,
        observations: List[Any],
    ) -> Dict[str, Any]:
        """Ingest multiple observations with per-item partial failure results."""
        items = []
        success_count = 0
        failed_count = 0

        for idx, observation_item in enumerate(observations):
            observation_id = self._observation_id_from_item(observation_item)
            try:
                observation = self._coerce_observation_request(observation_item)
                result = self.ingest_observation(observation)
                items.append({
                    "index": idx,
                    "success": True,
                    "observation_id": observation.observation_id,
                    "result": result,
                    "error": None,
                })
                success_count += 1
            except ValidationError as e:
                items.append({
                    "index": idx,
                    "success": False,
                    "observation_id": observation_id,
                    "result": None,
                    "error": f"Request validation failed: {e.errors()}",
                })
                failed_count += 1
            except APIError as e:
                items.append({
                    "index": idx,
                    "success": False,
                    "observation_id": observation_id,
                    "result": None,
                    "error": e.message,
                })
                failed_count += 1
            except Exception as e:
                items.append({
                    "index": idx,
                    "success": False,
                    "observation_id": observation_id,
                    "result": None,
                    "error": str(e),
                })
                failed_count += 1

        return {
            "items": items,
            "total": len(observations),
            "success_count": success_count,
            "failed_count": failed_count,
        }
    
    def create_memory(
        self,
        content: str,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        filters: Optional[Dict[str, Any]] = None,
        scope: Optional[str] = None,
        memory_type: Optional[str] = None,
        infer: bool = True,
    ) -> List[Dict[str, Any]]:
        """
        Create a new memory.
        
        Args:
            content: Memory content
            user_id: User ID
            agent_id: Agent ID
            run_id: Run ID
            metadata: Metadata
            filters: Filters
            scope: Scope
            memory_type: Memory type
            infer: Enable intelligent processing (may create multiple memories)
            
        Returns:
            List of created memory data (may contain multiple memories if infer=True)
            
        Raises:
            APIError: If creation fails
        """
        try:
            result = self.memory.add(
                messages=content,
                user_id=user_id,
                agent_id=agent_id,
                run_id=run_id,
                metadata=metadata,
                filters=filters,
                scope=scope,
                memory_type=memory_type,
                infer=infer,
            )
            
            # Extract all created memories from result
            # Result format: {"results": [{"id": memory_id, ...}], ...}
            all_results = result.get("results", [])
            
            # Empty results is acceptable (e.g., when intelligent processing detects duplicates)
            # Return empty list instead of raising error
            if not all_results:
                logger.info("No memories were created (likely duplicates detected or no facts extracted)")
                return []
            
            logger.info(f"Created {len(all_results)} memory/memories")
            
            # Normalize all results to include memory_id and other fields at top level
            # Fetch full memory info from database to get timestamps (consistent with batch_create_memories)
            normalized_memories = []
            
            for result_item in all_results:
                memory_id = result_item.get("id")
                if memory_id is None:
                    continue
                
                # Fetch full memory info from database to get complete data including timestamps
                try:
                    full_memory = self.get_memory(memory_id, user_id, agent_id)
                    if full_memory:
                        normalized_memories.append(full_memory)
                        continue
                except Exception as e:
                    logger.warning(f"Failed to fetch full memory info for {memory_id}: {e}, using result_item data")
                
                # Fallback to result_item if get_memory fails
                # Ensure metadata is always a dict, never None
                result_metadata = result_item.get("metadata")
                if result_metadata is None:
                    result_metadata = metadata or {}
                
                # Extract fields with fallback: use result_item value if present and not None, otherwise use input parameter
                def get_field(result_key: str, param_value):
                    """Get field from result_item if available and not None, otherwise use param_value"""
                    if result_key in result_item:
                        result_value = result_item.get(result_key)
                        # Use result value if it's not None, otherwise fall back to param
                        return result_value if result_value is not None else param_value
                    return param_value
                
                normalized_memory = {
                    "id": memory_id,
                    "memory_id": memory_id,
                    "content": get_field("memory", content),
                    "user_id": get_field("user_id", user_id),
                    "agent_id": get_field("agent_id", agent_id),
                    "run_id": get_field("run_id", run_id),
                    "metadata": result_metadata if isinstance(result_metadata, dict) else {},
                }
                
                # Add timestamps only if they exist and are not None
                if "created_at" in result_item and result_item["created_at"] is not None:
                    normalized_memory["created_at"] = result_item["created_at"]
                if "updated_at" in result_item and result_item["updated_at"] is not None:
                    normalized_memory["updated_at"] = result_item["updated_at"]
                
                normalized_memories.append(normalized_memory)
            
            # Record successful memory operation
            metrics_collector = get_metrics_collector()
            metrics_collector.record_memory_operation("create", "success")
            
            # Return array of all created memories
            return normalized_memories
            
        except Exception as e:
            logger.error(f"Failed to create memory: {e}", exc_info=True)
            
            # Record failed memory operation
            metrics_collector = get_metrics_collector()
            metrics_collector.record_memory_operation("create", "failed")
            
            raise APIError(
                code=ErrorCode.MEMORY_CREATE_FAILED,
                message=f"Failed to create memory: {str(e)}",
                status_code=500,
            )
    
    def get_memory(
        self,
        memory_id: int,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Get a memory by ID.
        
        Args:
            memory_id: Memory ID
            user_id: User ID for access control
            agent_id: Agent ID for access control
            
        Returns:
            Memory data
            
        Raises:
            APIError: If memory not found
        """
        try:
            memory = self.memory.get(
                memory_id=memory_id,
                user_id=user_id,
                agent_id=agent_id,
            )
            
            if memory is None:
                raise APIError(
                    code=ErrorCode.MEMORY_NOT_FOUND,
                    message=f"Memory {memory_id} not found",
                    status_code=404,
                )
            
            # Handle field name mismatch: storage uses "data" but get_memory returns "content"
            # If content is empty, try to get it from the underlying storage payload
            if not memory.get("content") or memory.get("content") == "":
                try:
                    # Access underlying storage to get raw payload with "data" field
                    storage_adapter = self.memory.storage
                    result = storage_adapter.vector_store.get(memory_id)
                    if result and result.payload:
                        data_content = result.payload.get("data", "")
                        if data_content:
                            memory["content"] = data_content
                except Exception as e:
                    logger.warning(f"Failed to get content from storage payload for memory {memory_id}: {e}")
            
            return memory
            
        except APIError:
            raise
        except Exception as e:
            logger.error(f"Failed to get memory {memory_id}: {e}", exc_info=True)
            raise APIError(
                code=ErrorCode.INTERNAL_ERROR,
                message=f"Failed to get memory: {str(e)}",
                status_code=500,
            )
    
    def list_memories(
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
        """
        List memories with pagination and sorting.
        
        Args:
            user_id: Filter by user ID
            agent_id: Filter by agent ID
            run_id: Filter by run ID
            limit: Maximum number of results
            offset: Number of results to skip
            sort_by: Optional field to sort by: 'created_at', 'updated_at', 'id'
            order: Sort order: 'desc' (descending) or 'asc' (ascending)
            filters: Additional metadata filters
            
        Returns:
            List of memories
        """
        try:
            kwargs = {
                "user_id": user_id,
                "agent_id": agent_id,
                "limit": limit,
                "offset": offset,
                "filters": filters,
                "sort_by": sort_by,
                "order": order,
            }
            if run_id is not None:
                kwargs["run_id"] = run_id
            result = self.memory.get_all(**kwargs)
            
            # Extract results from the dictionary response
            # get_all returns {"results": [...], "relations": [...]}
            memories_list = result.get("results", [])
            
            # Filter out non-dict items and ensure all items are dictionaries
            filtered_memories = []
            for item in memories_list:
                if isinstance(item, dict):
                    filtered_memories.append(item)
                else:
                    logger.warning(f"Skipping non-dict item in memories list: {type(item)} - {item}")
            
            return filtered_memories
            
        except Exception as e:
            logger.error(f"Failed to list memories: {e}", exc_info=True)
            raise APIError(
                code=ErrorCode.INTERNAL_ERROR,
                message=f"Failed to list memories: {str(e)}",
                status_code=500,
            )
    
    def count_memories(
        self,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
        filters: Optional[Dict[str, Any]] = None,
    ) -> int:
        """
        Count total memories matching the filters.
        
        Args:
            user_id: Filter by user ID
            agent_id: Filter by agent ID
            run_id: Filter by run ID
            filters: Additional metadata filters
            
        Returns:
            Total count of memories
        """
        try:
            # Use the new count_all method from core Memory
            kwargs = {
                "user_id": user_id,
                "agent_id": agent_id,
                "filters": filters,
            }
            if run_id is not None:
                kwargs["run_id"] = run_id
            count = self.memory.count_all(**kwargs)
            return count
            
        except Exception as e:
            logger.error(f"Failed to count memories: {e}", exc_info=True)
            return 0

    @staticmethod
    def _as_dict(value: Any) -> Dict[str, Any]:
        """Return value when it is a dict, otherwise an empty dict."""
        return value if isinstance(value, dict) else {}

    @classmethod
    def _memory_metadata(cls, memory: Dict[str, Any]) -> Dict[str, Any]:
        return cls._as_dict(memory.get("metadata"))

    @classmethod
    def _memory_observation(cls, memory: Dict[str, Any]) -> Dict[str, Any]:
        return cls._as_dict(cls._memory_metadata(memory).get("observation"))

    @staticmethod
    def _string_value(value: Any) -> Optional[str]:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @classmethod
    def _memory_field(cls, memory: Dict[str, Any], *keys: str) -> Optional[str]:
        metadata = cls._memory_metadata(memory)
        observation = cls._memory_observation(memory)
        for key in keys:
            value = (
                cls._string_value(memory.get(key))
                or cls._string_value(metadata.get(key))
                or cls._string_value(observation.get(key))
            )
            if value is not None:
                return value
        return None

    @classmethod
    def _memory_id(cls, memory: Dict[str, Any]) -> Optional[str]:
        return cls._memory_field(memory, "memory_id", "id")

    @classmethod
    def _memory_run_id(cls, memory: Dict[str, Any]) -> Optional[str]:
        return cls._memory_field(memory, "run_id", "session_id", "thread_id")

    @classmethod
    def _memory_user_id(cls, memory: Dict[str, Any]) -> Optional[str]:
        return cls._memory_field(memory, "user_id")

    @classmethod
    def _memory_agent_id(cls, memory: Dict[str, Any]) -> Optional[str]:
        return cls._memory_field(memory, "agent_id")

    @staticmethod
    def _parse_datetime(value: Any) -> Optional[datetime]:
        if value is None:
            return None
        if isinstance(value, datetime):
            parsed = value
        elif isinstance(value, str):
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return None
        else:
            return None
        if parsed.tzinfo is not None:
            return parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed

    @classmethod
    def _memory_datetime(cls, memory: Dict[str, Any]) -> Optional[datetime]:
        metadata = cls._memory_metadata(memory)
        observation = cls._memory_observation(memory)
        return cls._parse_datetime(
            metadata.get("occurred_at")
            or observation.get("occurred_at")
            or observation.get("timestamp")
            or memory.get("created_at")
            or memory.get("updated_at")
        )

    @staticmethod
    def _datetime_to_iso(value: Optional[datetime]) -> Optional[str]:
        if value is None:
            return None
        return value.replace(tzinfo=None).isoformat() + "Z"

    @classmethod
    def _memory_timestamp(cls, memory: Dict[str, Any]) -> Optional[str]:
        parsed = cls._memory_datetime(memory)
        if parsed is not None:
            return cls._datetime_to_iso(parsed)
        value = memory.get("created_at") or memory.get("updated_at")
        return cls._string_value(value)

    @classmethod
    def _memory_content(cls, memory: Dict[str, Any]) -> str:
        content = (
            memory.get("content")
            or memory.get("memory")
            or memory.get("data")
            or cls._memory_observation(memory).get("content")
            or ""
        )
        return str(content)

    @staticmethod
    def _preview(content: Any, limit: int = 240) -> str:
        text = " ".join(str(content or "").split())
        if len(text) <= limit:
            return text
        return text[: max(limit - 1, 0)].rstrip() + "..."

    @classmethod
    def _source_preview(cls, value: Any, limit: int = 240) -> str:
        if isinstance(value, (dict, list)):
            try:
                value = SESSION_TIMELINE_JSON_ENCODER.encode(value)
            except (TypeError, ValueError):
                value = str(value)
        return cls._preview(value, limit=limit)

    @classmethod
    def _event_type(cls, memory: Dict[str, Any]) -> str:
        metadata = cls._memory_metadata(memory)
        observation = cls._memory_observation(memory)
        for key in (
            "event_type",
            "observation_kind",
            "hook_event",
            "record_kind",
            "memory_type",
            "source",
        ):
            value = cls._string_value(metadata.get(key)) or cls._string_value(
                observation.get(key)
            )
            if value is not None:
                return value
        return "memory"

    @classmethod
    def _pipeline_mode(cls, memory: Dict[str, Any]) -> Optional[str]:
        metadata = cls._memory_metadata(memory)
        observation = cls._memory_observation(memory)
        return (
            cls._string_value(metadata.get("pipeline_mode"))
            or cls._string_value(observation.get("pipeline_mode"))
            or cls._string_value(metadata.get("inference_mode"))
            or cls._string_value(observation.get("inference_mode"))
        )

    @classmethod
    def _is_noop_event(cls, memory: Dict[str, Any]) -> bool:
        metadata = cls._memory_metadata(memory)
        observation = cls._memory_observation(memory)
        values = [
            cls._event_type(memory),
            metadata.get("observation_status"),
            observation.get("observation_status"),
            metadata.get("reason_status"),
            observation.get("reason_status"),
        ]
        normalized = {str(value).strip().lower() for value in values if value}
        return bool(normalized & {"none", "noop", "no_op", "skipped", "unchanged"})

    @classmethod
    def _timeline_metadata(
        cls,
        memory: Dict[str, Any],
        include_source: bool,
    ) -> Dict[str, Any]:
        metadata = dict(cls._memory_metadata(memory))
        if not include_source:
            metadata = cls._redact_source_metadata(metadata)
        return metadata

    @classmethod
    def _redact_source_metadata(cls, value: Any) -> Any:
        """Replace source-heavy metadata fields with bounded previews."""
        if isinstance(value, dict):
            redacted = {}
            for key, item in value.items():
                if key.lower() in SESSION_TIMELINE_SOURCE_KEYS:
                    preview = cls._source_preview(item)
                    if preview:
                        redacted[f"{key}_preview"] = preview
                    continue
                redacted[key] = cls._redact_source_metadata(item)
            return redacted
        if isinstance(value, list):
            return [cls._redact_source_metadata(item) for item in value]
        return value

    @classmethod
    def _is_session_memory(cls, memory: Dict[str, Any]) -> bool:
        metadata = cls._memory_metadata(memory)
        observation = cls._memory_observation(memory)
        return bool(
            cls._memory_run_id(memory)
            or metadata.get("schema") == OBSERVATION_SCHEMA
            or metadata.get("scope") == OBSERVATION_SCOPE
            or metadata.get("source") == OBSERVATION_SOURCE
            or observation.get("source") == OBSERVATION_SOURCE
        )

    def _load_session_memories(
        self,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
        cutoff_date: Optional[datetime] = None,
        sort_order: str = "desc",
    ) -> List[Dict[str, Any]]:
        """Load a bounded memory snapshot for session/timeline projections."""
        memories = self.list_memories(
            user_id=user_id,
            agent_id=agent_id,
            limit=SESSION_TIMELINE_FETCH_CAP,
            offset=0,
            sort_by="created_at",
            order=sort_order,
        )
        filtered = []
        for memory in memories:
            if not self._is_session_memory(memory):
                continue
            if run_id is not None and self._memory_run_id(memory) != run_id:
                continue
            occurred_at = self._memory_datetime(memory)
            if cutoff_date is not None and (
                occurred_at is None or occurred_at < cutoff_date.replace(tzinfo=None)
            ):
                continue
            filtered.append(memory)
        return filtered

    @staticmethod
    def _timeline_capabilities() -> Dict[str, bool]:
        return dict(SESSION_TIMELINE_CAPABILITIES)

    @classmethod
    def _event_sort_key(cls, event: Dict[str, Any]) -> Tuple[datetime, str, str]:
        occurred_at = cls._parse_datetime(event.get("occurred_at")) or datetime.min
        memory_id = event.get("memory_id") or ""
        event_id = event.get("event_id") or ""
        return occurred_at, str(memory_id), str(event_id)

    @staticmethod
    def _encode_timeline_cursor(event: Dict[str, Any]) -> str:
        payload = {
            "event_id": event.get("event_id"),
            "occurred_at": event.get("occurred_at"),
            "memory_id": event.get("memory_id"),
        }
        raw = SESSION_TIMELINE_CURSOR_ENCODER.encode(payload).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    @staticmethod
    def _decode_timeline_cursor(cursor: Optional[str]) -> Optional[Dict[str, Any]]:
        if not cursor:
            return None
        try:
            padded = cursor + "=" * (-len(cursor) % 4)
            decoded = base64.urlsafe_b64decode(padded.encode("ascii"))
            payload = json.loads(decoded.decode("utf-8"))
        except (binascii.Error, UnicodeDecodeError, ValueError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    @classmethod
    def _apply_timeline_cursor(
        cls,
        events: List[Dict[str, Any]],
        cursor: Optional[str],
    ) -> List[Dict[str, Any]]:
        payload = cls._decode_timeline_cursor(cursor)
        if not payload:
            return events
        for idx, event in enumerate(events):
            if (
                event.get("event_id") == payload.get("event_id")
                and event.get("occurred_at") == payload.get("occurred_at")
                and event.get("memory_id") == payload.get("memory_id")
            ):
                return events[idx + 1 :]
        return events

    @classmethod
    def _timeline_event_from_memory(
        cls,
        memory: Dict[str, Any],
        include_source: bool = False,
    ) -> Dict[str, Any]:
        memory_id = cls._memory_id(memory)
        metadata = cls._timeline_metadata(memory, include_source=include_source)
        observation = cls._as_dict(metadata.get("observation"))
        event_id = (
            cls._string_value(metadata.get("event_id"))
            or cls._string_value(metadata.get("observation_id"))
            or cls._string_value(observation.get("observation_id"))
            or memory_id
            or ""
        )
        content = cls._memory_content(memory)
        event = {
            "event_id": str(event_id),
            "occurred_at": cls._memory_timestamp(memory),
            "run_id": cls._memory_run_id(memory),
            "user_id": cls._memory_user_id(memory),
            "agent_id": cls._memory_agent_id(memory),
            "memory_id": memory_id,
            "event_type": cls._event_type(memory),
            "pipeline_mode": cls._pipeline_mode(memory),
            "content_preview": cls._preview(content),
            "metadata": metadata,
            "precision": SESSION_TIMELINE_PRECISION,
        }
        if include_source:
            event["source_preview"] = cls._preview(content, limit=1000)
            event["source_content"] = content
        return event

    @staticmethod
    def _event_matches_query(event: Dict[str, Any], query: Optional[str]) -> bool:
        if not query:
            return True
        needle = query.strip().lower()
        if not needle:
            return True
        haystack = [
            event.get("content_preview"),
            event.get("event_type"),
            event.get("run_id"),
            event.get("user_id"),
            event.get("agent_id"),
            event.get("memory_id"),
            SESSION_TIMELINE_JSON_ENCODER.encode(event.get("metadata") or {}),
        ]
        return any(needle in str(value or "").lower() for value in haystack)

    def list_session_summaries(
        self,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
        cutoff_date: Optional[datetime] = None,
        limit: int = 50,
        offset: int = 0,
        sort_by: str = "last_seen",
        order: str = "desc",
    ) -> Dict[str, Any]:
        """Return session summaries projected from the current memory snapshot."""
        memories = self._load_session_memories(
            user_id=user_id,
            agent_id=agent_id,
            run_id=run_id,
            cutoff_date=cutoff_date,
            sort_order=order,
        )
        sessions: Dict[str, Dict[str, Any]] = {}
        for memory in memories:
            session_run_id = self._memory_run_id(memory)
            if not session_run_id:
                continue
            occurred_at = self._memory_datetime(memory)
            occurred_iso = self._datetime_to_iso(occurred_at)
            session = sessions.setdefault(
                session_run_id,
                {
                    "run_id": session_run_id,
                    "user_id": self._memory_user_id(memory),
                    "agent_id": self._memory_agent_id(memory),
                    "first_seen": occurred_iso,
                    "last_seen": occurred_iso,
                    "event_count": 0,
                    "memory_ids": set(),
                    "latest_preview": "",
                    "latest_at": occurred_at,
                    "precision": SESSION_TIMELINE_PRECISION,
                },
            )
            session["event_count"] += 1
            memory_id = self._memory_id(memory)
            if memory_id is not None:
                session["memory_ids"].add(memory_id)

            first_at = self._parse_datetime(session.get("first_seen"))
            last_at = self._parse_datetime(session.get("last_seen"))
            if occurred_at is not None and (first_at is None or occurred_at < first_at):
                session["first_seen"] = occurred_iso
            if occurred_at is not None and (last_at is None or occurred_at >= last_at):
                session["last_seen"] = occurred_iso
                session["latest_at"] = occurred_at
                session["latest_preview"] = self._preview(self._memory_content(memory))

        summaries = []
        for session in sessions.values():
            summaries.append({
                "run_id": session["run_id"],
                "user_id": session.get("user_id"),
                "agent_id": session.get("agent_id"),
                "first_seen": session.get("first_seen"),
                "last_seen": session.get("last_seen"),
                "event_count": session["event_count"],
                "memory_count": len(session["memory_ids"]),
                "latest_preview": session.get("latest_preview") or "",
                "precision": SESSION_TIMELINE_PRECISION,
            })

        sort_field = sort_by if sort_by in {
            "run_id",
            "first_seen",
            "last_seen",
            "event_count",
            "memory_count",
        } else "last_seen"

        def sort_key(item: Dict[str, Any]):
            if sort_field in {"event_count", "memory_count"}:
                return item.get(sort_field) or 0
            if sort_field in {"first_seen", "last_seen"}:
                return self._parse_datetime(item.get(sort_field)) or datetime.min
            return str(item.get(sort_field) or "")

        summaries.sort(key=sort_key, reverse=(order != "asc"))
        total = len(summaries)
        page = summaries[offset:offset + limit]
        return {
            "sessions": page,
            "total": total,
            "limit": limit,
            "offset": offset,
            "precision": SESSION_TIMELINE_PRECISION,
            "capabilities": self._timeline_capabilities(),
        }

    def get_session_stats(
        self,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
        cutoff_date: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Return overview metrics for coding-agent session timeline views."""
        memories = self._load_session_memories(
            user_id=user_id,
            agent_id=agent_id,
            run_id=run_id,
            cutoff_date=cutoff_date,
        )
        session_ids = {
            session_run_id
            for memory in memories
            if (session_run_id := self._memory_run_id(memory))
        }
        changed_memory_ids = {
            memory_id
            for memory in memories
            if (memory_id := self._memory_id(memory))
        }
        event_types: Dict[str, int] = {}
        no_op_events = 0
        for memory in memories:
            event_type = self._event_type(memory)
            event_types[event_type] = event_types.get(event_type, 0) + 1
            if self._is_noop_event(memory):
                no_op_events += 1
        total_events = len(memories)
        return {
            "total_sessions": len(session_ids),
            "total_events": total_events,
            "changed_memories": len(changed_memory_ids),
            "no_op_events": no_op_events,
            "no_op_rate": round(no_op_events / total_events, 4) if total_events else 0.0,
            "event_types": event_types,
            "precision": SESSION_TIMELINE_PRECISION,
            "capabilities": self._timeline_capabilities(),
        }

    def list_timeline_events(
        self,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
        event_type: Optional[str] = None,
        q: Optional[str] = None,
        cursor: Optional[str] = None,
        limit: int = 50,
        order: str = "desc",
        cutoff_date: Optional[datetime] = None,
        include_source: bool = False,
    ) -> Dict[str, Any]:
        """Return a cursor-paginated timeline projected from the memory snapshot."""
        memories = self._load_session_memories(
            user_id=user_id,
            agent_id=agent_id,
            run_id=run_id,
            cutoff_date=cutoff_date,
            sort_order=order,
        )
        events = [
            self._timeline_event_from_memory(memory, include_source=include_source)
            for memory in memories
        ]
        if event_type:
            expected = event_type.strip().lower()
            events = [
                event for event in events
                if str(event.get("event_type") or "").lower() == expected
            ]
        events = [
            event for event in events
            if self._event_matches_query(event, q)
        ]
        events.sort(key=self._event_sort_key, reverse=(order != "asc"))
        total = len(events)
        events_after_cursor = self._apply_timeline_cursor(events, cursor)
        page = events_after_cursor[:limit]
        next_cursor = None
        if len(events_after_cursor) > limit and page:
            next_cursor = self._encode_timeline_cursor(page[-1])
        return {
            "events": page,
            "total": total,
            "limit": limit,
            "next_cursor": next_cursor,
            "order": order,
            "precision": SESSION_TIMELINE_PRECISION,
            "capabilities": self._timeline_capabilities(),
        }
    
    def update_memory(
        self,
        memory_id: int,
        content: Optional[str] = None,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Update a memory.
        
        Args:
            memory_id: Memory ID
            content: New content (optional)
            user_id: User ID for access control
            agent_id: Agent ID for access control
            metadata: Updated metadata (optional)
            
        Returns:
            Updated memory data
            
        Raises:
            APIError: If update fails
        """
        try:
            # First check if memory exists
            existing = self.get_memory(memory_id, user_id, agent_id)
            if existing is None:
                raise APIError(
                    code=ErrorCode.MEMORY_NOT_FOUND,
                    message=f"Memory not found: {memory_id}",
                    status_code=404,
                )
            
            # At least one of content or metadata must be provided
            if content is None and metadata is None:
                raise ValueError("At least one of content or metadata must be provided")
            
            # Use existing content if new content not provided
            final_content = content if content is not None else existing.get("content", "")
            
            # Merge metadata if both existing and new metadata exist
            final_metadata = metadata
            if metadata is not None and existing.get("metadata"):
                final_metadata = {**existing.get("metadata", {}), **metadata}
            elif existing.get("metadata"):
                final_metadata = existing.get("metadata")
            
            result = self.memory.update(
                memory_id=memory_id,
                content=final_content,
                user_id=user_id,
                agent_id=agent_id,
                metadata=final_metadata,
            )
            if result is None:
                raise APIError(
                    code=ErrorCode.MEMORY_NOT_FOUND,
                    message=f"Memory not found or access denied: {memory_id}",
                    status_code=404,
                )
            
            # Ensure result contains id field (storage.update_memory returns payload without id)
            if result and "id" not in result:
                result["id"] = memory_id
                result["memory_id"] = memory_id
            
            logger.info(f"Memory updated: {memory_id}")
            return result
            
        except APIError:
            raise
        except Exception as e:
            logger.error(f"Failed to update memory {memory_id}: {e}", exc_info=True)
            raise APIError(
                code=ErrorCode.MEMORY_UPDATE_FAILED,
                message=f"Failed to update memory: {str(e)}",
                status_code=500,
            )

    def get_statistics(
        self,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        cutoff_date: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Get memory statistics with optional time filtering (same logic as CLI via shared stats)."""
        from powermem.utils.stats import _parse_datetime_for_stats, calculate_stats_from_memories

        all_memories = self.memory.get_all(
            user_id=user_id,
            agent_id=agent_id,
            limit=10000,
        ).get("results", [])

        if cutoff_date is not None:
            filtered = [
                m for m in all_memories
                if (parsed := _parse_datetime_for_stats(m.get("created_at"))) is not None
                and parsed >= cutoff_date
            ]
            return calculate_stats_from_memories(filtered)

        stats = calculate_stats_from_memories(all_memories)
        return stats

    def get_users(self) -> List[str]:
        """Get a list of unique user IDs"""
        return self.memory.get_users()

    def delete_memory(
        self,
        memory_id: int,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> bool:
        """
        Delete a memory.
        
        Args:
            memory_id: Memory ID
            user_id: User ID for access control
            agent_id: Agent ID for access control
            
        Returns:
            True if deleted successfully
            
        Raises:
            APIError: If deletion fails
        """
        try:
            # First check if memory exists
            self.get_memory(memory_id, user_id, agent_id)
            
            success = self.memory.delete(
                memory_id=memory_id,
                user_id=user_id,
                agent_id=agent_id,
            )
            
            if not success:
                raise APIError(
                    code=ErrorCode.MEMORY_DELETE_FAILED,
                    message=f"Failed to delete memory {memory_id}",
                    status_code=500,
                )
            
            logger.info(f"Memory deleted: {memory_id}")
            return True
            
        except APIError:
            raise
        except Exception as e:
            logger.error(f"Failed to delete memory {memory_id}: {e}", exc_info=True)
            raise APIError(
                code=ErrorCode.MEMORY_DELETE_FAILED,
                message=f"Failed to delete memory: {str(e)}",
                status_code=500,
            )
    
    def bulk_delete_memories(
        self,
        memory_ids: List[int],
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Delete multiple memories.
        
        Args:
            memory_ids: List of memory IDs
            user_id: User ID for access control
            agent_id: Agent ID for access control
            
        Returns:
            Dictionary with deletion results
        """
        deleted = []
        failed = []
        
        for memory_id in memory_ids:
            try:
                self.delete_memory(memory_id, user_id, agent_id)
                deleted.append(memory_id)
            except APIError as e:
                failed.append({"memory_id": memory_id, "error": e.message})
        
        return {
            "deleted": deleted,
            "failed": failed,
            "total": len(memory_ids),
            "deleted_count": len(deleted),
            "failed_count": len(failed),
        }
    
    def batch_create_memories(
        self,
        memories: List[Dict[str, Any]],
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
        infer: bool = True,
    ) -> Dict[str, Any]:
        """
        Create multiple memories in batch.
        
        Args:
            memories: List of memory items, each containing:
                - content: Memory content
                - metadata: Optional metadata (overrides common metadata)
                - filters: Optional filters (overrides common filters)
                - scope: Optional scope
                - memory_type: Optional memory type
            user_id: Common user ID for all memories
            agent_id: Common agent ID for all memories
            run_id: Common run ID for all memories
            infer: Enable intelligent processing
            
        Returns:
            Dictionary with creation results
        """
        created = []
        failed = []
        
        for idx, memory_item in enumerate(memories):
            try:
                content = memory_item.get("content")
                if not content:
                    raise ValueError("Memory content is required")
                
                # Use item-specific metadata/filters if provided, otherwise use common ones
                metadata = memory_item.get("metadata")
                filters = memory_item.get("filters")
                scope = memory_item.get("scope")
                memory_type = memory_item.get("memory_type")
                
                result = self.memory.add(
                    messages=content,
                    user_id=user_id,
                    agent_id=agent_id,
                    run_id=run_id,
                    metadata=metadata,
                    filters=filters,
                    scope=scope,
                    memory_type=memory_type,
                    infer=infer,
                )
                
                # Extract memory_id from result
                # Result format: {"results": [{"id": memory_id, ...}], ...}
                memory_id = None
                if "results" in result and len(result["results"]) > 0:
                    memory_id = result["results"][0].get("id")
                elif "memory_id" in result:
                    memory_id = result["memory_id"]
                elif "id" in result:
                    memory_id = result["id"]
                
                if memory_id is None:
                    raise ValueError("Failed to extract memory_id from result")
                
                created.append({
                    "index": idx,
                    "memory_id": memory_id,
                    "content": content,
                })
                
            except Exception as e:
                logger.error(f"Failed to create memory at index {idx}: {e}", exc_info=True)
                failed.append({
                    "index": idx,
                    "content": memory_item.get("content", "N/A"),
                    "error": str(e),
                })
        
        return {
            "created": created,
            "failed": failed,
            "total": len(memories),
            "created_count": len(created),
            "failed_count": len(failed),
        }
    
    def batch_update_memories(
        self,
        updates: List[Dict[str, Any]],
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Update multiple memories in batch.
        
        Args:
            updates: List of update items, each containing:
                - memory_id: Memory ID to update
                - content: Optional new content
                - metadata: Optional updated metadata
            user_id: User ID for access control
            agent_id: Agent ID for access control
            
        Returns:
            Dictionary with update results
        """
        updated = []
        failed = []
        
        for idx, update_item in enumerate(updates):
            try:
                memory_id = update_item.get("memory_id")
                if memory_id is None:
                    raise ValueError("memory_id is required for each update")
                
                content = update_item.get("content")
                metadata = update_item.get("metadata")
                
                # At least one of content or metadata must be provided
                if content is None and metadata is None:
                    raise ValueError("At least one of content or metadata must be provided")
                
                # Get existing memory to merge metadata if needed
                existing = self.get_memory(memory_id, user_id, agent_id)
                
                # Merge metadata if both existing and new metadata exist
                final_metadata = metadata
                if metadata is not None and existing.get("metadata"):
                    final_metadata = {**existing.get("metadata", {}), **metadata}
                elif existing.get("metadata"):
                    final_metadata = existing.get("metadata")
                
                # Use existing content if new content not provided
                final_content = content if content is not None else existing.get("content", "")
                
                result = self.memory.update(
                    memory_id=memory_id,
                    content=final_content,
                    user_id=user_id,
                    agent_id=agent_id,
                    metadata=final_metadata,
                )
                
                updated.append({
                    "index": idx,
                    "memory_id": memory_id,
                })
                
            except APIError as e:
                logger.error(f"Failed to update memory at index {idx}: {e}", exc_info=True)
                failed.append({
                    "index": idx,
                    "memory_id": update_item.get("memory_id"),
                    "error": e.message,
                })
            except Exception as e:
                logger.error(f"Failed to update memory at index {idx}: {e}", exc_info=True)
                failed.append({
                    "index": idx,
                    "memory_id": update_item.get("memory_id"),
                    "error": str(e),
                })
        
        return {
            "updated": updated,
            "failed": failed,
            "total": len(updates),
            "updated_count": len(updated),
            "failed_count": len(failed),
        }
    
    async def analyze_memory_quality(
        self,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        cutoff_date: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """
        Analyze memory quality and identify issues.
        
        Args:
            user_id: Filter by user ID
            agent_id: Filter by agent ID
            cutoff_date: Optional cutoff date for time filtering
            
        Returns:
            Dictionary with quality metrics:
                - total_memories: Total number of memories
                - low_quality_count: Number of low quality memories
                - low_quality_ratio: Ratio of low quality memories (0.0-1.0)
                - quality_criteria: Distribution of quality issues
        """
        try:
            from powermem.utils.stats import _extract_importance, _parse_datetime_for_stats

            # Get all memories (without pagination limit for analysis)
            result = self.memory.get_all(
                user_id=user_id,
                agent_id=agent_id,
                limit=10000,  # High limit for comprehensive analysis
                offset=0,
            )
            
            memories_list = result.get("results", [])
            
            # Filter by cutoff_date if provided (same parsing as stats)
            if cutoff_date:
                memories_list = [
                    m for m in memories_list
                    if (parsed := _parse_datetime_for_stats(m.get("created_at"))) is not None
                    and parsed >= cutoff_date
                ]
            
            total_memories = len(memories_list)
            
            if total_memories == 0:
                return {
                    "total_memories": 0,
                    "low_quality_count": 0,
                    "low_quality_ratio": 0.0,
                    "quality_criteria": {},
                }
            
            # Quality criteria counters
            quality_issues = {
                "missing_metadata": 0,
                "empty_content": 0,
                "low_importance": 0,
            }
            
            low_quality_memories = set()  # Use set to avoid counting same memory twice
            
            for memory in memories_list:
                memory_id = memory.get("id") or memory.get("memory_id")
                
                # Check for missing or empty metadata
                metadata = memory.get("metadata")
                if not metadata or (isinstance(metadata, dict) and len(metadata) == 0):
                    quality_issues["missing_metadata"] += 1
                    low_quality_memories.add(memory_id)
                
                # Check for empty or very short content
                # Note: get_all() returns 'memory' field, not 'content'
                content = memory.get("memory") or memory.get("content") or memory.get("data") or ""
                content_len = len(str(content).strip())
                if content_len < 5:
                    quality_issues["empty_content"] += 1
                    low_quality_memories.add(memory_id)
                
                # Check for low importance using the same extraction rule as stats.
                importance = _extract_importance(memory)
                if importance is not None and importance < 0.3:
                    quality_issues["low_importance"] += 1
                    low_quality_memories.add(memory_id)
            
            low_quality_count = len(low_quality_memories)
            low_quality_ratio = low_quality_count / total_memories if total_memories > 0 else 0.0
            
            logger.info(f"Quality analysis complete: {low_quality_count}/{total_memories} low quality memories")
            
            return {
                "total_memories": total_memories,
                "low_quality_count": low_quality_count,
                "low_quality_ratio": round(low_quality_ratio, 4),
                "quality_criteria": quality_issues,
            }
            
        except Exception as e:
            logger.error(f"Failed to analyze memory quality: {e}", exc_info=True)
            raise APIError(
                code=ErrorCode.INTERNAL_ERROR,
                message=f"Failed to analyze memory quality: {str(e)}",
                status_code=500,
            )
