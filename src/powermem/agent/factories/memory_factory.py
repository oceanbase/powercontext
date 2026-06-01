"""
Memory factory for creating different types of agent memory managers.

This module provides the main factory class for creating different types
of agent memory managers based on configuration.
"""

from typing import Dict
import logging

from typing import Any, Dict
from powermem.agent.abstract.manager import AgentMemoryManagerBase
from powermem.agent.implementations.multi_agent import MultiAgentMemoryManager
from powermem.agent.implementations.multi_user import MultiUserMemoryManager
from powermem.agent.implementations.hybrid import HybridMemoryManager
from powermem.configs import MemoryConfig

logger = logging.getLogger(__name__)


class MemoryFactory:
    """
    Factory class for creating different types of agent memory managers.
    
    This factory provides a unified interface for creating memory managers
    based on configuration, supporting multi-agent, multi-user, and hybrid modes.
    """
    
    # Registry of available memory manager types
    _MANAGER_REGISTRY: Dict[str, type] = {
        "multi_agent": MultiAgentMemoryManager,
        "multi_user": MultiUserMemoryManager,
        "hybrid": HybridMemoryManager,
    }
    
    @classmethod
    def create_from_config(cls, config: Dict[str, Any]) -> AgentMemoryManagerBase:
        """
        Create a memory manager from configuration.
        
        Args:
            config: Memory configuration object
            
        Returns:
            AgentMemoryManagerBase instance
            
        Raises:
            ValueError: If the manager type is not supported
        """
        if not hasattr(config, 'agent_memory') or not config.agent_memory.enabled:
            raise ValueError("Agent memory management is not enabled in configuration")
        
        manager_type = config.agent_memory.mode
        
        if manager_type not in cls._MANAGER_REGISTRY:
            raise ValueError(f"Unsupported manager type: {manager_type}")
        
        manager_class = cls._MANAGER_REGISTRY[manager_type]
        
        try:
            manager = manager_class(config)
            manager.initialize()
            logger.info(f"Created {manager_type} memory manager successfully")
            return manager
        except Exception as e:
            logger.error(f"Failed to create {manager_type} memory manager: {e}", exc_info=True)
            raise
    
    @classmethod
    def create_manager(
        cls,
        manager_type: str,
        config: Dict[str, Any]
    ) -> AgentMemoryManagerBase:
        """
        Create a specific type of memory manager.
        
        Args:
            manager_type: Type of manager to create
            config: Memory configuration object
            
        Returns:
            AgentMemoryManagerBase instance
            
        Raises:
            ValueError: If the manager type is not supported
        """
        if manager_type not in cls._MANAGER_REGISTRY:
            raise ValueError(f"Unsupported manager type: {manager_type}")
        
        manager_class = cls._MANAGER_REGISTRY[manager_type]
        
        try:
            manager = manager_class(config)
            manager.initialize()
            logger.info(f"Created {manager_type} memory manager successfully")
            return manager
        except Exception as e:
            logger.error(f"Failed to create {manager_type} memory manager: {e}", exc_info=True)
            raise
    
    @classmethod
    def register_manager(
        cls,
        manager_type: str,
        manager_class: type
    ) -> None:
        """
        Register a new memory manager type.
        
        Args:
            manager_type: Type identifier for the manager
            manager_class: Class implementing AgentMemoryManagerBase
        """
        if not issubclass(manager_class, AgentMemoryManagerBase):
            raise ValueError("Manager class must inherit from AgentMemoryManagerBase")
        
        cls._MANAGER_REGISTRY[manager_type] = manager_class
        logger.info(f"Registered new memory manager type: {manager_type}")
    
    @classmethod
    def get_available_managers(cls) -> Dict[str, type]:
        """
        Get all available memory manager types.
        
        Returns:
            Dictionary mapping manager types to their classes
        """
        return cls._MANAGER_REGISTRY.copy()
    
    @classmethod
    def is_manager_type_supported(cls, manager_type: str) -> bool:
        """
        Check if a manager type is supported.
        
        Args:
            manager_type: Type identifier to check
            
        Returns:
            True if supported, False otherwise
        """
        return manager_type in cls._MANAGER_REGISTRY
    
    @classmethod
    def create_hybrid_manager(
        cls,
        config: MemoryConfig,
        primary_mode: str = "multi_user",
        fallback_mode: str = "multi_agent"
    ) -> AgentMemoryManagerBase:
        """
        Create a hybrid memory manager with specific modes.
        
        Args:
            config: Memory configuration object
            primary_mode: Primary management mode
            fallback_mode: Fallback management mode
            
        Returns:
            HybridMemoryManager instance
        """
        # Ensure hybrid config is set
        if not hasattr(config, 'agent_memory'):
            raise ValueError("Agent memory configuration not found")
        
        config.agent_memory.mode = "hybrid"
        config.agent_memory.hybrid_config.enabled = True
        config.agent_memory.hybrid_config.primary_mode = primary_mode
        config.agent_memory.hybrid_config.fallback_mode = fallback_mode
        
        return cls.create_from_config(config)
    
    @classmethod
    def create_multi_agent_manager(
        cls,
        config: MemoryConfig,
        enable_collaboration: bool = True,
        default_scope: str = "private"
    ) -> AgentMemoryManagerBase:
        """
        Create a multi-agent memory manager with specific settings.
        
        Args:
            config: Memory configuration object
            enable_collaboration: Whether to enable collaboration
            default_scope: Default memory scope
            
        Returns:
            MultiAgentMemoryManager instance
        """
        # Ensure multi-agent config is set
        if not hasattr(config, 'agent_memory'):
            raise ValueError("Agent memory configuration not found")
        
        config.agent_memory.mode = "multi_agent"
        config.agent_memory.multi_agent_config.enabled = True
        config.agent_memory.multi_agent_config.default_collaboration_level = (
            "collaborative" if enable_collaboration else "isolated"
        )
        
        return cls.create_from_config(config)
    
    @classmethod
    def create_multi_user_manager(
        cls,
        config: MemoryConfig,
        user_isolation: bool = True,
        cross_user_sharing: bool = False
    ) -> AgentMemoryManagerBase:
        """
        Create a multi-user memory manager with specific settings.
        
        Args:
            config: Memory configuration object
            user_isolation: Whether to enable user isolation
            cross_user_sharing: Whether to allow cross-user sharing
            
        Returns:
            MultiUserMemoryManager instance
        """
        # Ensure multi-user config is set
        if not hasattr(config, 'agent_memory'):
            raise ValueError("Agent memory configuration not found")
        
        config.agent_memory.mode = "multi_user"
        config.agent_memory.multi_user_config.user_isolation = user_isolation
        config.agent_memory.multi_user_config.cross_user_sharing = cross_user_sharing
        
        return cls.create_from_config(config)
