"""
Compatibility Wrapper

Provides backward compatibility with the original Memory class and multi_agent
implementation, ensuring existing code continues to work without changes.
"""

import logging
from typing import Any, Dict, List, Optional

from typing import Any, Dict
from powermem.agent.wrappers.agent_memory_wrapper import AgentMemoryWrapper

logger = logging.getLogger(__name__)


class CompatibilityWrapper:
    """
    Compatibility wrapper that provides backward compatibility with the original
    Memory class and multi_agent implementation.
    
    This wrapper ensures that existing code continues to work without changes
    while providing access to the new agent memory management features.
    """
    
    def __init__(self, config: Dict[str, Any]):
        """
        Initialize the compatibility wrapper.
        
        Args:
            config: Memory configuration object
        """
        self.config = config
        self.agent_memory_wrapper = AgentMemoryWrapper(config)
        self.initialized = False
    
    def initialize(self) -> None:
        """
        Initialize the compatibility wrapper.
        """
        try:
            self.agent_memory_wrapper.initialize()
            self.initialized = True
            logger.info("Compatibility wrapper initialized successfully")
            
        except Exception as e:
            logger.error(f"Failed to initialize compatibility wrapper: {e}", exc_info=True)
            self.initialized = False
    
    def is_enabled(self) -> bool:
        """
        Check if agent memory management is enabled.
        
        Returns:
            True if enabled, False otherwise
        """
        return self.agent_memory_wrapper.is_enabled()
    
    # Backward compatibility methods for original multi_agent interface
    
    def add_agent_memory(
        self,
        content: str,
        agent_id: str,
        context: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Add agent memory (backward compatibility method).
        
        Args:
            content: Memory content
            agent_id: ID of the agent
            context: Optional context information
            metadata: Optional metadata
            
        Returns:
            Dictionary containing the result
        """
        return self.agent_memory_wrapper.add_memory(
            content=content,
            agent_id=agent_id,
            context=context,
            metadata=metadata
        )
    
    def get_agent_memories(
        self,
        agent_id: str,
        query: Optional[str] = None,
        filters: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """
        Get agent memories (backward compatibility method).
        
        Args:
            agent_id: ID of the agent
            query: Optional query string
            filters: Optional filters
            
        Returns:
            List of memory dictionaries
        """
        return self.agent_memory_wrapper.get_memories(
            agent_id=agent_id,
            query=query,
            filters=filters
        )
    
    def share_agent_memory(
        self,
        memory_id: str,
        from_agent: str,
        to_agents: List[str],
        permissions: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        Share agent memory (backward compatibility method).
        
        Args:
            memory_id: ID of the memory
            from_agent: ID of the agent sharing
            to_agents: List of agent IDs to share with
            permissions: Optional permissions
            
        Returns:
            Dictionary containing the result
        """
        return self.agent_memory_wrapper.share_memory(
            memory_id=memory_id,
            from_agent=from_agent,
            to_agents=to_agents,
            permissions=permissions
        )
    
    def get_agent_context(self, agent_id: str) -> Dict[str, Any]:
        """
        Get agent context (backward compatibility method).
        
        Args:
            agent_id: ID of the agent
            
        Returns:
            Dictionary containing context information
        """
        return self.agent_memory_wrapper.get_context_info(agent_id=agent_id)
    
    def update_agent_memory_decay(self) -> Dict[str, Any]:
        """
        Update agent memory decay (backward compatibility method).
        
        Returns:
            Dictionary containing the result
        """
        return self.agent_memory_wrapper.update_memory_decay()
    
    def cleanup_agent_forgotten_memories(self) -> Dict[str, Any]:
        """
        Cleanup agent forgotten memories (backward compatibility method).
        
        Returns:
            Dictionary containing the result
        """
        return self.agent_memory_wrapper.cleanup_forgotten_memories()
    
    def get_agent_memory_statistics(self) -> Dict[str, Any]:
        """
        Get agent memory statistics (backward compatibility method).
        
        Returns:
            Dictionary containing memory statistics
        """
        return self.agent_memory_wrapper.get_memory_statistics()
    
    def switch_agent_memory_mode(self, new_mode: str) -> Dict[str, Any]:
        """
        Switch agent memory mode (backward compatibility method).
        
        Args:
            new_mode: New mode to switch to
            
        Returns:
            Dictionary containing the result
        """
        return self.agent_memory_wrapper.switch_mode(new_mode)
    
    def get_agent_memory_mode_info(self) -> Dict[str, Any]:
        """
        Get agent memory mode information (backward compatibility method).
        
        Returns:
            Dictionary containing mode information
        """
        manager_info = self.agent_memory_wrapper.get_manager_info()
        
        return {
            "current_mode": self.config.agent_memory.mode if hasattr(self.config, 'agent_memory') else None,
            "available_modes": self.agent_memory_wrapper.get_available_modes(),
            "manager_type": manager_info.get("manager_type"),
            "enabled": manager_info.get("enabled", False),
        }
    
    # New unified interface methods
    
    def add_memory(
        self,
        content: str,
        agent_id: str,
        context: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Add memory (unified interface method).
        
        Args:
            content: Memory content
            agent_id: ID of the agent/user
            context: Optional context information
            metadata: Optional metadata
            
        Returns:
            Dictionary containing the result
        """
        return self.agent_memory_wrapper.add_memory(
            content=content,
            agent_id=agent_id,
            context=context,
            metadata=metadata
        )
    
    def get_memories(
        self,
        agent_id: str,
        query: Optional[str] = None,
        filters: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """
        Get memories (unified interface method).
        
        Args:
            agent_id: ID of the agent/user
            query: Optional query string
            filters: Optional filters
            
        Returns:
            List of memory dictionaries
        """
        return self.agent_memory_wrapper.get_memories(
            agent_id=agent_id,
            query=query,
            filters=filters
        )
    
    def update_memory(
        self,
        memory_id: str,
        agent_id: str,
        updates: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Update memory (unified interface method).
        
        Args:
            memory_id: ID of the memory
            agent_id: ID of the agent/user
            updates: Updates to apply
            
        Returns:
            Dictionary containing the result
        """
        return self.agent_memory_wrapper.update_memory(
            memory_id=memory_id,
            agent_id=agent_id,
            updates=updates
        )
    
    def delete_memory(
        self,
        memory_id: str,
        agent_id: str
    ) -> Dict[str, Any]:
        """
        Delete memory (unified interface method).
        
        Args:
            memory_id: ID of the memory
            agent_id: ID of the agent/user
            
        Returns:
            Dictionary containing the result
        """
        return self.agent_memory_wrapper.delete_memory(
            memory_id=memory_id,
            agent_id=agent_id
        )
    
    def share_memory(
        self,
        memory_id: str,
        from_agent: str,
        to_agents: List[str],
        permissions: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        Share memory (unified interface method).
        
        Args:
            memory_id: ID of the memory
            from_agent: ID of the agent/user sharing
            to_agents: List of agent/user IDs to share with
            permissions: Optional permissions
            
        Returns:
            Dictionary containing the result
        """
        return self.agent_memory_wrapper.share_memory(
            memory_id=memory_id,
            from_agent=from_agent,
            to_agents=to_agents,
            permissions=permissions
        )
    
    def get_context_info(self, agent_id: str) -> Dict[str, Any]:
        """
        Get context information (unified interface method).
        
        Args:
            agent_id: ID of the agent/user
            
        Returns:
            Dictionary containing context information
        """
        return self.agent_memory_wrapper.get_context_info(agent_id=agent_id)
    
    def update_memory_decay(self) -> Dict[str, Any]:
        """
        Update memory decay (unified interface method).
        
        Returns:
            Dictionary containing the result
        """
        return self.agent_memory_wrapper.update_memory_decay()
    
    def cleanup_forgotten_memories(self) -> Dict[str, Any]:
        """
        Cleanup forgotten memories (unified interface method).
        
        Returns:
            Dictionary containing the result
        """
        return self.agent_memory_wrapper.cleanup_forgotten_memories()
    
    def get_memory_statistics(self) -> Dict[str, Any]:
        """
        Get memory statistics (unified interface method).
        
        Returns:
            Dictionary containing memory statistics
        """
        return self.agent_memory_wrapper.get_memory_statistics()
    
    def check_permission(
        self,
        agent_id: str,
        memory_id: str,
        permission: str
    ) -> bool:
        """
        Check permission (unified interface method).
        
        Args:
            agent_id: ID of the agent/user
            memory_id: ID of the memory
            permission: Permission to check
            
        Returns:
            True if the agent/user has the permission, False otherwise
        """
        return self.agent_memory_wrapper.check_permission(
            agent_id=agent_id,
            memory_id=memory_id,
            permission=permission
        )
    
    def switch_mode(self, new_mode: str) -> Dict[str, Any]:
        """
        Switch mode (unified interface method).
        
        Args:
            new_mode: New mode to switch to
            
        Returns:
            Dictionary containing the result
        """
        return self.agent_memory_wrapper.switch_mode(new_mode)
    
    def get_mode_info(self) -> Dict[str, Any]:
        """
        Get mode information (unified interface method).
        
        Returns:
            Dictionary containing mode information
        """
        return self.agent_memory_wrapper.get_manager_info()
    
    def get_available_modes(self) -> List[str]:
        """
        Get available modes (unified interface method).
        
        Returns:
            List of available modes
        """
        return self.agent_memory_wrapper.get_available_modes()
    
    def validate_configuration(self) -> Dict[str, Any]:
        """
        Validate configuration (unified interface method).
        
        Returns:
            Dictionary containing validation results
        """
        return self.agent_memory_wrapper.validate_configuration()
    
    # Utility methods for migration and compatibility
    
    def migrate_from_legacy(
        self,
        legacy_memories: List[Dict[str, Any]],
        default_agent_id: str = "legacy_agent"
    ) -> Dict[str, Any]:
        """
        Migrate memories from legacy format.
        
        Args:
            legacy_memories: List of legacy memory dictionaries
            default_agent_id: Default agent ID for legacy memories
            
        Returns:
            Dictionary containing migration results
        """
        if not self.is_enabled():
            return {"error": "Agent memory management is not enabled"}
        
        try:
            migrated_count = 0
            failed_count = 0
            
            for legacy_memory in legacy_memories:
                try:
                    # Extract content and metadata
                    content = legacy_memory.get('memory', legacy_memory.get('content', ''))
                    metadata = legacy_memory.get('metadata', {})
                    agent_id = metadata.get('agent_id', default_agent_id)
                    
                    # Add memory using new system
                    result = self.add_memory(
                        content=content,
                        agent_id=agent_id,
                        metadata=metadata
                    )
                    
                    if 'error' not in result:
                        migrated_count += 1
                    else:
                        failed_count += 1
                        
                except Exception as e:
                    logger.error(f"Failed to migrate legacy memory: {e}", exc_info=True)
                    failed_count += 1
            
            return {
                "success": True,
                "migrated_count": migrated_count,
                "failed_count": failed_count,
                "total_count": len(legacy_memories),
            }
            
        except Exception as e:
            logger.error(f"Failed to migrate from legacy: {e}", exc_info=True)
            return {"error": str(e)}
    
    def get_compatibility_info(self) -> Dict[str, Any]:
        """
        Get compatibility information.
        
        Returns:
            Dictionary containing compatibility information
        """
        return {
            "backward_compatible": True,
            "legacy_methods_supported": [
                "add_agent_memory",
                "get_agent_memories",
                "share_agent_memory",
                "get_agent_context",
                "update_agent_memory_decay",
                "cleanup_agent_forgotten_memories",
                "get_agent_memory_statistics",
                "switch_agent_memory_mode",
                "get_agent_memory_mode_info",
            ],
            "unified_methods_available": [
                "add_memory",
                "get_memories",
                "update_memory",
                "delete_memory",
                "share_memory",
                "get_context_info",
                "update_memory_decay",
                "cleanup_forgotten_memories",
                "get_memory_statistics",
                "check_permission",
                "switch_mode",
                "get_mode_info",
            ],
            "migration_tools": [
                "migrate_from_legacy",
                "validate_configuration",
            ],
        }
