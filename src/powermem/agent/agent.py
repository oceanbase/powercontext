"""
Unified Agent Memory Interface

This module provides a simplified, unified interface for all agent memory management
scenarios (Multi-Agent, Multi-User, Hybrid) with automatic mode detection and
seamless switching.
"""

import logging
from typing import Any, Dict, List, Optional, Union
from datetime import datetime

from ..agent.types import AccessPermission, MemoryScope, MemoryType, PrivacyLevel, CollaborationLevel


class ConfigObject:
    """Wrapper to provide attribute-like access to configuration dictionaries."""
    
    def __init__(self, data: Dict[str, Any]):
        self._data = data
    
    def __getattr__(self, name: str):
        if name in self._data:
            value = self._data[name]
            if isinstance(value, dict):
                return ConfigObject(value)
            return value
        raise AttributeError(f"'{self.__class__.__name__}' object has no attribute '{name}'")
    
    def to_dict(self):
        """Convert ConfigObject to dictionary (recursively)."""
        if hasattr(self, '_data'):
            result = {}
            for key, value in self._data.items():
                if isinstance(value, ConfigObject):
                    result[key] = value.to_dict()
                elif isinstance(value, dict):
                    result[key] = value
                else:
                    result[key] = value
            return result
        return {}
    
    def __dict__(self):
        """Support dict() conversion."""
        return self.to_dict()
    
    def get(self, key: str, default=None):
        """Dictionary-like get method."""
        return self._data.get(key, default)
    
    def items(self):
        """Dictionary-like items method."""
        return self._data.items()
    
    def keys(self):
        """Dictionary-like keys method."""
        return self._data.keys()
    
    def values(self):
        """Dictionary-like values method."""
        return self._data.values()
    
    def __contains__(self, key):
        """Support 'in' operator."""
        return key in self._data
    
    def __setitem__(self, key, value):
        """Support item assignment."""
        self._data[key] = value
    
    def __getitem__(self, key):
        """Support item access."""
        if key in self._data:
            value = self._data[key]
            if isinstance(value, dict):
                return ConfigObject(value)
            return value
        raise KeyError(key)
    
    def copy(self):
        """Support copy method with deep copy for nested dictionaries."""
        import copy
        return ConfigObject(copy.deepcopy(self._data))

logger = logging.getLogger(__name__)


