"""
Memory service for PowerMem API
"""

import logging
from typing import Any, Dict, List, Optional
from datetime import datetime
from powermem import Memory, auto_config
from ..models.errors import ErrorCode, APIError
from ..utils.converters import memory_dict_to_response
from ..utils.metrics import get_metrics_collector

logger = logging.getLogger("server")


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
        limit: int = 100,
        offset: int = 0,
        sort_by: Optional[str] = None,
        order: str = "desc",
    ) -> List[Dict[str, Any]]:
        """
        List memories with pagination and sorting.
        
        Args:
            user_id: Filter by user ID
            agent_id: Filter by agent ID
            limit: Maximum number of results
            offset: Number of results to skip
            sort_by: Optional field to sort by: 'created_at', 'updated_at', 'id'
            order: Sort order: 'desc' (descending) or 'asc' (ascending)
            
        Returns:
            List of memories
        """
        try:
            result = self.memory.get_all(
                user_id=user_id,
                agent_id=agent_id,
                limit=limit,
                offset=offset,
                sort_by=sort_by,
                order=order,
            )
            
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
    ) -> int:
        """
        Count total memories matching the filters.
        
        Args:
            user_id: Filter by user ID
            agent_id: Filter by agent ID
            
        Returns:
            Total count of memories
        """
        try:
            # Use the new count_all method from core Memory
            count = self.memory.count_all(
                user_id=user_id,
                agent_id=agent_id,
            )
            return count
            
        except Exception as e:
            logger.error(f"Failed to count memories: {e}", exc_info=True)
            return 0
    
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
        cutoff_date: Optional[datetime] = None
    ) -> Dict[str, Any]:
        """Get memory statistics with optional time filtering"""
        # Get base statistics from Memory
        stats = self.memory.get_statistics(user_id=user_id, agent_id=agent_id)
        
        # If cutoff_date is provided, we need to filter the results
        if cutoff_date:
            # Get all memories to filter by date
            all_memories = self.memory.get_all(
                user_id=user_id,
                agent_id=agent_id,
                limit=10000
            ).get("results", [])
            
            # Filter memories by created_at
            filtered_memories = [
                m for m in all_memories
                if self._parse_datetime(m.get("created_at")) >= cutoff_date
            ]
            
            # Recalculate statistics for filtered memories
            stats = self._calculate_stats_from_memories(filtered_memories)
        
        return stats
    
    def _parse_datetime(self, date_str: Optional[str]) -> datetime:
        """Parse datetime string to datetime object"""
        if not date_str:
            return datetime.min
        try:
            # Try ISO format with timezone
            from dateutil import parser
            return parser.parse(date_str)
        except:
            return datetime.min
    
    def _calculate_stats_from_memories(self, memories: List[Dict]) -> Dict[str, Any]:
        """Calculate statistics from a list of memories"""
        from collections import defaultdict
        from datetime import datetime, timedelta, timezone
        
        total_memories = len(memories)
        if total_memories == 0:
            return {
                "total_memories": 0,
                "by_type": {},
                "avg_importance": 0.0,
                "top_accessed": [],
                "growth_trend": {},
                "age_distribution": {
                    "< 1 day": 0,
                    "1-7 days": 0,
                    "7-30 days": 0,
                    "> 30 days": 0,
                },
            }
        
        # Calculate statistics
        by_type = defaultdict(int)
        total_importance = 0.0
        access_counts = []
        growth_by_date = defaultdict(int)
        age_distribution = {
            "< 1 day": 0,
            "1-7 days": 0,
            "7-30 days": 0,
            "> 30 days": 0,
        }
        
        now = datetime.now(timezone.utc)
        
        for m in memories:
            # Type distribution
            mem_type = m.get("type") or m.get("memory_type") or "unknown"
            by_type[mem_type] += 1
            
            # Importance
            importance = m.get("importance") or 0.5
            total_importance += float(importance)
            
            # Access count
            access_count = m.get("access_count", 0)
            if access_count > 0:
                access_counts.append({
                    "id": m.get("id") or m.get("memory_id"),
                    "content": (m.get("memory") or m.get("content") or "")[:100],
                    "access_count": access_count,
                })
            
            # Growth trend by date
            created_at = m.get("created_at")
            if created_at:
                date_obj = self._parse_datetime(created_at)
                date_key = date_obj.strftime("%Y-%m-%d")
                growth_by_date[date_key] += 1
                
                # Age distribution
                age = (now - date_obj).days
                if age < 1:
                    age_distribution["< 1 day"] += 1
                elif age < 7:
                    age_distribution["1-7 days"] += 1
                elif age < 30:
                    age_distribution["7-30 days"] += 1
                else:
                    age_distribution["> 30 days"] += 1
        
        # Sort and limit top accessed
        access_counts.sort(key=lambda x: x["access_count"], reverse=True)
        top_accessed = access_counts[:10]
        
        return {
            "total_memories": total_memories,
            "by_type": dict(by_type),
            "avg_importance": total_importance / total_memories if total_memories > 0 else 0.0,
            "top_accessed": top_accessed,
            "growth_trend": dict(growth_by_date),
            "age_distribution": age_distribution,
        }

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
            # Get all memories (without pagination limit for analysis)
            result = self.memory.get_all(
                user_id=user_id,
                agent_id=agent_id,
                limit=10000,  # High limit for comprehensive analysis
                offset=0,
            )
            
            memories_list = result.get("results", [])
            
            # Filter by cutoff_date if provided
            if cutoff_date:
                memories_list = [
                    m for m in memories_list
                    if self._parse_datetime(m.get("created_at")) >= cutoff_date
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
                
                # Check for low importance (if importance field exists)
                importance = memory.get("importance")
                if importance is not None and float(importance) < 0.3:
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