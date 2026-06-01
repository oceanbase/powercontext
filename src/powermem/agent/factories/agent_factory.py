"""
Agent factory for creating agent-related components.

This module provides factory classes for creating agent-related components
such as context managers, scope managers, etc.
"""

from typing import Dict, Any, Type
import logging

from powermem.agent.abstract.context import AgentContextManagerBase
from powermem.agent.abstract.scope import AgentScopeManagerBase
from powermem.agent.abstract.permission import AgentPermissionManagerBase
from powermem.agent.abstract.collaboration import AgentCollaborationManagerBase
from powermem.agent.abstract.privacy import AgentPrivacyManagerBase

logger = logging.getLogger(__name__)


class AgentFactory:
    """
    Factory class for creating agent-related components.
    
    This factory provides a unified interface for creating various agent
    components such as context managers, scope managers, etc.
    """
    
    # Registry of available component types
    _COMPONENT_REGISTRY: Dict[str, Dict[str, Type]] = {
        "context": {},
        "scope": {},
        "permission": {},
        "collaboration": {},
        "privacy": {},
    }
    
    @classmethod
    def create_context_manager(
        cls,
        manager_type: str,
        config: Dict[str, Any]
    ) -> AgentContextManagerBase:
        """
        Create a context manager.
        
        Args:
            manager_type: Type of context manager to create
            config: Configuration dictionary
            
        Returns:
            AgentContextManagerBase instance
            
        Raises:
            ValueError: If the manager type is not supported
        """
        if manager_type not in cls._COMPONENT_REGISTRY["context"]:
            raise ValueError(f"Unsupported context manager type: {manager_type}")
        
        manager_class = cls._COMPONENT_REGISTRY["context"][manager_type]
        
        try:
            manager = manager_class(config)
            manager.initialize()
            logger.info(f"Created {manager_type} context manager successfully")
            return manager
        except Exception as e:
            logger.error(f"Failed to create {manager_type} context manager: {e}", exc_info=True)
            raise
    
    @classmethod
    def create_scope_manager(
        cls,
        manager_type: str,
        config: Dict[str, Any]
    ) -> AgentScopeManagerBase:
        """
        Create a scope manager.
        
        Args:
            manager_type: Type of scope manager to create
            config: Configuration dictionary
            
        Returns:
            AgentScopeManagerBase instance
            
        Raises:
            ValueError: If the manager type is not supported
        """
        if manager_type not in cls._COMPONENT_REGISTRY["scope"]:
            raise ValueError(f"Unsupported scope manager type: {manager_type}")
        
        manager_class = cls._COMPONENT_REGISTRY["scope"][manager_type]
        
        try:
            manager = manager_class(config)
            manager.initialize()
            logger.info(f"Created {manager_type} scope manager successfully")
            return manager
        except Exception as e:
            logger.error(f"Failed to create {manager_type} scope manager: {e}", exc_info=True)
            raise
    
    @classmethod
    def create_permission_manager(
        cls,
        manager_type: str,
        config: Dict[str, Any]
    ) -> AgentPermissionManagerBase:
        """
        Create a permission manager.
        
        Args:
            manager_type: Type of permission manager to create
            config: Configuration dictionary
            
        Returns:
            AgentPermissionManagerBase instance
            
        Raises:
            ValueError: If the manager type is not supported
        """
        if manager_type not in cls._COMPONENT_REGISTRY["permission"]:
            raise ValueError(f"Unsupported permission manager type: {manager_type}")
        
        manager_class = cls._COMPONENT_REGISTRY["permission"][manager_type]
        
        try:
            manager = manager_class(config)
            manager.initialize()
            logger.info(f"Created {manager_type} permission manager successfully")
            return manager
        except Exception as e:
            logger.error(f"Failed to create {manager_type} permission manager: {e}", exc_info=True)
            raise
    
    @classmethod
    def create_collaboration_manager(
        cls,
        manager_type: str,
        config: Dict[str, Any]
    ) -> AgentCollaborationManagerBase:
        """
        Create a collaboration manager.
        
        Args:
            manager_type: Type of collaboration manager to create
            config: Configuration dictionary
            
        Returns:
            AgentCollaborationManagerBase instance
            
        Raises:
            ValueError: If the manager type is not supported
        """
        if manager_type not in cls._COMPONENT_REGISTRY["collaboration"]:
            raise ValueError(f"Unsupported collaboration manager type: {manager_type}")
        
        manager_class = cls._COMPONENT_REGISTRY["collaboration"][manager_type]
        
        try:
            manager = manager_class(config)
            manager.initialize()
            logger.info(f"Created {manager_type} collaboration manager successfully")
            return manager
        except Exception as e:
            logger.error(f"Failed to create {manager_type} collaboration manager: {e}", exc_info=True)
            raise
    
    @classmethod
    def create_privacy_manager(
        cls,
        manager_type: str,
        config: Dict[str, Any]
    ) -> AgentPrivacyManagerBase:
        """
        Create a privacy manager.
        
        Args:
            manager_type: Type of privacy manager to create
            config: Configuration dictionary
            
        Returns:
            AgentPrivacyManagerBase instance
            
        Raises:
            ValueError: If the manager type is not supported
        """
        if manager_type not in cls._COMPONENT_REGISTRY["privacy"]:
            raise ValueError(f"Unsupported privacy manager type: {manager_type}")
        
        manager_class = cls._COMPONENT_REGISTRY["privacy"][manager_type]
        
        try:
            manager = manager_class(config)
            manager.initialize()
            logger.info(f"Created {manager_type} privacy manager successfully")
            return manager
        except Exception as e:
            logger.error(f"Failed to create {manager_type} privacy manager: {e}", exc_info=True)
            raise
    
    @classmethod
    def register_component(
        cls,
        component_type: str,
        component_name: str,
        component_class: Type
    ) -> None:
        """
        Register a new component type.
        
        Args:
            component_type: Type of component (context, scope, etc.)
            component_name: Name identifier for the component
            component_class: Class implementing the component interface
        """
        if component_type not in cls._COMPONENT_REGISTRY:
            raise ValueError(f"Unsupported component type: {component_type}")
        
        # Validate that the class implements the correct interface
        base_classes = {
            "context": AgentContextManagerBase,
            "scope": AgentScopeManagerBase,
            "permission": AgentPermissionManagerBase,
            "collaboration": AgentCollaborationManagerBase,
            "privacy": AgentPrivacyManagerBase,
        }
        
        if not issubclass(component_class, base_classes[component_type]):
            raise ValueError(f"Component class must inherit from {base_classes[component_type].__name__}")
        
        cls._COMPONENT_REGISTRY[component_type][component_name] = component_class
        logger.info(f"Registered new {component_type} component: {component_name}")
    
    @classmethod
    def get_available_components(cls, component_type: str) -> Dict[str, Type]:
        """
        Get all available components of a specific type.
        
        Args:
            component_type: Type of component to get
            
        Returns:
            Dictionary mapping component names to their classes
        """
        if component_type not in cls._COMPONENT_REGISTRY:
            raise ValueError(f"Unsupported component type: {component_type}")
        
        return cls._COMPONENT_REGISTRY[component_type].copy()
    
    @classmethod
    def is_component_supported(cls, component_type: str, component_name: str) -> bool:
        """
        Check if a component is supported.
        
        Args:
            component_type: Type of component
            component_name: Name of the component
            
        Returns:
            True if supported, False otherwise
        """
        return (
            component_type in cls._COMPONENT_REGISTRY and
            component_name in cls._COMPONENT_REGISTRY[component_type]
        )
