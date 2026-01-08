"""
Multi-User Memory Manager Implementation

This module provides the concrete implementation of the multi-user memory manager,
designed for single-agent scenarios with multiple users.
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Union
from powermem.agent.types import (
    CollaborationLevel,
    MemoryType,
    MemoryScope,
    PrivacyLevel,
    AccessPermission
)
from powermem.intelligence.intelligent_memory_manager import IntelligentMemoryManager
from powermem.agent.abstract.manager import AgentMemoryManagerBase

logger = logging.getLogger(__name__)


class MultiUserMemoryManager(AgentMemoryManagerBase):
    """
    Multi-user memory manager implementation.
    
    Designed for single-agent scenarios with multiple users, providing
    user isolation, cross-user sharing, and privacy protection.
    """
    
    def __init__(self, config: Dict[str, Any]):
        """
        Initialize the multi-user memory manager.
        
        Args:
            config: Memory configuration object
        """
        super().__init__(config)
        self.multi_user_config = config.agent_memory.multi_user_config
        
        # Initialize components
        self.intelligent_manager = None
        
        # User-based memory storage
        self.user_memories = {}
        self.shared_memories = {}
        self.user_sessions = {}
        
        # User context tracking
        self.user_contexts = {}
        self.user_preferences = {}
        
        # Cross-user sharing tracking
        self.sharing_relationships = {}
        self.consent_records = {}
    
    def initialize(self) -> None:
        """
        Initialize the memory manager and all components.
        """
        try:
            # Initialize intelligent memory manager
            self.intelligent_manager = IntelligentMemoryManager(self.config)
            
            # Initialize user contexts
            self._initialize_user_contexts()
            
            self.initialized = True
            logger.info("Multi-user memory manager initialized successfully")
            
        except Exception as e:
            logger.error(f"Failed to initialize multi-user memory manager: {e}")
            raise
    
    def _initialize_user_contexts(self) -> None:
        """Initialize user contexts from configuration."""
        # Initialize default user context settings
        default_context = {
            'max_memories': self.multi_user_config.user_context_config.get('max_user_memories', 10000),
            'session_timeout': self.multi_user_config.user_context_config.get('user_session_timeout', 3600),
            'privacy_level': PrivacyLevel.STANDARD,
            'sharing_enabled': self.multi_user_config.cross_user_sharing,
            'created_at': datetime.now().isoformat(),
        }
        
        # Store default context template
        self.default_user_context = default_context
    
    def process_memory(
        self,
        content: str,
        agent_id: str,
        context: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Process and store a new memory.
        
        Args:
            content: The memory content to store
            agent_id: ID of the user creating the memory
            context: Additional context information
            metadata: Additional metadata for the memory
            
        Returns:
            Dictionary containing the processed memory information
        """
        try:
            # Extract user information
            user_id = self._extract_user_id(agent_id, context, metadata)
            
            # Check user isolation
            if self.multi_user_config.user_isolation:
                self._ensure_user_isolation(user_id)
            
            # Add user collaboration context information to metadata
            agent_context_metadata = metadata or {}
            
            # Get user collaboration information
            user_collaboration_info = self._get_user_collaboration_context(user_id, context)
            
            # Get user permission information
            user_permission_info = self._get_user_permission_context(user_id, agent_id)
            
            # Get user sharing information
            user_sharing_info = self._get_user_sharing_context(user_id, context)
            
            # Organize all agent/user-related information under 'agent' key
            agent_info = {
                'agent_id': agent_id,
                'user_id': user_id,
                'mode': 'multi_user',
                'collaboration': user_collaboration_info,
                'permissions': user_permission_info,
                'sharing': user_sharing_info,
            }
            
            agent_context_metadata['agent'] = agent_info
            
            # Process with intelligent memory manager
            enhanced_metadata = self.intelligent_manager.process_metadata(
                content=content,
                metadata=agent_context_metadata,
                context=context or {}
            )
            
            # Determine memory type from enhanced metadata
            memory_type = self._determine_memory_type_from_metadata(enhanced_metadata)
            
            # Determine scope based on sharing settings
            scope = self._determine_user_scope(user_id, context, enhanced_metadata)
            
            # Persist to database first to get Snowflake ID
            # Use temporary memory data for database insertion
            # Extract run_id from metadata
            run_id = enhanced_metadata.get('run_id') if enhanced_metadata else None
            
            temp_memory_data = {
                'content': content,
                'user_id': user_id,
                'agent_id': agent_id,
                'run_id': run_id,
                'scope': scope,
                'memory_type': memory_type,
                'metadata': enhanced_metadata,
            }
            
            # Get Snowflake ID from database
            memory_id = self._persist_memory_to_storage(temp_memory_data)
            if not memory_id:
                raise ValueError("Failed to get memory ID from database")
            
            # Create complete memory data with Snowflake ID from database
            memory_data = {
                'id': memory_id,
                'content': content,  # Keep original content unchanged
                'user_id': user_id,
                'agent_id': agent_id,  # Keep for compatibility
                'scope': scope,
                'memory_type': memory_type,
                'metadata': enhanced_metadata,  # Use enhanced metadata
                'context': context or {},
                'created_at': datetime.now().isoformat(),
                'updated_at': datetime.now().isoformat(),
                'access_count': 0,
                'last_accessed': None,
                'retention_score': enhanced_metadata.get('intelligence', {}).get('current_retention', 1.0),
                'importance_level': enhanced_metadata.get('intelligence', {}).get('importance_score'),
                'privacy_level': self._determine_privacy_level(enhanced_metadata),
                'shared_with': enhanced_metadata.get('share_with', []),
            }
            
            # Store in user-specific storage
            if user_id not in self.user_memories:
                self.user_memories[user_id] = {
                    MemoryType.WORKING: {},
                    MemoryType.SHORT_TERM: {},
                    MemoryType.LONG_TERM: {},
                    MemoryType.SEMANTIC: {},
                    MemoryType.EPISODIC: {},
                    MemoryType.PROCEDURAL: {},
                    MemoryType.PUBLIC_SHARED: {},
                    MemoryType.PRIVATE_AGENT: {},
                    MemoryType.COLLABORATIVE: {},
                    MemoryType.GROUP_CONSENSUS: {},
                }
            
            self.user_memories[user_id][memory_type][memory_id] = memory_data
            
            # Handle cross-user sharing if enabled
            if self.multi_user_config.cross_user_sharing and memory_data['shared_with']:
                self._handle_cross_user_sharing(memory_id, user_id, memory_data['shared_with'])
            
            # Update user context
            self._update_user_context(user_id, memory_data)
            
            # Apply privacy protection
            self._apply_privacy_protection(memory_id, user_id, memory_data)
            
            logger.info(f"Processed memory {memory_id} for user {user_id} with scope {scope}")
            
            return {
                'id': memory_id,
                'memory': content,
                'scope': scope.value,
                'memory_type': memory_type.value,
                'user_id': user_id,
                'agent_id': agent_id,
                'created_at': memory_data['created_at'],
                'updated_at': memory_data['updated_at'],
                'metadata': memory_data['metadata'],
            }
            
        except Exception as e:
            logger.error(f"Failed to process memory for user {agent_id}: {e}")
            raise
    
    def _persist_memory_to_storage(self, memory_data: Dict[str, Any]) -> int:
        """
        Persist memory data to database and vector store.
        
        Args:
            memory_data: Memory data dictionary (minimal data for database insertion)
        
        Returns:
            Snowflake ID (int) from database
        """
        try:
            # Use existing Memory infrastructure
            if not hasattr(self, '_memory_instance'):
                from powermem.core.memory import Memory
                # Convert ConfigObject back to dict for Memory class
                config_dict = self.config._data if hasattr(self.config, '_data') else self.config
                self._memory_instance = Memory(config_dict)
            
            # Use the existing Memory.add() method
            # Get the Snowflake ID returned from database to ensure consistency
            # Use infer=False to use simple mode since intelligent processing is already done at agent layer
            add_result = self._memory_instance.add(
                messages=memory_data['content'],
                user_id=memory_data.get('user_id'),
                agent_id=memory_data.get('agent_id'),
                run_id=memory_data.get('run_id'),
                metadata={
                    'scope': memory_data.get('scope').value if memory_data.get('scope') else None,
                    'memory_type': memory_data.get('memory_type').value if memory_data.get('memory_type') else None,
                    'retention_score': memory_data.get('retention_score'),
                    'importance_level': memory_data.get('importance_level'),
                    'privacy_level': memory_data.get('privacy_level').value if memory_data.get('privacy_level') else None,
                    **memory_data.get('metadata', {})
                },
                infer=False  # Use simple mode to avoid intelligent processing returning empty results
            )
            
            # Get the Snowflake ID from database
            if not add_result:
                logger.error("Memory.add() returned None or empty result")
                raise ValueError("Failed to persist memory to database: Memory.add() returned None")
            
            if 'results' not in add_result:
                logger.error(f"Memory.add() returned unexpected structure: {add_result}")
                raise ValueError(f"Failed to persist memory to database: Missing 'results' key in response. Got keys: {list(add_result.keys())}")
            
            if not add_result['results'] or len(add_result['results']) == 0:
                logger.error(f"Memory.add() returned empty results list: {add_result}")
                raise ValueError("Failed to persist memory to database: Empty results list")
            
            db_memory_id = add_result['results'][0].get('id')
            if db_memory_id:
                logger.info(f"Persisted memory {db_memory_id} to storage")
                return db_memory_id
            else:
                logger.error(f"Memory.add() result missing 'id' field: {add_result['results'][0]}")
                raise ValueError("Failed to get memory ID from database: Missing 'id' in result")
            
        except Exception as e:
            logger.error(f"Failed to persist memory to storage: {e}")
            # Re-raise exception to allow caller to handle it
            raise
    
    def _extract_user_id(
        self,
        agent_id: str,
        context: Optional[Dict[str, Any]],
        metadata: Optional[Dict[str, Any]]
    ) -> str:
        """Extract user ID from various sources."""
        # Priority: metadata > context > agent_id
        if metadata and 'user_id' in metadata:
            return metadata['user_id']
        elif context and 'user_id' in context:
            return context['user_id']
        else:
            return agent_id  # Use agent_id as user_id in multi-user mode
    
    def _ensure_user_isolation(self, user_id: str) -> None:
        """Ensure user isolation is maintained."""
        # Check memory limits
        user_memory_count = self._get_user_memory_count(user_id)
        max_memories = self.multi_user_config.user_context_config.get('max_user_memories', 10000)
        
        if user_memory_count >= max_memories:
            # Clean up old memories if limit exceeded
            self._cleanup_old_user_memories(user_id, user_memory_count - max_memories + 1000)
    
    def _determine_user_scope(
        self,
        user_id: str,
        context: Optional[Dict[str, Any]],
        metadata: Optional[Dict[str, Any]]
    ) -> MemoryScope:
        """Determine the appropriate scope for a user memory."""
        # Check if memory is meant to be shared
        if metadata and metadata.get('share_with'):
            return MemoryScope.USER_GROUP
        elif context and context.get('collaboration_level') == 'high':
            return MemoryScope.USER_GROUP
        else:
            return MemoryScope.PRIVATE
    
    def _determine_memory_type_from_metadata(self, enhanced_metadata: Dict[str, Any]) -> MemoryType:
        """Determine the memory type based on enhanced metadata."""
        intelligence = enhanced_metadata.get('intelligence', {})
        memory_type_str = intelligence.get('memory_type', 'working')
        
        # Map string to enum
        type_mapping = {
            'working': MemoryType.WORKING,
            'short_term': MemoryType.SHORT_TERM,
            'long_term': MemoryType.LONG_TERM,
            'semantic_memory': MemoryType.SEMANTIC,
            'episodic_memory': MemoryType.EPISODIC,
            'procedural_memory': MemoryType.PROCEDURAL,
        }
        
        return type_mapping.get(memory_type_str, MemoryType.WORKING)
    
    def _get_user_collaboration_context(self, user_id: str, context: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """Get user collaboration context information."""
        return {
            'is_collaborating': context.get('collaboration_level', 'low') == 'high' if context else False,
            'collaboration_type': context.get('collaboration_type', 'asynchronous') if context else 'asynchronous',
            'collaboration_status': 'active' if context and context.get('collaboration_level') == 'high' else 'inactive',
            'participants': context.get('share_with', []) if context else [],
            'collaboration_level': context.get('collaboration_level', 'low') if context else 'low'
        }
    
    def _get_user_permission_context(self, user_id: str, agent_id: str) -> Dict[str, Any]:
        """Get user permission context information."""
        return {
            'user_permissions': {
                'read': True,  # User can always read their own memories
                'write': True,  # User can write to their memories
                'delete': True,  # User can delete their own memories
                'admin': True  # User is admin of their own memories
            },
            'agent_permissions': {
                'read': True,  # Agent can read user memories
                'write': True,  # Agent can write to user memories
                'delete': False,  # Agent cannot delete user memories
                'admin': False  # Agent is not admin
            },
            'access_level': 'owner',
            'isolation_enabled': getattr(self.multi_user_config, 'user_isolation', True)
        }
    
    def _get_user_sharing_context(self, user_id: str, context: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """Get user sharing context information."""
        sharing_info = {
            'is_shared': False,
            'shared_with': [],
            'sharing_level': 'private',
            'can_share': getattr(self.multi_user_config, 'sharing_enabled', False)
        }
        
        # Check if memory is being shared
        if context and context.get('share_with'):
            sharing_info.update({
                'is_shared': True,
                'shared_with': context.get('share_with', []),
                'sharing_level': 'collaborative'
            })
        
        # Check if user is in any groups (placeholder - would need to be implemented)
        user_groups = []  # MultiUserMemoryManager doesn't have user_groups yet
        if user_groups:
            sharing_info.update({
                'is_shared': True,
                'shared_with': user_groups,
                'sharing_level': 'group'
            })
        
        return sharing_info
    
    def _determine_privacy_level(self, metadata: Optional[Dict[str, Any]]) -> PrivacyLevel:
        """Determine privacy level for the memory."""
        if metadata and 'privacy_level' in metadata:
            try:
                return PrivacyLevel(metadata['privacy_level'])
            except ValueError:
                pass
        return PrivacyLevel.STANDARD
    
    def _handle_cross_user_sharing(
        self,
        memory_id: str,
        owner_id: str,
        shared_with: List[str]
    ) -> None:
        """Handle cross-user memory sharing."""
        # Check if consent is required
        if self.multi_user_config.user_context_config.get('cross_user_consent_required', True):
            # Record sharing request
            for target_user in shared_with:
                if target_user not in self.consent_records:
                    self.consent_records[target_user] = {}
                
                self.consent_records[target_user][memory_id] = {
                    'owner_id': owner_id,
                    'requested_at': datetime.now().isoformat(),
                    'consent_given': False,
                    'consent_required': True,
                }
        else:
            # Direct sharing without consent
            self._grant_shared_access(memory_id, owner_id, shared_with)
    
    def _grant_shared_access(
        self,
        memory_id: str,
        owner_id: str,
        shared_with: List[str]
    ) -> None:
        """Grant shared access to memory."""
        if memory_id not in self.shared_memories:
            self.shared_memories[memory_id] = {
                'owner_id': owner_id,
                'shared_with': [],
                'permissions': {},
                'shared_at': datetime.now().isoformat(),
            }
        
        for user_id in shared_with:
            if user_id not in self.shared_memories[memory_id]['shared_with']:
                self.shared_memories[memory_id]['shared_with'].append(user_id)
                self.shared_memories[memory_id]['permissions'][user_id] = ['read']
    
    def _update_user_context(self, user_id: str, memory_data: Dict[str, Any]) -> None:
        """Update user context with new memory."""
        if user_id not in self.user_contexts:
            self.user_contexts[user_id] = self.default_user_context.copy()
        
        # Update context
        self.user_contexts[user_id]['last_memory_at'] = datetime.now().isoformat()
        self.user_contexts[user_id]['total_memories'] = self._get_user_memory_count(user_id) + 1
        
        # Update session if applicable
        session_id = memory_data.get('context', {}).get('session_id')
        if session_id:
            if user_id not in self.user_sessions:
                self.user_sessions[user_id] = {}
            
            if session_id not in self.user_sessions[user_id]:
                self.user_sessions[user_id][session_id] = {
                    'started_at': datetime.now().isoformat(),
                    'memories': [],
                }
            
            self.user_sessions[user_id][session_id]['memories'].append(memory_data['id'])
            self.user_sessions[user_id][session_id]['last_activity'] = datetime.now().isoformat()
    
    def _apply_privacy_protection(
        self,
        memory_id: str,
        user_id: str,
        memory_data: Dict[str, Any]
    ) -> None:
        """Apply privacy protection to memory."""
        privacy_level = memory_data.get('privacy_level', PrivacyLevel.STANDARD)
        
        # Apply privacy settings based on level
        if privacy_level == PrivacyLevel.CONFIDENTIAL:
            # Maximum privacy - encrypt content
            memory_data['encrypted'] = True
            memory_data['encryption_key'] = f"user_{user_id}_key"
        elif privacy_level == PrivacyLevel.SENSITIVE:
            # Enhanced privacy - anonymize sensitive data
            memory_data['anonymized'] = True
    
    def get_memories(
        self,
        agent_id: str,
        query: Optional[str] = None,
        filters: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """
        Retrieve memories for a specific user.
        
        Args:
            agent_id: ID of the user
            query: Optional query string for filtering
            filters: Optional additional filters
            
        Returns:
            List of memory dictionaries
        """
        try:
            user_id = self._extract_user_id(agent_id, None, filters)
            accessible_memories = []
            
            # First, try to get memories from database
            # Initialize Memory instance if not exists
            if not hasattr(self, '_memory_instance'):
                from powermem.core.memory import Memory
                config_dict = self.config._data if hasattr(self.config, '_data') else self.config
                self._memory_instance = Memory(config_dict)
            
            # Query memories from database by user_id and agent_id
            db_result = self._memory_instance.get_all(
                user_id=user_id,
                agent_id=agent_id,
                limit=1000  # Get a large number to ensure we get all memories
            )
            
            # Extract results from database response
            db_memories = db_result.get('results', []) if isinstance(db_result, dict) else db_result
            
            # Convert database format to user memory format and load into memory cache
            processed_memory_ids = set()
            
            for db_memory in db_memories:
                memory_id = db_memory.get('id')
                if not memory_id:
                    continue
                
                processed_memory_ids.add(memory_id)
                
                # Convert database format to user memory format
                # Storage adapter.get_all_memories() returns 'memory' field (mapped from payload.data)
                # Keep 'document' as fallback for database raw field name compatibility
                content = db_memory.get('memory') or db_memory.get('document', '')
                
                memory_data = {
                    'id': memory_id,
                    'content': content,
                    'user_id': db_memory.get('user_id', user_id),
                    'agent_id': db_memory.get('agent_id', agent_id),
                    'run_id': db_memory.get('run_id'),
                    'metadata': db_memory.get('metadata', {}),
                    'created_at': db_memory.get('created_at'),
                    'updated_at': db_memory.get('updated_at'),
                    'access_count': 0,
                    'last_accessed': None,
                }
                
                # Extract memory_type from metadata
                metadata = memory_data.get('metadata', {})
                memory_type_str = metadata.get('memory_type') or metadata.get('agent', {}).get('memory_type', 'working')
                
                try:
                    memory_type = MemoryType(memory_type_str) if isinstance(memory_type_str, str) else memory_type_str
                except (ValueError, TypeError):
                    memory_type = MemoryType.WORKING  # Default type
                
                memory_data['memory_type'] = memory_type
                memory_data['scope'] = metadata.get('scope') or 'private'
                
                # Extract additional fields from metadata
                if 'intelligence' in metadata:
                    memory_data['retention_score'] = metadata['intelligence'].get('current_retention', 1.0)
                    memory_data['importance_level'] = metadata['intelligence'].get('importance_score')
                
                # Load into memory cache for future fast access
                if user_id not in self.user_memories:
                    self.user_memories[user_id] = {
                        MemoryType.WORKING: {},
                        MemoryType.SHORT_TERM: {},
                        MemoryType.LONG_TERM: {},
                        MemoryType.SEMANTIC: {},
                        MemoryType.EPISODIC: {},
                        MemoryType.PROCEDURAL: {},
                        MemoryType.PUBLIC_SHARED: {},
                        MemoryType.PRIVATE_AGENT: {},
                        MemoryType.COLLABORATIVE: {},
                        MemoryType.GROUP_CONSENSUS: {},
                    }
                
                if memory_id not in self.user_memories[user_id][memory_type]:
                    self.user_memories[user_id][memory_type][memory_id] = memory_data
                
                # Check if this memory belongs to the user
                if memory_data['user_id'] == user_id:
                    accessible_memories.append(memory_data)
            
            # Also check in-memory cache for any memories not in database (for backward compatibility)
            if user_id in self.user_memories:
                for memory_type in MemoryType:
                    for memory_id, memory_data in self.user_memories[user_id][memory_type].items():
                        # Skip if already processed from database
                        if memory_id in processed_memory_ids:
                            continue
                        
                        # Check if memory belongs to user
                        if memory_data.get('user_id') == user_id:
                            accessible_memories.append(memory_data)
            
            # Get shared memories
            for memory_id, sharing_data in self.shared_memories.items():
                if user_id in sharing_data['shared_with']:
                    # Find the original memory
                    memory_data = self._find_memory(memory_id)
                    if memory_data and memory_data not in accessible_memories:
                        accessible_memories.append(memory_data)
            
            # Apply query filtering if provided
            if query:
                accessible_memories = [
                    memory for memory in accessible_memories
                    if query.lower() in memory.get('content', '').lower()
                ]
            
            # Apply additional filters if provided
            if filters:
                for key, value in filters.items():
                    if key != 'user_id':  # user_id already used for database query
                        accessible_memories = [
                            memory for memory in accessible_memories
                            if memory.get(key) == value
                        ]
            
            # Update access statistics
            for memory in accessible_memories:
                if 'access_count' in memory:
                    memory['access_count'] = memory.get('access_count', 0) + 1
                memory['last_accessed'] = datetime.now().isoformat()
            
            logger.info(f"Retrieved {len(accessible_memories)} memories for user {user_id}")
            return accessible_memories
            
        except Exception as e:
            logger.error(f"Failed to get memories for user {agent_id}: {e}", exc_info=True)
            raise
    
    def update_memory(
        self,
        memory_id: str,
        agent_id: str,
        updates: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Update an existing memory.
        
        Args:
            memory_id: ID of the memory to update
            agent_id: ID of the user making the update
            updates: Dictionary of updates to apply
            
        Returns:
            Dictionary containing the updated memory information
        """
        try:
            # Find the memory
            memory_data = self._find_memory(memory_id)
            if not memory_data:
                raise ValueError(f"Memory {memory_id} not found")
            
            user_id = self._extract_user_id(agent_id, None, None)
            
            # Check if user owns the memory or has write permission
            if memory_data['user_id'] != user_id:
                # Check shared access
                if memory_id not in self.shared_memories:
                    raise PermissionError(f"User {user_id} does not have permission to update memory {memory_id}")
                
                sharing_data = self.shared_memories[memory_id]
                if user_id not in sharing_data['shared_with']:
                    raise PermissionError(f"User {user_id} does not have permission to update memory {memory_id}")
                
                user_permissions = sharing_data['permissions'].get(user_id, [])
                if 'write' not in user_permissions:
                    raise PermissionError(f"User {user_id} does not have write permission for memory {memory_id}")
            
            # Apply updates
            for key, value in updates.items():
                if key in memory_data:
                    memory_data[key] = value
            
            memory_data['updated_at'] = datetime.now().isoformat()
            
            # Update intelligent memory manager if content changed
            if 'content' in updates:
                self.intelligent_manager.update_memory(
                    memory_id=memory_id,
                    content=updates['content'],
                    metadata=memory_data.get('metadata', {})
                )
            
            logger.info(f"Updated memory {memory_id} by user {user_id}")
            
            return {
                'id': memory_id,
                'memory': memory_data['content'],
                'updated_at': memory_data['updated_at'],
                'user_id': user_id,
                'agent_id': agent_id,
            }
            
        except Exception as e:
            logger.error(f"Failed to update memory {memory_id}: {e}")
            raise
    
    def delete_memory(
        self,
        memory_id: str,
        agent_id: str
    ) -> Dict[str, Any]:
        """
        Delete a memory.
        
        Args:
            memory_id: ID of the memory to delete
            agent_id: ID of the user making the deletion
            
        Returns:
            Dictionary containing the deletion result
        """
        try:
            # Find the memory
            memory_data = self._find_memory(memory_id)
            if not memory_data:
                raise ValueError(f"Memory {memory_id} not found")
            
            user_id = self._extract_user_id(agent_id, None, None)
            
            # Check if user owns the memory
            if memory_data['user_id'] != user_id:
                raise PermissionError(f"User {user_id} does not have permission to delete memory {memory_id}")
            
            # Remove from user storage
            user_id = memory_data['user_id']
            memory_type = memory_data['memory_type']
            
            if user_id in self.user_memories and memory_type in self.user_memories[user_id]:
                if memory_id in self.user_memories[user_id][memory_type]:
                    del self.user_memories[user_id][memory_type][memory_id]
            
            # Clean up sharing data
            if memory_id in self.shared_memories:
                del self.shared_memories[memory_id]
            
            # Clean up consent records
            for user_consents in self.consent_records.values():
                if memory_id in user_consents:
                    del user_consents[memory_id]
            
            logger.info(f"Deleted memory {memory_id} by user {user_id}")
            
            return {
                'success': True,
                'deleted_id': memory_id,
                'deleted_by': user_id,
            }
            
        except Exception as e:
            logger.error(f"Failed to delete memory {memory_id}: {e}")
            raise
    
    def share_memory(
        self,
        memory_id: str,
        from_agent: str,
        to_agents: List[str],
        permissions: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        Share a memory with other users.
        
        Args:
            memory_id: ID of the memory to share
            from_agent: ID of the user sharing the memory
            to_agents: List of user IDs to share with
            permissions: Optional list of permissions to grant
            
        Returns:
            Dictionary containing the sharing result
        """
        try:
            # Find the memory
            memory_data = self._find_memory(memory_id)
            if not memory_data:
                raise ValueError(f"Memory {memory_id} not found")
            
            from_user_id = self._extract_user_id(from_agent, None, None)
            
            # Check if user owns the memory
            if memory_data['user_id'] != from_user_id:
                raise PermissionError(f"User {from_user_id} does not own memory {memory_id}")
            
            # Grant shared access
            shared_with = []
            default_permissions = permissions or ['read']
            
            self._grant_shared_access(memory_id, from_user_id, to_agents)
            
            # Update sharing data with permissions
            if memory_id in self.shared_memories:
                for user_id in to_agents:
                    self.shared_memories[memory_id]['permissions'][user_id] = default_permissions
                    shared_with.append(user_id)
            
            logger.info(f"Shared memory {memory_id} from {from_user_id} to {len(shared_with)} users")
            
            return {
                'success': True,
                'memory_id': memory_id,
                'shared_from': from_user_id,
                'shared_with': shared_with,
                'permissions': default_permissions,
            }
            
        except Exception as e:
            logger.error(f"Failed to share memory {memory_id}: {e}")
            raise
    
    def get_context_info(self, agent_id: str) -> Dict[str, Any]:
        """
        Get context information for a user.
        
        Args:
            agent_id: ID of the user
            
        Returns:
            Dictionary containing context information
        """
        try:
            user_id = self._extract_user_id(agent_id, None, None)
            
            context_info = {
                'user_id': user_id,
                'agent_id': agent_id,
                'memory_count': self._get_user_memory_count(user_id),
                'shared_memories_count': self._get_shared_memories_count(user_id),
                'active_sessions': len(self.user_sessions.get(user_id, {})),
                'privacy_level': self.user_contexts.get(user_id, {}).get('privacy_level', PrivacyLevel.STANDARD),
                'sharing_enabled': self.multi_user_config.cross_user_sharing,
                'isolation_enabled': self.multi_user_config.user_isolation,
            }
            
            # Add session information
            if user_id in self.user_sessions:
                context_info['sessions'] = {}
                for session_id, session_data in self.user_sessions[user_id].items():
                    context_info['sessions'][session_id] = {
                        'started_at': session_data['started_at'],
                        'memory_count': len(session_data['memories']),
                        'last_activity': session_data.get('last_activity'),
                    }
            
            return context_info
            
        except Exception as e:
            logger.error(f"Failed to get context info for user {agent_id}: {e}")
            raise
    
    def update_memory_decay(self) -> Dict[str, Any]:
        """
        Update memory decay based on Ebbinghaus forgetting curve.
        
        Returns:
            Dictionary containing the decay update results
        """
        try:
            decay_results = {
                'updated_memories': 0,
                'forgotten_memories': 0,
                'reinforced_memories': 0,
            }
            
            # Update decay for all user memories
            for user_id, user_memory_types in self.user_memories.items():
                for memory_type in MemoryType:
                    for memory_id, memory_data in user_memory_types[memory_type].items():
                        # Update decay using intelligent memory manager
                        # Note: IntelligentMemoryManager.update_memory_decay() updates all memories
                        # We'll call it once and then process individual results
                        if not hasattr(self, '_decay_updated'):
                            self.intelligent_manager.update_memory_decay()
                            self._decay_updated = True
                        
                        # For individual memory processing, we'll use a simplified approach
                        current_score = memory_data.get('retention_score', 1.0)
                        access_count = memory_data.get('access_count', 0)
                        last_accessed = memory_data.get('last_accessed')
                        
                        # Simple decay calculation (this should be replaced with proper Ebbinghaus algorithm)
                        decay_rate = 0.1
                        if last_accessed:
                            # Parse ISO format string to datetime
                            if isinstance(last_accessed, str):
                                last_accessed_dt = datetime.fromisoformat(last_accessed.replace('Z', '+00:00'))
                            else:
                                last_accessed_dt = last_accessed
                            time_since_access = (datetime.now() - last_accessed_dt).total_seconds() / 3600
                        else:
                            time_since_access = 24
                        new_score = current_score * (1 - decay_rate * time_since_access / 24)
                        new_score = max(0.0, min(1.0, new_score))
                        
                        decay_result = {
                            'new_score': new_score,
                            'decay_rate': decay_rate,
                            'forgotten': new_score < 0.1
                        }
                        
                        # Update memory data
                        memory_data['retention_score'] = decay_result.get('new_score', memory_data.get('retention_score', 1.0))
                        memory_data['decay_rate'] = decay_result.get('decay_rate', 0.1)
                        
                        decay_results['updated_memories'] += 1
                        
                        # Check if memory should be forgotten
                        if decay_result.get('forgotten', False):
                            decay_results['forgotten_memories'] += 1
                        
                        # Check if memory should be reinforced
                        if decay_result.get('reinforced', False):
                            decay_results['reinforced_memories'] += 1
            
            logger.info(f"Updated memory decay: {decay_results}")
            return decay_results
            
        except Exception as e:
            logger.error(f"Failed to update memory decay: {e}")
            raise
    
    def cleanup_forgotten_memories(self) -> Dict[str, Any]:
        """
        Clean up memories that have been forgotten.
        
        Returns:
            Dictionary containing the cleanup results
        """
        try:
            cleanup_results = {
                'cleaned_memories': 0,
                'archived_memories': 0,
                'deleted_memories': 0,
            }
            
            # Clean up forgotten memories for all users
            for user_id, user_memory_types in self.user_memories.items():
                for memory_type in MemoryType:
                    memories_to_remove = []
                    
                    for memory_id, memory_data in user_memory_types[memory_type].items():
                        retention_score = memory_data.get('retention_score', 1.0)
                        
                        # Check if memory should be cleaned up
                        if retention_score < 0.1:  # Forgotten threshold
                            memories_to_remove.append(memory_id)
                            cleanup_results['deleted_memories'] += 1
                        elif retention_score < 0.3:  # Archive threshold
                            # Archive memory instead of deleting
                            memory_data['archived'] = True
                            cleanup_results['archived_memories'] += 1
                    
                    # Remove forgotten memories
                    for memory_id in memories_to_remove:
                        del user_memory_types[memory_type][memory_id]
                        cleanup_results['cleaned_memories'] += 1
            
            logger.info(f"Cleaned up forgotten memories: {cleanup_results}")
            return cleanup_results
            
        except Exception as e:
            logger.error(f"Failed to cleanup forgotten memories: {e}")
            raise
    
    def get_memory_statistics(self) -> Dict[str, Any]:
        """
        Get statistics about the memory system.
        
        Returns:
            Dictionary containing memory statistics
        """
        try:
            stats = {
                'total_memories': 0,
                'total_users': len(self.user_memories),
                'user_breakdown': {},
                'type_breakdown': {},
                'sharing_stats': {
                    'shared_memories': len(self.shared_memories),
                    'sharing_relationships': len(self.sharing_relationships),
                    'consent_requests': sum(len(consents) for consents in self.consent_records.values()),
                },
                'session_stats': {
                    'total_sessions': sum(len(sessions) for sessions in self.user_sessions.values()),
                    'active_users': len(self.user_sessions),
                },
            }
            
            # Count memories by user and type
            for user_id, user_memory_types in self.user_memories.items():
                user_count = 0
                for memory_type in MemoryType:
                    type_count = len(user_memory_types[memory_type])
                    user_count += type_count
                    stats['total_memories'] += type_count
                    
                    # Type breakdown
                    type_key = memory_type.value
                    if type_key not in stats['type_breakdown']:
                        stats['type_breakdown'][type_key] = 0
                    stats['type_breakdown'][type_key] += type_count
                
                stats['user_breakdown'][user_id] = user_count
            
            return stats
            
        except Exception as e:
            logger.error(f"Failed to get memory statistics: {e}")
            raise
    
    def check_permission(
        self,
        agent_id: str,
        memory_id: str,
        permission: str
    ) -> bool:
        """
        Check if a user has a specific permission for a memory.
        
        Args:
            agent_id: ID of the user
            memory_id: ID of the memory
            permission: Permission to check
            
        Returns:
            True if the user has the permission, False otherwise
        """
        try:
            user_id = self._extract_user_id(agent_id, None, None)
            
            # Find the memory
            memory_data = self._find_memory(memory_id)
            if not memory_data:
                return False
            
            # Check if user owns the memory
            if memory_data['user_id'] == user_id:
                return True
            
            # Check shared access
            if memory_id in self.shared_memories:
                sharing_data = self.shared_memories[memory_id]
                if user_id in sharing_data['shared_with']:
                    user_permissions = sharing_data['permissions'].get(user_id, [])
                    return permission.lower() in user_permissions
            
            return False
            
        except Exception:
            return False
    
    def _get_user_memory_count(self, user_id: str) -> int:
        """Get the total number of memories for a user."""
        if user_id not in self.user_memories:
            return 0
        
        count = 0
        for memory_type in MemoryType:
            count += len(self.user_memories[user_id][memory_type])
        return count
    
    def _get_shared_memories_count(self, user_id: str) -> int:
        """Get the number of memories shared with a user."""
        count = 0
        for sharing_data in self.shared_memories.values():
            if user_id in sharing_data['shared_with']:
                count += 1
        return count
    
    def _cleanup_old_user_memories(self, user_id: str, count_to_remove: int) -> None:
        """Clean up old memories for a user."""
        # Simple implementation: remove oldest memories
        all_memories = []
        
        if user_id in self.user_memories:
            for memory_type in MemoryType:
                for memory_id, memory_data in self.user_memories[user_id][memory_type].items():
                    all_memories.append((memory_id, memory_type, memory_data))
        
        # Sort by creation date
        all_memories.sort(key=lambda x: x[2].get('created_at', ''))
        
        # Remove oldest memories
        for i in range(min(count_to_remove, len(all_memories))):
            memory_id, memory_type, _ = all_memories[i]
            del self.user_memories[user_id][memory_type][memory_id]
    
    def _find_memory(self, memory_id: str) -> Optional[Dict[str, Any]]:
        """Find a memory by ID across all users and types."""
        for user_id, user_memory_types in self.user_memories.items():
            for memory_type in MemoryType:
                if memory_id in user_memory_types[memory_type]:
                    return user_memory_types[memory_type][memory_id]
        return None
