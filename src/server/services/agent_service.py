"""
Agent service for PowerMem API
"""

import logging
from typing import Any, Dict, List, Optional
from powermem import auto_config
from powermem.agent import AgentMemory
from ..models.errors import ErrorCode, APIError

logger = logging.getLogger("server")


class AgentService:
    """Service for agent memory operations"""
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Initialize agent service.
        
        Args:
            config: PowerMem configuration (uses auto_config if None)
        """
        if config is None:
            config = auto_config()
        
        self.agent_memory = AgentMemory(config=config)
        logger.info("AgentService initialized")
    
    def get_agent_memories(
        self,
        agent_id: str,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """
        Get all memories for an agent.
        
        Args:
            agent_id: Agent ID
            limit: Maximum number of results
            offset: Number of results to skip
            
        Returns:
            List of memories
            
        Raises:
            APIError: If retrieval fails
        """
        try:
            if not agent_id:
                raise APIError(
                    code=ErrorCode.INVALID_REQUEST,
                    message="agent_id is required",
                    status_code=400,
                )
            
            # AgentMemory.get_all() doesn't support offset, so we need to handle it manually
            all_memories = self.agent_memory.get_all(
                agent_id=agent_id,
                limit=limit + offset,  # Get more results to account for offset
            )
            
            # Apply offset manually
            if offset > 0 and len(all_memories) > offset:
                memories = all_memories[offset:offset + limit]
            elif offset > 0:
                memories = []
            else:
                memories = all_memories[:limit]
            
            return memories
            
        except APIError:
            raise
        except Exception as e:
            logger.error(f"Failed to get agent memories {agent_id}: {e}", exc_info=True)
            raise APIError(
                code=ErrorCode.INTERNAL_ERROR,
                message=f"Failed to get agent memories: {str(e)}",
                status_code=500,
            )
    
    def create_agent_memory(
        self,
        agent_id: str,
        content: str,
        user_id: Optional[str] = None,
        run_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        filters: Optional[Dict[str, Any]] = None,
        scope: Optional[str] = None,
        memory_type: Optional[str] = None,
        infer: bool = True,
    ) -> Dict[str, Any]:
        """
        Create a memory for an agent.
        
        Uses AgentMemory system for intelligent memory management with
        multi-agent collaboration, permissions, and scope support.
        
        Args:
            agent_id: Agent ID
            content: Memory content
            user_id: User ID
            run_id: Run ID (stored in metadata)
            metadata: Metadata
            filters: Filters (stored in metadata)
            scope: Memory scope (e.g., 'AGENT', 'USER_GROUP', 'PUBLIC')
            memory_type: Memory type (stored in metadata)
            infer: Deprecated - AgentMemory handles intelligent processing internally
            
        Returns:
            Created memory data with memory_id field
            
        Raises:
            APIError: If creation fails
        """
        try:
            if not agent_id:
                raise APIError(
                    code=ErrorCode.INVALID_REQUEST,
                    message="agent_id is required",
                    status_code=400,
                )
            
            # Prepare metadata with run_id and other fields if provided
            enhanced_metadata = metadata or {}
            if run_id:
                enhanced_metadata["run_id"] = run_id
            if filters:
                enhanced_metadata["filters"] = filters
            if memory_type:
                enhanced_metadata["memory_type"] = memory_type
            
            # AgentMemory.add() returns a dict with memory information
            result = self.agent_memory.add(
                content=content,
                user_id=user_id,
                agent_id=agent_id,
                metadata=enhanced_metadata,
                scope=scope,
            )
            
            # Ensure memory_id field exists (use "id" from result)
            if isinstance(result, dict):
                if "id" in result and "memory_id" not in result:
                    result["memory_id"] = result["id"]
                logger.info(f"Agent memory created: {result.get('memory_id')} for agent {agent_id}")
                return result
            else:
                logger.error(f"Failed to create memory for agent {agent_id}: unexpected result type={type(result)}")
                raise APIError(
                    code=ErrorCode.MEMORY_CREATE_FAILED,
                    message="No memory was created. Unexpected result format.",
                    status_code=500,
                )
            
        except APIError:
            raise
        except Exception as e:
            logger.error(f"Failed to create agent memory: {e}", exc_info=True)
            raise APIError(
                code=ErrorCode.MEMORY_CREATE_FAILED,
                message=f"Failed to create agent memory: {str(e)}",
                status_code=500,
            )
    
    def share_memories(
        self,
        agent_id: str,
        target_agent_id: str,
        memory_ids: Optional[List[int]] = None,
    ) -> Dict[str, Any]:
        """
        Share memories between agents.
        
        Uses AgentMemory's share_memory method for proper memory sharing
        between agents with permission and collaboration support.
        
        Args:
            agent_id: Source agent ID
            target_agent_id: Target agent ID
            memory_ids: Specific memory IDs to share (None for all)
            
        Returns:
            Sharing result
            
        Raises:
            APIError: If sharing fails
        """
        try:
            if not agent_id or not target_agent_id:
                raise APIError(
                    code=ErrorCode.INVALID_REQUEST,
                    message="agent_id and target_agent_id are required",
                    status_code=400,
                )
            
            # Get memories to share
            if memory_ids:
                # Get specific memories by ID
                memories = []
                all_memories = self.agent_memory.get_all(agent_id=agent_id)
                logger.info(f"Found {len(all_memories)} total memories for agent {agent_id}, filtering by {len(memory_ids)} specific IDs: {memory_ids}")
                
                # Convert memory_ids to set, handling both int and str types
                memory_id_set = set(memory_ids)
                # Also create a set with string versions for type compatibility
                memory_id_set_str = {str(mid) for mid in memory_ids}
                
                # Debug: log first memory's ID to understand the format
                if all_memories:
                    first_mem_id = all_memories[0].get("id") or all_memories[0].get("memory_id")
                    logger.debug(f"Sample memory ID from get_all: {first_mem_id} (type: {type(first_mem_id).__name__}), requested IDs: {memory_ids} (types: {[type(mid).__name__ for mid in memory_ids]})")
                
                for memory in all_memories:
                    mem_id = memory.get("id") or memory.get("memory_id")
                    # Check both original type and string type for compatibility
                    if mem_id in memory_id_set or str(mem_id) in memory_id_set_str:
                        memories.append(memory)
                        logger.debug(f"Matched memory ID {mem_id} (type: {type(mem_id).__name__})")
                    else:
                        logger.debug(f"Memory ID {mem_id} (type: {type(mem_id).__name__}) not in requested IDs {memory_ids}. memory_id_set={memory_id_set}, memory_id_set_str={memory_id_set_str}")
                
                logger.info(f"After filtering, found {len(memories)} matching memories")
            else:
                memories = self.agent_memory.get_all(agent_id=agent_id)
                logger.info(f"Found {len(memories)} memories for agent {agent_id} to share")
            
            if not memories:
                logger.warning(f"No memories found for agent {agent_id}. Cannot share memories.")
                return {
                    "shared_count": 0,
                    "source_agent_id": agent_id,
                    "target_agent_id": target_agent_id,
                }
            
            # Use AgentMemory's share_memory method for proper sharing
            shared_count = 0
            for memory in memories:
                try:
                    mem_id = memory.get("id") or memory.get("memory_id")
                    if not mem_id:
                        logger.warning(f"Memory missing ID, skipping: {memory}")
                        continue
                    
                    # Try to use the share_memory method if available and supported
                    use_share_method = False
                    if hasattr(self.agent_memory, 'share_memory'):
                        # Check if the mode supports share_memory
                        current_mode = self.agent_memory.get_mode() if hasattr(self.agent_memory, 'get_mode') else None
                        if current_mode in ['multi_agent', 'hybrid']:
                            use_share_method = True
                    
                    if use_share_method:
                        try:
                            share_result = self.agent_memory.share_memory(
                                memory_id=str(mem_id),
                                from_agent=agent_id,
                                to_agents=[target_agent_id],
                            )
                            if share_result.get("success", False):
                                shared_count += 1
                                logger.debug(f"Successfully shared memory {mem_id} using share_memory method")
                            else:
                                logger.warning(f"share_memory returned unsuccessful result for memory {mem_id}, falling back to copy")
                                # Fallback to copy
                                try:
                                    self._copy_memory_to_agent(memory, target_agent_id)
                                    shared_count += 1
                                except ValueError as e:
                                    logger.warning(f"Skipping memory {mem_id} due to empty content: {e}")
                                except Exception as e:
                                    logger.warning(f"Failed to copy memory {mem_id} to agent {target_agent_id}: {e}")
                        except (RuntimeError, ValueError, PermissionError) as e:
                            # share_memory not supported or failed, fallback to copy
                            logger.info(f"share_memory not supported or failed for memory {mem_id}: {e}. Using fallback copy method.")
                            try:
                                self._copy_memory_to_agent(memory, target_agent_id)
                                shared_count += 1
                            except ValueError as ve:
                                logger.warning(f"Skipping memory {mem_id} due to empty content: {ve}")
                            except Exception as copy_e:
                                logger.warning(f"Failed to copy memory {mem_id} to agent {target_agent_id}: {copy_e}")
                    else:
                        # Fallback: copy memory to target agent
                        logger.debug(f"Using fallback copy method for memory {mem_id}")
                        try:
                            self._copy_memory_to_agent(memory, target_agent_id)
                            shared_count += 1
                        except ValueError as e:
                            # Skip memories with empty content, but don't fail the entire operation
                            logger.warning(f"Skipping memory {mem_id} due to empty content: {e}")
                        except Exception as e:
                            # Log other errors but continue with other memories
                            logger.warning(f"Failed to copy memory {mem_id} to agent {target_agent_id}: {e}")
                except Exception as e:
                    logger.warning(f"Failed to share memory {memory.get('id') or memory.get('memory_id')}: {e}", exc_info=True)
            
            logger.info(f"Shared {shared_count} memories from {agent_id} to {target_agent_id}")
            
            return {
                "shared_count": shared_count,
                "source_agent_id": agent_id,
                "target_agent_id": target_agent_id,
            }
            
        except APIError:
            raise
        except Exception as e:
            logger.error(f"Failed to share memories: {e}", exc_info=True)
            raise APIError(
                code=ErrorCode.AGENT_MEMORY_SHARE_FAILED,
                message=f"Failed to share memories: {str(e)}",
                status_code=500,
            )
    
    def _copy_memory_to_agent(
        self,
        memory: Dict[str, Any],
        target_agent_id: str
    ) -> None:
        """
        Copy a memory to another agent (fallback method when share_memory is not supported).
        
        Args:
            memory: Memory dictionary to copy
            target_agent_id: Target agent ID
            
        Raises:
            ValueError: If memory content is empty or missing
        """
        # Extract content from various possible field names
        # Storage adapter returns "memory" field, get_memories returns "content" field
        memory_id = memory.get("id") or memory.get("memory_id")
        
        # Debug: log memory structure to understand the issue
        logger.debug(f"Copying memory {memory_id} to agent {target_agent_id}. Memory keys: {list(memory.keys())}")
        
        # AgentMemory.get_all() returns memories with 'content' field (from get_memories())
        # The agent layer standardizes to 'content' field, but keep 'memory' as fallback
        # for compatibility with direct storage access
        content = memory.get("content") or memory.get("memory", "")
        
        # Strip whitespace and check if content is actually empty
        if isinstance(content, str):
            content = content.strip()
        elif content:
            content = str(content).strip()
        else:
            content = ""
        
        if not content:
            # Try to get content from metadata if available
            metadata = memory.get("metadata", {})
            if isinstance(metadata, dict):
                content = (
                    metadata.get("content") or 
                    metadata.get("memory") or 
                    metadata.get("data") or 
                    ""
                )
                if isinstance(content, str):
                    content = content.strip()
                elif content:
                    content = str(content).strip()
                else:
                    content = ""
        
        if not content:
            logger.warning(
                f"Memory {memory_id} has empty content, skipping copy to agent {target_agent_id}. "
                f"Memory fields: content={memory.get('content')}, memory={memory.get('memory')}, "
                f"data={memory.get('data')}, document={memory.get('document')}"
            )
            raise ValueError(f"Cannot copy memory {memory_id} with empty content")
        
        try:
            # Extract scope - handle both enum and string
            scope = memory.get("scope")
            if scope and hasattr(scope, 'value'):
                scope = scope.value
            elif not isinstance(scope, str):
                scope = None
            
            self.agent_memory.add(
                content=content,
                user_id=memory.get("user_id"),
                agent_id=target_agent_id,
                metadata=memory.get("metadata", {}),
                scope=scope,
            )
            logger.debug(f"Successfully copied memory {memory.get('id') or memory.get('memory_id')} to agent {target_agent_id}")
        except Exception as e:
            logger.error(f"Failed to copy memory {memory.get('id') or memory.get('memory_id')} to agent {target_agent_id}: {e}")
            raise
    
    def get_shared_memories(
        self,
        agent_id: str,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """
        Get shared memories for an agent.
        
        Note: This is a simplified implementation. Full implementation would
        track sharing relationships.
        
        Args:
            agent_id: Agent ID
            limit: Maximum number of results
            offset: Number of results to skip
            
        Returns:
            List of shared memories
        """
        # For now, return all memories for the agent
        # In a full implementation, this would filter for shared memories only
        return self.get_agent_memories(agent_id, limit, offset)
