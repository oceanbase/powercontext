"""
Agent Memory Wrapper

Provides a unified interface for the new agent memory system,
abstracting away the complexity of different memory managers.
"""

import logging
from typing import Any, Dict, List, Optional
from datetime import datetime

from typing import Any, Dict
from powermem.agent.abstract.manager import AgentMemoryManagerBase
from powermem.agent.factories.memory_factory import MemoryFactory

logger = logging.getLogger(__name__)


class AgentMemoryWrapper:
    """
    Agent memory wrapper that provides a unified interface for the new agent memory system.
    
    This wrapper abstracts away the complexity of different memory managers and provides
    a consistent interface for all agent memory operations.
    """
    
    def __init__(self, config: Dict[str, Any]):
        """
        Initialize the agent memory wrapper.
        
        Args:
            config: Memory configuration object
        """
        self.config = config
        self.agent_memory_manager: Optional[AgentMemoryManagerBase] = None
        self.initialized = False
    
    def initialize(self) -> None:
        """
        Initialize the agent memory wrapper.
        """
        try:
            if not hasattr(self.config, 'agent_memory') or not self.config.agent_memory.enabled:
                logger.info("Agent memory management is not enabled")
                return
            
            # Create memory manager using factory
            self.agent_memory_manager = MemoryFactory.create_from_config(self.config)
            self.initialized = True
            
            logger.info(f"Agent memory wrapper initialized with {self.config.agent_memory.mode} mode")
            
        except Exception as e:
            logger.error(f"Failed to initialize agent memory wrapper: {e}", exc_info=True)
            self.agent_memory_manager = None
            self.initialized = False
    
    def is_enabled(self) -> bool:
        """
        Check if agent memory management is enabled.
        
        Returns:
            True if enabled, False otherwise
        """
        return (
            hasattr(self.config, 'agent_memory') and
            self.config.agent_memory.enabled and
            self.agent_memory_manager is not None
        )
    
    def add_memory(
        self,
        content: str,
        agent_id: str,
        context: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Add a new memory.
        
        Args:
            content: Memory content
            agent_id: ID of the agent/user
            context: Optional context information
            metadata: Optional metadata
            
        Returns:
            Dictionary containing the result
        """
        if not self.is_enabled():
            return {"error": "Agent memory management is not enabled"}
        
        try:
            return self.agent_memory_manager.process_memory(
                content=content,
                agent_id=agent_id,
                context=context,
                metadata=metadata
            )
        except Exception as e:
            logger.error(f"Failed to add memory: {e}", exc_info=True)
            return {"error": str(e)}
    
    def get_memories(
        self,
        agent_id: str,
        query: Optional[str] = None,
        filters: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """
        Get memories for an agent/user.
        
        Args:
            agent_id: ID of the agent/user
            query: Optional query string
            filters: Optional filters
            
        Returns:
            List of memory dictionaries
        """
        if not self.is_enabled():
            return []
        
        try:
            return self.agent_memory_manager.get_memories(
                agent_id=agent_id,
                query=query,
                filters=filters
            )
        except Exception as e:
            logger.error(f"Failed to get memories: {e}", exc_info=True)
            return []
    
    def update_memory(
        self,
        memory_id: str,
        agent_id: str,
        updates: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Update a memory.
        
        Args:
            memory_id: ID of the memory
            agent_id: ID of the agent/user
            updates: Updates to apply
            
        Returns:
            Dictionary containing the result
        """
        if not self.is_enabled():
            return {"error": "Agent memory management is not enabled"}
        
        try:
            return self.agent_memory_manager.update_memory(
                memory_id=memory_id,
                agent_id=agent_id,
                updates=updates
            )
        except Exception as e:
            logger.error(f"Failed to update memory: {e}", exc_info=True)
            return {"error": str(e)}
    
    def delete_memory(
        self,
        memory_id: str,
        agent_id: str
    ) -> Dict[str, Any]:
        """
        Delete a memory.
        
        Args:
            memory_id: ID of the memory
            agent_id: ID of the agent/user
            
        Returns:
            Dictionary containing the result
        """
        if not self.is_enabled():
            return {"error": "Agent memory management is not enabled"}
        
        try:
            return self.agent_memory_manager.delete_memory(
                memory_id=memory_id,
                agent_id=agent_id
            )
        except Exception as e:
            logger.error(f"Failed to delete memory: {e}", exc_info=True)
            return {"error": str(e)}
    
    def share_memory(
        self,
        memory_id: str,
        from_agent: str,
        to_agents: List[str],
        permissions: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        Share a memory with other agents/users.
        
        Args:
            memory_id: ID of the memory
            from_agent: ID of the agent/user sharing
            to_agents: List of agent/user IDs to share with
            permissions: Optional permissions
            
        Returns:
            Dictionary containing the result
        """
        if not self.is_enabled():
            return {"error": "Agent memory management is not enabled"}
        
        try:
            return self.agent_memory_manager.share_memory(
                memory_id=memory_id,
                from_agent=from_agent,
                to_agents=to_agents,
                permissions=permissions
            )
        except Exception as e:
            logger.error(f"Failed to share memory: {e}", exc_info=True)
            return {"error": str(e)}
    
    def get_context_info(self, agent_id: str) -> Dict[str, Any]:
        """
        Get context information for an agent/user.
        
        Args:
            agent_id: ID of the agent/user
            
        Returns:
            Dictionary containing context information
        """
        if not self.is_enabled():
            return {"error": "Agent memory management is not enabled"}
        
        try:
            return self.agent_memory_manager.get_context_info(agent_id=agent_id)
        except Exception as e:
            logger.error(f"Failed to get context info: {e}", exc_info=True)
            return {"error": str(e)}
    
    def update_memory_decay(self) -> Dict[str, Any]:
        """
        Update memory decay.
        
        Returns:
            Dictionary containing the result
        """
        if not self.is_enabled():
            return {"error": "Agent memory management is not enabled"}
        
        try:
            return self.agent_memory_manager.update_memory_decay()
        except Exception as e:
            logger.error(f"Failed to update memory decay: {e}", exc_info=True)
            return {"error": str(e)}
    
    def cleanup_forgotten_memories(self) -> Dict[str, Any]:
        """
        Clean up forgotten memories.
        
        Returns:
            Dictionary containing the result
        """
        if not self.is_enabled():
            return {"error": "Agent memory management is not enabled"}
        
        try:
            return self.agent_memory_manager.cleanup_forgotten_memories()
        except Exception as e:
            logger.error(f"Failed to cleanup forgotten memories: {e}", exc_info=True)
            return {"error": str(e)}
    
    def get_memory_statistics(self) -> Dict[str, Any]:
        """
        Get memory statistics.
        
        Returns:
            Dictionary containing memory statistics
        """
        if not self.is_enabled():
            return {"error": "Agent memory management is not enabled"}
        
        try:
            return self.agent_memory_manager.get_memory_statistics()
        except Exception as e:
            logger.error(f"Failed to get memory statistics: {e}", exc_info=True)
            return {"error": str(e)}
    
    def check_permission(
        self,
        agent_id: str,
        memory_id: str,
        permission: str
    ) -> bool:
        """
        Check if an agent/user has a specific permission.
        
        Args:
            agent_id: ID of the agent/user
            memory_id: ID of the memory
            permission: Permission to check
            
        Returns:
            True if the agent/user has the permission, False otherwise
        """
        if not self.is_enabled():
            return False
        
        try:
            return self.agent_memory_manager.check_permission(
                agent_id=agent_id,
                memory_id=memory_id,
                permission=permission
            )
        except Exception as e:
            logger.error(f"Failed to check permission: {e}", exc_info=True)
            return False
    
    def get_manager_info(self) -> Dict[str, Any]:
        """
        Get information about the current memory manager.
        
        Returns:
            Dictionary containing manager information
        """
        if not self.is_enabled():
            return {
                "enabled": False,
                "manager_type": None,
                "mode": None,
            }
        
        return {
            "enabled": True,
            "manager_type": self.agent_memory_manager.get_manager_type(),
            "mode": self.config.agent_memory.mode,
            "initialized": self.agent_memory_manager.is_initialized(),
        }
    
    def switch_mode(self, new_mode: str) -> Dict[str, Any]:
        """
        Switch to a new memory management mode.
        
        Args:
            new_mode: New mode to switch to
            
        Returns:
            Dictionary containing the result
        """
        if not hasattr(self.config, 'agent_memory'):
            return {"error": "Agent memory configuration not found"}
        
        try:
            # Update configuration
            self.config.agent_memory.mode = new_mode
            
            # Recreate memory manager
            self.agent_memory_manager = MemoryFactory.create_from_config(self.config)
            
            logger.info(f"Switched agent memory mode to {new_mode}")
            
            return {
                "success": True,
                "new_mode": new_mode,
                "manager_type": self.agent_memory_manager.get_manager_type(),
                "timestamp": datetime.now().isoformat(),
            }
            
        except Exception as e:
            logger.error(f"Failed to switch mode: {e}", exc_info=True)
            return {"error": str(e)}
    
    def get_available_modes(self) -> List[str]:
        """
        Get list of available memory management modes.
        
        Returns:
            List of available modes
        """
        return ["multi_agent", "multi_user", "hybrid"]
    
    def validate_configuration(self) -> Dict[str, Any]:
        """
        Validate the current configuration.
        
        Returns:
            Dictionary containing validation results
        """
        try:
            if not hasattr(self.config, 'agent_memory'):
                return {
                    "valid": False,
                    "error": "Agent memory configuration not found",
                }
            
            agent_config = self.config.agent_memory
            
            # Check mode validity
            valid_modes = ["multi_agent", "multi_user", "hybrid"]
            if agent_config.mode not in valid_modes:
                return {
                    "valid": False,
                    "error": f"Invalid mode: {agent_config.mode}",
                }
            
            # Check if manager can be created
            try:
                test_manager = MemoryFactory.create_from_config(self.config)
                return {
                    "valid": True,
                    "mode": agent_config.mode,
                    "enabled": agent_config.enabled,
                    "manager_type": test_manager.get_manager_type(),
                }
            except Exception as e:
                return {
                    "valid": False,
                    "error": f"Failed to create manager: {str(e)}",
                }
            
        except Exception as e:
            return {
                "valid": False,
                "error": str(e),
            }