class AgentMemory:
    """
    Unified Agent Memory Interface
    
    This class provides a simplified interface for all agent memory scenarios:
    - Multi-Agent: Multiple agents with collaboration
    - Multi-User: Single agent with multiple users  
    - Hybrid: Dynamic switching between modes
    
    The interface automatically detects the appropriate mode and provides
    a consistent API regardless of the underlying implementation.
    """
    
    def __init__(self, config: Dict[str, Any], mode: Optional[str] = None):
        """
        Initialize the unified agent memory interface.
        
        Args:
            config: Memory configuration dictionary
            mode: Optional mode override ('multi_agent', 'multi_user', 'hybrid', 'auto')
        """
        self.config = ConfigObject(config)
        self.mode = mode or self._detect_mode(config)
        self._memory = None
        self._agent_manager = None
        self._initialized = False
        
        # Initialize based on detected mode
        self._initialize()
    
    def _detect_mode(self, config: Dict[str, Any]) -> str:
        """Detect the appropriate mode based on configuration."""
        # Check for explicit mode configuration
        if 'agent_memory' in config:
            agent_config = config['agent_memory']
            if 'mode' in agent_config:
                return agent_config['mode']
            
            # Check for specific manager configurations
            if 'multi_agent_config' in agent_config:
                return 'multi_agent'
            elif 'multi_user_config' in agent_config:
                return 'multi_user'
            elif 'hybrid_config' in agent_config:
                return 'hybrid'
        
        # Default to auto mode for intelligent detection
        return 'auto'
    
    def _initialize(self) -> None:
        """Initialize the appropriate memory manager based on mode."""
        try:
            if self.mode == 'multi_agent':
                self._initialize_multi_agent()
            elif self.mode == 'multi_user':
                self._initialize_multi_user()
            elif self.mode == 'hybrid':
                self._initialize_hybrid()
            elif self.mode == 'auto':
                self._initialize_auto()
            else:
                raise ValueError(f"Unknown mode: {self.mode}")
            
            self._initialized = True
            logger.info(f"AgentMemory initialized in {self.mode} mode")
            
        except Exception as e:
            logger.error(f"Failed to initialize AgentMemory: {e}")
            raise
    
    def _initialize_multi_agent(self) -> None:
        """Initialize multi-agent mode."""
        from .implementations.multi_agent import MultiAgentMemoryManager
        
        # Ensure multi-agent config exists
        if 'agent_memory' not in self.config:
            self.config['agent_memory'] = {}
        
        if 'multi_agent_config' not in self.config['agent_memory']:
            self.config['agent_memory']['multi_agent_config'] = self._get_default_multi_agent_config()
        
        # Create the manager with ConfigObject wrapper
        original_config = self.config._data if hasattr(self.config, '_data') else self.config
        config_obj = ConfigObject(original_config)
        self._agent_manager = MultiAgentMemoryManager(config_obj)
        self._agent_manager.initialize()
        
        # Memory instance will be created lazily when needed
    
    def _initialize_multi_user(self) -> None:
        """Initialize multi-user mode."""
        from .implementations.multi_user import MultiUserMemoryManager
        
        # Ensure multi-user config exists
        if 'agent_memory' not in self.config:
            self.config['agent_memory'] = {}
        
        if 'multi_user_config' not in self.config['agent_memory']:
            self.config['agent_memory']['multi_user_config'] = self._get_default_multi_user_config()
        
        # Create the manager with ConfigObject wrapper
        original_config = self.config._data if hasattr(self.config, '_data') else self.config
        config_obj = ConfigObject(original_config)
        self._agent_manager = MultiUserMemoryManager(config_obj)
        self._agent_manager.initialize()
        
        # Memory instance will be created lazily when needed
    
    def _initialize_hybrid(self) -> None:
        """Initialize hybrid mode."""
        from .implementations.hybrid import HybridMemoryManager
        
        # Ensure hybrid config exists
        if 'agent_memory' not in self.config:
            self.config['agent_memory'] = {}
        
        if 'hybrid_config' not in self.config['agent_memory']:
            self.config['agent_memory']['hybrid_config'] = self._get_default_hybrid_config()
        
        # Create the manager with ConfigObject wrapper
        original_config = self.config._data if hasattr(self.config, '_data') else self.config
        config_obj = ConfigObject(original_config)
        self._agent_manager = HybridMemoryManager(config_obj)
        self._agent_manager.initialize()
        
        # Memory instance will be created lazily when needed
    
    def _initialize_auto(self) -> None:
        """Initialize auto mode with intelligent detection."""
        # For now, default to multi-agent mode
        # This can be enhanced with intelligent detection logic
        logger.info("Auto mode detected, defaulting to multi-agent")
        self._initialize_multi_agent()
    
    def _get_default_multi_agent_config(self) -> Dict[str, Any]:
        """Get default multi-agent configuration from config or use AgentMemorySettings defaults."""
        # Try to get from config first
        agent_config = None
        if 'agent_memory' in self.config:
            agent_config = self.config['agent_memory']
            if isinstance(agent_config, ConfigObject):
                agent_config = agent_config._data if hasattr(agent_config, '_data') else agent_config.to_dict()
        
        # Use AgentMemorySettings defaults (aligned with config_loader)
        from powermem.config_loader import AgentMemorySettings
        settings = AgentMemorySettings()
        
        # Extract values from config if available, otherwise use Settings defaults
        enabled = agent_config.get('enabled', settings.enabled) if agent_config else settings.enabled
        default_scope = agent_config.get('default_scope', settings.default_scope) if agent_config else settings.default_scope
        default_privacy_level = agent_config.get('default_privacy_level', settings.default_privacy_level) if agent_config else settings.default_privacy_level
        default_collaboration_level = agent_config.get('default_collaboration_level', settings.default_collaboration_level) if agent_config else settings.default_collaboration_level
        default_access_permission = agent_config.get('default_access_permission', settings.default_access_permission) if agent_config else settings.default_access_permission
        
        return {
            'enabled': enabled,
            'default_scope': default_scope,
            'default_privacy_level': default_privacy_level,
            'default_collaboration_level': default_collaboration_level,
            'default_access_permission': default_access_permission,
            'default_permissions': {
                'owner': ['read', 'write', 'delete', 'admin'],
                'collaborator': ['read', 'write'],
                'viewer': ['read']
            },
            'agent_groups': {},
            'collaboration_settings': {
                'auto_share_threshold': 0.8,
                'default_privacy_level': default_privacy_level.lower()
            },
            'privacy_config': {
                'enable_encryption': False,
                'data_anonymization': True,
                'access_logging': True,
                'retention_policy': '30_days',
                'gdpr_compliance': True,
                'default_privacy_level': default_privacy_level.lower()
            },
            'collaborative_memory_config': {
                'enabled': True,
                'auto_collaboration': True,
                'collaboration_threshold': 0.7,
                'max_collaborators': 5,
                'collaboration_timeout': 3600,
                'default_collaboration_level': default_collaboration_level.lower()
            },
            'default_scope': default_scope.lower()
        }
    
    def _get_default_multi_user_config(self) -> Dict[str, Any]:
        """Get default multi-user configuration from config or use AgentMemorySettings defaults."""
        # Try to get from config first
        agent_config = None
        if 'agent_memory' in self.config:
            agent_config = self.config['agent_memory']
            if isinstance(agent_config, ConfigObject):
                agent_config = agent_config._data if hasattr(agent_config, '_data') else agent_config.to_dict()
        
        # Use AgentMemorySettings defaults (aligned with config_loader)
        from powermem.config_loader import AgentMemorySettings
        settings = AgentMemorySettings()
        
        # Extract values from config if available, otherwise use Settings defaults
        # For multi_user mode, default_scope should be 'USER_GROUP' if not specified
        enabled = agent_config.get('enabled', settings.enabled) if agent_config else settings.enabled
        default_scope = agent_config.get('default_scope', 'USER_GROUP') if agent_config else 'USER_GROUP'
        default_privacy_level = agent_config.get('default_privacy_level', settings.default_privacy_level) if agent_config else settings.default_privacy_level
        default_collaboration_level = agent_config.get('default_collaboration_level', settings.default_collaboration_level) if agent_config else settings.default_collaboration_level
        default_access_permission = agent_config.get('default_access_permission', settings.default_access_permission) if agent_config else settings.default_access_permission
        
        return {
            'enabled': enabled,
            'default_scope': default_scope,
            'default_privacy_level': default_privacy_level,
            'default_collaboration_level': default_collaboration_level,
            'default_access_permission': default_access_permission,
            'user_isolation': True,
            'cross_user_sharing': True,
            'privacy_protection': True,
            'default_permissions': {
                'owner': ['read', 'write', 'delete', 'admin'],
                'collaborator': ['read', 'write'],
                'viewer': ['read']
            },
            'user_context_config': {
                'max_user_memories': 10000,
                'context_retention_days': 30,
                'auto_cleanup': True,
                'context_sharing_enabled': True
            }
        }
    
    def _get_default_hybrid_config(self) -> Dict[str, Any]:
        """Get default hybrid configuration from config or use AgentMemorySettings defaults."""
        # Try to get from config first
        agent_config = None
        if 'agent_memory' in self.config:
            agent_config = self.config['agent_memory']
            if isinstance(agent_config, ConfigObject):
                agent_config = agent_config._data if hasattr(agent_config, '_data') else agent_config.to_dict()
        
        # Use AgentMemorySettings defaults (aligned with config_loader)
        from powermem.config_loader import AgentMemorySettings
        settings = AgentMemorySettings()
        
        # Extract values from config if available, otherwise use Settings defaults
        enabled = agent_config.get('enabled', settings.enabled) if agent_config else settings.enabled
        default_scope = agent_config.get('default_scope', settings.default_scope) if agent_config else settings.default_scope
        default_privacy_level = agent_config.get('default_privacy_level', settings.default_privacy_level) if agent_config else settings.default_privacy_level
        default_collaboration_level = agent_config.get('default_collaboration_level', settings.default_collaboration_level) if agent_config else settings.default_collaboration_level
        default_access_permission = agent_config.get('default_access_permission', settings.default_access_permission) if agent_config else settings.default_access_permission
        
        return {
            'enabled': enabled,
            'default_scope': default_scope,
            'default_privacy_level': default_privacy_level,
            'default_collaboration_level': default_collaboration_level,
            'default_access_permission': default_access_permission,
            'primary_mode': 'multi_agent',
            'fallback_mode': 'multi_user',
            'auto_switch': True,
            'switch_threshold': 0.7,
            'mode_detection': {
                'agent_count_threshold': 2,
                'user_count_threshold': 3,
                'collaboration_threshold': 0.5
            },
            'mode_switching_config': {
                'enabled': True,
                'switch_delay': 1.0,
                'context_window': 10,
                'confidence_threshold': 0.8,
                'fallback_enabled': True
            }
        }
    
    # Unified API Methods
    
    def add(
        self,
        content: str,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        scope: Optional[str] = None,
        share_with: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        Add a new memory.
        
        Args:
            content: The memory content to store
            user_id: Optional user ID
            agent_id: Optional agent ID
            metadata: Optional metadata for the memory
            scope: Optional memory scope
            share_with: Optional list of IDs to share with
            
        Returns:
            Dictionary containing the added memory information
        """
        if not self._initialized:
            raise RuntimeError("AgentMemory not initialized")
        
        try:
            # Use the underlying agent manager for processing
            if hasattr(self._agent_manager, 'process_memory'):
                # Multi-agent or multi-user mode
                context = {
                    'user_id': user_id,
                    'agent_id': agent_id,
                    'scope': scope,
                    'share_with': share_with or []
                }
                
                result = self._agent_manager.process_memory(
                    content=content,
                    agent_id=agent_id or 'default',
                    context=context,
                    metadata=metadata
                )
                
                # Memory is stored by the agent manager, no need for additional storage
                
                return result
            else:
                # Fallback to agent manager
                raise RuntimeError("No agent manager available for fallback")
                
        except Exception as e:
            logger.error(f"Failed to add memory: {e}")
            raise
    
    def search(
        self,
        query: str,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        scope: Optional[str] = None,
        limit: int = 10
    ) -> List[Dict[str, Any]]:
        """
        Search memories.
        
        Args:
            query: Search query string
            user_id: Optional user ID filter
            agent_id: Optional agent ID filter
            scope: Optional scope filter
            limit: Maximum number of results
            
        Returns:
            List of matching memory dictionaries
        """
        if not self._initialized:
            raise RuntimeError("AgentMemory not initialized")
        
        try:
            # Use the underlying agent manager for search
            if hasattr(self._agent_manager, 'get_memories'):
                filters = {}
                if user_id:
                    filters['user_id'] = user_id
                if scope:
                    filters['scope'] = scope
                
                results = self._agent_manager.get_memories(
                    agent_id=agent_id or 'default',
                    query=query,
                    filters=filters
                )
                
                return results[:limit]
            else:
                # Fallback to base memory
                # Use agent manager for search
                if hasattr(self._agent_manager, 'get_memories'):
                    return self._agent_manager.get_memories(
                        agent_id=agent_id or 'default',
                        query=query,
                        filters={'user_id': user_id} if user_id else None
                    )
                else:
                    raise RuntimeError("Search not supported by current manager")
                
        except Exception as e:
            logger.error(f"Failed to search memories: {e}")
            raise
    
    def get_all(
        self,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """
        Get all memories with optional filtering.
        
        Args:
            user_id: Optional user ID filter
            agent_id: Optional agent ID filter
            limit: Maximum number of results
            
        Returns:
            List of all memory dictionaries
        """
        if not self._initialized:
            raise RuntimeError("AgentMemory not initialized")
        
        try:
            # Use the underlying agent manager
            if hasattr(self._agent_manager, 'get_memories'):
                filters = {}
                if user_id:
                    filters['user_id'] = user_id
                
                results = self._agent_manager.get_memories(
                    agent_id=agent_id or 'default',
                    filters=filters
                )
                
                return results[:limit]
            else:
                # Fallback to base memory
                # Use agent manager for get_all
                if hasattr(self._agent_manager, 'get_memories'):
                    return self._agent_manager.get_memories(
                        agent_id=agent_id or 'default',
                        filters={'user_id': user_id} if user_id else None
                    )
                else:
                    raise RuntimeError("Get all not supported by current manager")
                
        except Exception as e:
            logger.error(f"Failed to get all memories: {e}")
            raise
    
    def update(
        self,
        memory_id: str,
        content: str,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Update an existing memory.
        
        Args:
            memory_id: ID of the memory to update
            content: New content for the memory
            user_id: Optional user ID
            agent_id: Optional agent ID
            metadata: Optional new metadata
            
        Returns:
            Dictionary containing the updated memory information
        """
        if not self._initialized:
            raise RuntimeError("AgentMemory not initialized")
        
        try:
            # Use the underlying agent manager
            if hasattr(self._agent_manager, 'update_memory'):
                updates = {'content': content}
                if metadata:
                    updates['metadata'] = metadata
                
                return self._agent_manager.update_memory(
                    memory_id=memory_id,
                    agent_id=agent_id or 'default',
                    updates=updates
                )
            else:
                # Use agent manager for update
                if hasattr(self._agent_manager, 'update_memory'):
                    return self._agent_manager.update_memory(
                        memory_id=memory_id,
                        agent_id=agent_id or 'default',
                        updates=updates
                    )
                else:
                    raise RuntimeError("Update not supported by current manager")
                
        except Exception as e:
            logger.error(f"Failed to update memory {memory_id}: {e}")
            raise
    
    def delete(
        self,
        memory_id: str,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None
    ) -> bool:
        """
        Delete a memory.
        
        Args:
            memory_id: ID of the memory to delete
            user_id: Optional user ID
            agent_id: Optional agent ID
            
        Returns:
            True if successful, False otherwise
        """
        if not self._initialized:
            raise RuntimeError("AgentMemory not initialized")
        
        try:
            # Use the underlying agent manager
            if hasattr(self._agent_manager, 'delete_memory'):
                result = self._agent_manager.delete_memory(
                    memory_id=memory_id,
                    agent_id=agent_id or 'default'
                )
                return result.get('success', False)
            else:
                # Use agent manager for delete
                if hasattr(self._agent_manager, 'delete_memory'):
                    result = self._agent_manager.delete_memory(
                        memory_id=memory_id,
                        agent_id=agent_id or 'default'
                    )
                    return result.get('success', False)
                else:
                    raise RuntimeError("Delete not supported by current manager")
                
        except Exception as e:
            logger.error(f"Failed to delete memory {memory_id}: {e}")
            raise
    
    def delete_all(
        self,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None
    ) -> bool:
        """
        Delete all memories matching the provided identifiers.
        
        Args:
            user_id: Optional user ID filter
            agent_id: Optional agent ID (defaults to 'default' if not provided)
            
        Returns:
            True if all deletions succeeded (or nothing to delete), False otherwise
        """
        if not self._initialized:
            raise RuntimeError("AgentMemory not initialized")
        
        try:
            # Require manager capabilities for listing and deleting
            if not hasattr(self._agent_manager, 'get_memories') or not hasattr(self._agent_manager, 'delete_memory'):
                raise RuntimeError("Delete all not supported by current manager")
            
            filters: Dict[str, Any] = {}
            if user_id:
                filters['user_id'] = user_id
            
            # Determine the agent_id to use for deletion
            # In multi_user mode, user_id should be used as agent_id for permission checks
            # In multi_agent mode, agent_id should be provided explicitly
            deletion_agent_id = agent_id or user_id or 'default'
            
            # Fetch memories to delete
            results = self._agent_manager.get_memories(
                agent_id=deletion_agent_id,
                filters=filters
            )
            
            if not results:
                return True
            
            all_ok = True
            for item in results:
                mem_id = item.get('id') or item.get('memory_id')
                if not mem_id:
                    continue
                resp = self._agent_manager.delete_memory(
                    memory_id=mem_id,
                    agent_id=deletion_agent_id
                )
                ok = bool(resp.get('success', False)) if isinstance(resp, dict) else bool(resp)
                all_ok = all_ok and ok
            return all_ok
        
        except Exception as e:
            logger.error(f"Failed to delete all memories: {e}")
            raise
    
    def reset(self) -> None:
        """
        Reset the memory store by clearing all data.
        
        This method resets the underlying memory storage, including:
        - Vector store collections
        - Database tables
        - Graph store (if enabled)
        
        WARNING: This will delete ALL memories and cannot be undone.
        """
        if not self._initialized:
            raise RuntimeError("AgentMemory not initialized")
        
        try:
            # Try to access the underlying Memory instance through the agent manager
            if hasattr(self._agent_manager, '_memory_instance'):
                self._agent_manager._memory_instance.reset()
                logger.info("Memory store reset completed successfully")
            else:
                # Fallback: try to reset through agent manager if it has a reset method
                if hasattr(self._agent_manager, 'reset'):
                    self._agent_manager.reset()
                    logger.info("Memory store reset completed successfully")
                else:
                    raise RuntimeError("Reset not supported by current manager - no memory instance or reset method available")
        except Exception as e:
            logger.error(f"Failed to reset memory store: {e}")
            raise

    # Agent-specific methods (for multi-agent mode)
    
    def create_agent(self, agent_id: str, agent_name: Optional[str] = None) -> 'AgentMemory':
        """
        Create a new agent (multi-agent mode only).
        
        Args:
            agent_id: Unique identifier for the agent
            agent_name: Optional human-readable name for the agent
            
        Returns:
            AgentMemory instance for the agent
        """
        if self.mode not in ['multi_agent', 'hybrid']:
            raise RuntimeError(f"create_agent() not supported in {self.mode} mode")
        
        # Create a new AgentMemory instance for this specific agent
        agent_config = self.config.copy()
        agent_config['agent_id'] = agent_id
        
        return AgentMemory(config=agent_config, mode=self.mode)
    
    def create_group(self, group_name: str, agent_ids: List[str], permissions: Optional[Dict[str, List[str]]] = None) -> Dict[str, Any]:
        """
        Create an agent group (multi-agent mode only).
        
        Args:
            group_name: Name of the group
            agent_ids: List of agent IDs to include in the group
            permissions: Optional permissions configuration
            
        Returns:
            Dictionary containing group creation result
        """
        if self.mode not in ['multi_agent', 'hybrid']:
            raise RuntimeError(f"create_group() not supported in {self.mode} mode")
        
        if hasattr(self._agent_manager, 'create_group'):
            return self._agent_manager.create_group(group_name, agent_ids, permissions)
        else:
            raise RuntimeError("Group creation not supported by current manager")
    
    def share_memory(self, memory_id: str, from_agent: str, to_agents: List[str], permissions: Optional[List[str]] = None) -> Dict[str, Any]:
        """
        Share a memory between agents (multi-agent mode only).
        
        Args:
            memory_id: ID of the memory to share
            from_agent: ID of the agent sharing the memory
            to_agents: List of agent IDs to share with
            permissions: Optional list of permissions to grant
            
        Returns:
            Dictionary containing sharing result
        """
        if self.mode not in ['multi_agent', 'hybrid']:
            raise RuntimeError(f"share_memory() not supported in {self.mode} mode")
        
        if hasattr(self._agent_manager, 'share_memory'):
            return self._agent_manager.share_memory(memory_id, from_agent, to_agents, permissions)
        else:
            raise RuntimeError("Memory sharing not supported by current manager")
    
    # Statistics and monitoring
    
    def get_statistics(self) -> Dict[str, Any]:
        """
        Get statistics about the memory system.
        
        Returns:
            Dictionary containing memory statistics
        """
        if not self._initialized:
            raise RuntimeError("AgentMemory not initialized")
        
        try:
            if hasattr(self._agent_manager, 'get_memory_statistics'):
                return self._agent_manager.get_memory_statistics()
            else:
                # Fallback to basic statistics
                all_memories = self.get_all()
                return {
                    'total_memories': len(all_memories),
                    'mode': self.mode,
                    'initialized': self._initialized
                }
                
        except Exception as e:
            logger.error(f"Failed to get statistics: {e}")
            raise
    
    def get_mode(self) -> str:
        """Get the current mode."""
        return self.mode
    
    def switch_mode(self, new_mode: str) -> bool:
        """
        Switch to a different mode (hybrid mode only).
        
        Args:
            new_mode: New mode to switch to
            
        Returns:
            True if successful, False otherwise
        """
        if self.mode != 'hybrid':
            raise RuntimeError(f"Mode switching only supported in hybrid mode, current mode: {self.mode}")
        
        if hasattr(self._agent_manager, 'switch_mode'):
            return self._agent_manager.switch_mode(new_mode)
        else:
            raise RuntimeError("Mode switching not supported by current manager")
