"""
Multi-Agent Memory Manager Implementation

This module provides the concrete implementation of the multi-agent memory manager,
refactored to conform to the new AgentMemoryManagerBase interface.
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Union
from powermem.agent.types import (
    AccessPermission,
    MemoryScope,
    MemoryType,
    PrivacyLevel,
    CollaborationLevel,
)
from powermem.intelligence.intelligent_memory_manager import IntelligentMemoryManager
from powermem.agent.abstract.manager import AgentMemoryManagerBase
from powermem.agent.components.scope_controller import ScopeController
from powermem.agent.components.permission_controller import PermissionController
from powermem.agent.components.collaboration_coordinator import CollaborationCoordinator
from powermem.agent.components.privacy_protector import PrivacyProtector

logger = logging.getLogger(__name__)


class MultiAgentMemoryManager(AgentMemoryManagerBase):
    """
    Multi-agent memory manager implementation.
    
    Coordinates intelligent memory management with multi-agent capabilities,
    handles scope-based memory storage, permissions, and collaboration.
    """
    
    def __init__(self, config: Dict[str, Any]):
        """
        Initialize the multi-agent memory manager.
        
        Args:
            config: Memory configuration object
        """
        super().__init__(config)
        self.multi_agent_config = config.agent_memory.multi_agent_config
        
        # Initialize components
        self.intelligent_manager = None
        self.scope_controller = None
        self.permission_controller = None
        self.collaboration_coordinator = None
        self.privacy_protector = None
        
        # Scope-based memory storage
        self.scope_memories = {
            MemoryScope.PRIVATE: {
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
            },
            MemoryScope.AGENT_GROUP: {
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
            },
            MemoryScope.USER_GROUP: {
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
            },
            MemoryScope.PUBLIC: {
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
            },
            MemoryScope.RESTRICTED: {
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
            },
        }
        
        # Agent group management
        self.agent_groups = {}
        self.agent_memberships = {}
        
        # Collaboration tracking
        self.active_collaborations = {}
        self.collaboration_memories = {}
    
    def initialize(self) -> None:
        """
        Initialize the memory manager and all components.
        """
        try:
            # Initialize intelligent memory manager
            self.intelligent_manager = IntelligentMemoryManager(self.config)
            
            # Initialize components
            self.scope_controller = ScopeController(self.config)
            self.permission_controller = PermissionController(self.config)
            self.collaboration_coordinator = CollaborationCoordinator(self.config)
            self.privacy_protector = PrivacyProtector(self.config)
            
            # Initialize agent groups from config
            self._initialize_agent_groups()
            
            self.initialized = True
            logger.info("Multi-agent memory manager initialized successfully")
            
        except Exception as e:
            logger.error(f"Failed to initialize multi-agent memory manager: {e}")
            raise
    
    def _initialize_agent_groups(self) -> None:
        """Initialize agent groups from configuration."""
        if hasattr(self.multi_agent_config, 'agent_groups'):
            for group_name, group_config in self.multi_agent_config.agent_groups.items():
                self.agent_groups[group_name] = {
                    'members': group_config.get('members', []),
                    'permissions': group_config.get('permissions', {}),
                    'created_at': datetime.now().isoformat(),
                }
                
                # Update agent memberships
                for agent_id in group_config.get('members', []):
                    if agent_id not in self.agent_memberships:
                        self.agent_memberships[agent_id] = []
                    self.agent_memberships[agent_id].append(group_name)
    
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
            agent_id: ID of the agent creating the memory
            context: Additional context information
            metadata: Additional metadata for the memory
            
        Returns:
            Dictionary containing the processed memory information
        """
        try:
            # Determine memory scope
            scope = self.scope_controller.determine_scope(agent_id, context, metadata)
            
            # Add agent collaboration context information to metadata
            agent_context_metadata = metadata or {}
            
            # Get collaboration information
            collaboration_info = self._get_collaboration_context(agent_id, context)
            
            # Get permission information
            permission_info = self._get_permission_context(agent_id, scope)
            
            # Get sharing information
            sharing_info = self._get_sharing_context(agent_id, context)
            
            # Organize all agent-related information under 'agent' key
            agent_info = {
                'agent_id': agent_id,
                'mode': 'multi_agent',
                'scope': scope.value if hasattr(scope, 'value') else str(scope),
                'collaboration': collaboration_info,
                'permissions': permission_info,
                'sharing': sharing_info,
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
            
            # Persist to database first to get Snowflake ID
            # Use temporary memory data for database insertion
            temp_memory_data = {
                'content': content,
                'agent_id': agent_id,
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
                'agent_id': agent_id,
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
            }
            
            # Store in appropriate scope and type
            self.scope_memories[scope][memory_type][memory_id] = memory_data
            
            # Also store in scope controller's storage for access control
            if self.scope_controller:
                self.scope_controller.scope_storage[scope][memory_type][memory_id] = memory_data
            
            # Set up permissions - grant owner permissions to the memory creator
            owner_permissions = self.multi_agent_config.default_permissions.get("owner", [])
            # Convert string permissions to AccessPermission enum
            owner_permissions_enum = []
            for perm in owner_permissions:
                try:
                    # Handle both string and AccessPermission enum inputs
                    if isinstance(perm, AccessPermission):
                        owner_permissions_enum.append(perm)
                    else:
                        # Convert to AccessPermission enum (case insensitive)
                        owner_permissions_enum.append(AccessPermission(perm.lower()))
                except ValueError:
                    logger.warning(f"Invalid permission: {perm}")
            
            # Grant owner permissions to the memory creator
            for permission in owner_permissions_enum:
                self.permission_controller.grant_permission(
                    memory_id=memory_id,
                    agent_id=agent_id,
                    permission=permission,
                    granted_by=agent_id
                )
            
            # Handle collaboration if applicable
            if context and context.get('collaboration_level') == 'high':
                self._handle_collaborative_memory(memory_id, agent_id, context)
            
            # Apply privacy protection
            self.privacy_protector.set_privacy_level(
                memory_id=memory_id,
                privacy_level=PrivacyLevel.STANDARD,
                set_by=agent_id
            )
            
            logger.info(f"Processed memory {memory_id} for agent {agent_id} with scope {scope}")
            
            return {
                'id': memory_id,
                'memory': content,
                'scope': scope.value,
                'memory_type': memory_type.value,
                'agent_id': agent_id,
                'created_at': memory_data['created_at'],
                'updated_at': memory_data['updated_at'],
                'metadata': memory_data['metadata'],
            }
            
        except Exception as e:
            logger.error(f"Failed to process memory for agent {agent_id}: {e}")
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
                if hasattr(self.config, '_data'):
                    config_dict = self.config._data
                elif hasattr(self.config, 'to_dict'):
                    config_dict = self.config.to_dict()
                else:
                    config_dict = self.config
                
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
                    **memory_data.get('metadata', {})
                },
                infer=False  # Use simple mode to avoid intelligent processing returning empty results
            )
            
            # Get the Snowflake ID from database
            if add_result and 'results' in add_result and len(add_result['results']) > 0:
                db_memory_id = add_result['results'][0].get('id')
                if db_memory_id:
                    logger.info(f"Persisted memory {db_memory_id} to storage")
                    return db_memory_id
                else:
                    raise ValueError("Failed to get memory ID from database")
            else:
                raise ValueError("Failed to persist memory to database")
            
        except Exception as e:
            logger.error(f"Failed to persist memory to storage: {e}")
            # Re-raise exception to allow caller to handle it
            raise
    
    def _get_collaboration_context(self, agent_id: str, context: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """Get collaboration context information."""
        collaboration_info = {
            'is_collaborating': False,
            'collaboration_type': None,
            'collaboration_status': None,
            'participants': [],
            'collaboration_level': context.get('collaboration_level', 'low') if context else 'low'
        }
        
        # Check if agent is in active collaboration
        for collaboration_id, collaboration_data in self.active_collaborations.items():
            if agent_id in collaboration_data.get('participants', []):
                collaboration_info.update({
                    'is_collaborating': True,
                    'collaboration_type': collaboration_data.get('type', 'asynchronous'),
                    'collaboration_status': collaboration_data.get('status', 'active'),
                    'participants': collaboration_data.get('participants', []),
                })
                break
        
        return collaboration_info
    
    def _get_permission_context(self, agent_id: str, scope: MemoryScope) -> Dict[str, Any]:
        """Get permission context information."""
        return {
            'scope_permissions': {
                'read': True,  # Agent can always read their own memories
                'write': True,  # Agent can always write to their scope
                'delete': True,  # Agent can delete their own memories
                'admin': scope in [MemoryScope.PUBLIC, MemoryScope.AGENT_GROUP]  # Admin for public/group scopes
            },
            'scope_type': scope.value if hasattr(scope, 'value') else str(scope),
            'access_level': 'owner' if scope == MemoryScope.PRIVATE else 'member'
        }
    
    def _get_sharing_context(self, agent_id: str, context: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """Get sharing context information."""
        sharing_info = {
            'is_shared': False,
            'shared_with': [],
            'sharing_level': 'private',
            'can_share': True
        }
        
        # Check if memory is being shared
        if context and context.get('share_with'):
            sharing_info.update({
                'is_shared': True,
                'shared_with': context.get('share_with', []),
                'sharing_level': 'collaborative'
            })
        
        # Check if agent is in any groups
        agent_groups = [group for group, members in self.agent_groups.items() if agent_id in members]
        if agent_groups:
            sharing_info.update({
                'is_shared': True,
                'shared_with': agent_groups,
                'sharing_level': 'group'
            })
        
        return sharing_info
    
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
    
    def _handle_collaborative_memory(
        self,
        memory_id: str,
        agent_id: str,
        context: Dict[str, Any]
    ) -> None:
        """Handle collaborative memory creation."""
        participants = context.get('participants', [])
        if participants:
            collaboration_id = self.collaboration_coordinator.initiate_collaboration(
                initiator_id=agent_id,
                participant_ids=participants,
                collaboration_type=CollaborationLevel.COLLABORATIVE,
                context=context
            )
            
            if collaboration_id:
                self.collaboration_memories[memory_id] = collaboration_id
    
    def get_memories(
        self,
        agent_id: str,
        query: Optional[str] = None,
        filters: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """
        Retrieve memories for a specific agent.
        
        Args:
            agent_id: ID of the agent
            query: Optional query string for filtering
            filters: Optional additional filters
            
        Returns:
            List of memory dictionaries
        """
        try:
            # First, try to get memories from database
            # Initialize Memory instance if not exists
            if not hasattr(self, '_memory_instance'):
                from powermem.core.memory import Memory
                if hasattr(self.config, '_data'):
                    config_dict = self.config._data
                elif hasattr(self.config, 'to_dict'):
                    config_dict = self.config.to_dict()
                else:
                    config_dict = self.config
                self._memory_instance = Memory(config_dict)
            
            # Query memories from database by agent_id
            user_id = filters.get('user_id') if filters else None
            db_result = self._memory_instance.get_all(
                user_id=user_id,
                agent_id=agent_id,
                limit=1000  # Get a large number to ensure we get all memories
            )
            
            # Extract results from database response
            db_memories = db_result.get('results', []) if isinstance(db_result, dict) else db_result
            
            # Convert database format to agent memory format and load into memory cache
            accessible_memories = []
            total_memories = len(db_memories)
            scope_access_passed = 0
            permission_passed = 0
            
            for db_memory in db_memories:
                memory_id = db_memory.get('id')
                if not memory_id:
                    continue
                
                # Convert database format to agent memory format
                # Storage adapter.get_all_memories() returns 'memory' field (mapped from payload.data)
                # Keep 'document' as fallback for database raw field name compatibility
                content = db_memory.get('memory') or db_memory.get('document', '')
                
                memory_data = {
                    'id': memory_id,
                    'content': content,
                    'agent_id': db_memory.get('agent_id', agent_id),
                    'user_id': db_memory.get('user_id'),
                    'run_id': db_memory.get('run_id'),
                    'metadata': db_memory.get('metadata', {}),
                    'created_at': db_memory.get('created_at'),
                    'updated_at': db_memory.get('updated_at'),
                    'access_count': 0,
                    'last_accessed': None,
                }
                
                # Extract scope and memory_type from metadata
                metadata = memory_data.get('metadata', {})
                scope_str = metadata.get('scope') or metadata.get('agent', {}).get('scope', 'agent')
                memory_type_str = metadata.get('memory_type') or metadata.get('agent', {}).get('memory_type', 'working')
                
                try:
                    scope = MemoryScope(scope_str) if isinstance(scope_str, str) else scope_str
                except (ValueError, TypeError):
                    scope = MemoryScope.AGENT  # Default scope
                
                try:
                    memory_type = MemoryType(memory_type_str) if isinstance(memory_type_str, str) else memory_type_str
                except (ValueError, TypeError):
                    memory_type = MemoryType.WORKING  # Default type
                
                memory_data['scope'] = scope
                memory_data['memory_type'] = memory_type
                
                # Load into memory cache for future fast access
                if memory_id not in self.scope_memories[scope][memory_type]:
                    self.scope_memories[scope][memory_type][memory_id] = memory_data
                
                # Also load into scope controller's storage for access control
                if self.scope_controller and memory_id not in self.scope_controller.scope_storage[scope][memory_type]:
                    self.scope_controller.scope_storage[scope][memory_type][memory_id] = memory_data
                
                # Restore permissions from metadata if available
                # Check if this memory was created by the current agent
                memory_agent_id = memory_data.get('agent_id') or metadata.get('agent', {}).get('agent_id')
                if memory_agent_id == agent_id:
                    # This memory belongs to the agent, grant owner permissions
                    if memory_id not in self.permission_controller.memory_permissions:
                        self.permission_controller.memory_permissions[memory_id] = {}
                    if agent_id not in self.permission_controller.memory_permissions[memory_id]:
                        # Grant owner permissions
                        owner_permissions = self.multi_agent_config.default_permissions.get("owner", [])
                        owner_permissions_enum = []
                        for perm in owner_permissions:
                            try:
                                if isinstance(perm, AccessPermission):
                                    owner_permissions_enum.append(perm)
                                else:
                                    owner_permissions_enum.append(AccessPermission(perm.lower()))
                            except ValueError:
                                pass
                        self.permission_controller.memory_permissions[memory_id][agent_id] = owner_permissions_enum
                
                # Check if agent has access to this memory
                scope_access = self.scope_controller.check_scope_access(agent_id, memory_id)
                if scope_access:
                    scope_access_passed += 1
                    
                    permission_check = self.permission_controller.check_permission(
                        agent_id, memory_id, AccessPermission.READ
                    )
                    if permission_check:
                        permission_passed += 1
                        accessible_memories.append(memory_data)
                    else:
                        logger.debug(f"Permission denied for agent {agent_id} on memory {memory_id}")
                else:
                    logger.debug(f"Scope access denied for agent {agent_id} on memory {memory_id}")
            
            # Also check in-memory cache for any memories not in database (for backward compatibility)
            for scope in MemoryScope:
                for memory_type in MemoryType:
                    for memory_id, memory_data in self.scope_memories[scope][memory_type].items():
                        # Skip if already processed from database
                        if any(m.get('id') == memory_id for m in accessible_memories):
                            continue
                        
                        total_memories += 1
                        
                        # Check if agent has access to this memory
                        scope_access = self.scope_controller.check_scope_access(agent_id, memory_id)
                        if scope_access:
                            scope_access_passed += 1
                            
                            permission_check = self.permission_controller.check_permission(
                                agent_id, memory_id, AccessPermission.READ
                            )
                            if permission_check:
                                permission_passed += 1
                                accessible_memories.append(memory_data)
                            else:
                                logger.debug(f"Permission denied for agent {agent_id} on memory {memory_id}")
                        else:
                            logger.debug(f"Scope access denied for agent {agent_id} on memory {memory_id}")
            
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
            
            logger.info(f"Retrieved {len(accessible_memories)} memories for agent {agent_id} "
                       f"(total: {total_memories}, scope_passed: {scope_access_passed}, permission_passed: {permission_passed})")
            return accessible_memories
            
        except Exception as e:
            logger.error(f"Failed to get memories for agent {agent_id}: {e}", exc_info=True)
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
            agent_id: ID of the agent making the update
            updates: Dictionary of updates to apply
            
        Returns:
            Dictionary containing the updated memory information
        """
        try:
            # Find the memory
            memory_data = self._find_memory(memory_id)
            if not memory_data:
                raise ValueError(f"Memory {memory_id} not found")
            
            # Check permissions
            if not self.permission_controller.check_permission(
                agent_id, memory_id, AccessPermission.WRITE
            ):
                raise PermissionError(f"Agent {agent_id} does not have write permission for memory {memory_id}")
            
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
            
            logger.info(f"Updated memory {memory_id} by agent {agent_id}")
            
            return {
                'id': memory_id,
                'memory': memory_data['content'],
                'updated_at': memory_data['updated_at'],
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
            agent_id: ID of the agent making the deletion
            
        Returns:
            Dictionary containing the deletion result
        """
        try:
            # Find the memory
            memory_data = self._find_memory(memory_id)
            if not memory_data:
                raise ValueError(f"Memory {memory_id} not found")
            
            # Check permissions
            if not self.permission_controller.check_permission(
                agent_id, memory_id, AccessPermission.DELETE
            ):
                raise PermissionError(f"Agent {agent_id} does not have delete permission for memory {memory_id}")
            
            # Remove from scope-based storage
            scope = memory_data['scope']
            memory_type = memory_data['memory_type']
            del self.scope_memories[scope][memory_type][memory_id]
            
            # Clean up permissions
            self.permission_controller.revoke_permission(
                memory_id=memory_id,
                agent_id=agent_id,
                permission=AccessPermission.DELETE,
                revoked_by=agent_id
            )
            
            # Clean up collaboration if applicable
            if memory_id in self.collaboration_memories:
                del self.collaboration_memories[memory_id]
            
            logger.info(f"Deleted memory {memory_id} by agent {agent_id}")
            
            return {
                'success': True,
                'deleted_id': memory_id,
                'deleted_by': agent_id,
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
        Share a memory with other agents.
        
        Args:
            memory_id: ID of the memory to share
            from_agent: ID of the agent sharing the memory
            to_agents: List of agent IDs to share with
            permissions: Optional list of permissions to grant
            
        Returns:
            Dictionary containing the sharing result
        """
        try:
            # Find the memory
            memory_data = self._find_memory(memory_id)
            if not memory_data:
                raise ValueError(f"Memory {memory_id} not found")
            
            # Check if sharing agent has share permission
            if not self.permission_controller.check_permission(
                from_agent, memory_id, AccessPermission.SHARE
            ):
                raise PermissionError(f"Agent {from_agent} does not have share permission for memory {memory_id}")
            
            # Grant permissions to target agents
            shared_with = []
            default_permissions = permissions or ['read']
            
            for agent_id in to_agents:
                for perm in default_permissions:
                    try:
                        # Handle both string and AccessPermission enum inputs
                        if isinstance(perm, AccessPermission):
                            permission = perm
                        else:
                            # Convert to AccessPermission enum (case insensitive)
                            permission = AccessPermission(perm.lower())
                        self.permission_controller.grant_permission(
                            memory_id=memory_id,
                            agent_id=agent_id,
                            permission=permission,
                            granted_by=from_agent
                        )
                        shared_with.append(agent_id)
                    except ValueError:
                        logger.warning(f"Invalid permission: {perm}")
            
            # Update memory data to include shared_with list for scope access
            memory_data = self._find_memory(memory_id)
            if memory_data:
                if 'shared_with' not in memory_data:
                    memory_data['shared_with'] = []
                memory_data['shared_with'].extend(shared_with)
                
                # Also update in scope controller's storage
                if self.scope_controller:
                    for scope in MemoryScope:
                        for memory_type in MemoryType:
                            if memory_id in self.scope_controller.scope_storage[scope][memory_type]:
                                self.scope_controller.scope_storage[scope][memory_type][memory_id] = memory_data
                                break
            
            logger.info(f"Shared memory {memory_id} from {from_agent} to {len(shared_with)} agents")
            
            return {
                'success': True,
                'memory_id': memory_id,
                'shared_from': from_agent,
                'shared_with': shared_with,
                'permissions': default_permissions,
            }
            
        except Exception as e:
            logger.error(f"Failed to share memory {memory_id}: {e}")
            raise
    
    def get_context_info(self, agent_id: str) -> Dict[str, Any]:
        """
        Get context information for an agent.
        
        Args:
            agent_id: ID of the agent
            
        Returns:
            Dictionary containing context information
        """
        try:
            context_info = {
                'agent_id': agent_id,
                'groups': self.agent_memberships.get(agent_id, []),
                'active_collaborations': [],
                'memory_count': 0,
                'scope_breakdown': {},
            }
            
            # Count memories by scope
            for scope in MemoryScope:
                count = 0
                for memory_type in MemoryType:
                    for memory_data in self.scope_memories[scope][memory_type].values():
                        if memory_data['agent_id'] == agent_id:
                            count += 1
                context_info['scope_breakdown'][scope.value] = count
                context_info['memory_count'] += count
            
            # Get active collaborations
            for collaboration_id, collaboration_data in self.active_collaborations.items():
                if agent_id in collaboration_data.get('participants', []):
                    context_info['active_collaborations'].append(collaboration_id)
            
            return context_info
            
        except Exception as e:
            logger.error(f"Failed to get context info for agent {agent_id}: {e}")
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
            
            # Update decay for all memories
            for scope in MemoryScope:
                for memory_type in MemoryType:
                    for memory_id, memory_data in self.scope_memories[scope][memory_type].items():
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
            
            # Clean up forgotten memories
            for scope in MemoryScope:
                for memory_type in MemoryType:
                    memories_to_remove = []
                    
                    for memory_id, memory_data in self.scope_memories[scope][memory_type].items():
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
                        del self.scope_memories[scope][memory_type][memory_id]
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
                'scope_breakdown': {},
                'type_breakdown': {},
                'agent_breakdown': {},
                'collaboration_stats': {
                    'active_collaborations': len(self.active_collaborations),
                    'collaborative_memories': len(self.collaboration_memories),
                },
                'group_stats': {
                    'total_groups': len(self.agent_groups),
                    'total_memberships': len(self.agent_memberships),
                },
            }
            
            # Count memories by scope and type
            for scope in MemoryScope:
                scope_count = 0
                for memory_type in MemoryType:
                    type_count = len(self.scope_memories[scope][memory_type])
                    scope_count += type_count
                    stats['total_memories'] += type_count
                    
                    # Type breakdown
                    type_key = memory_type.value
                    if type_key not in stats['type_breakdown']:
                        stats['type_breakdown'][type_key] = 0
                    stats['type_breakdown'][type_key] += type_count
                
                stats['scope_breakdown'][scope.value] = scope_count
            
            # Count memories by agent
            for scope in MemoryScope:
                for memory_type in MemoryType:
                    for memory_data in self.scope_memories[scope][memory_type].values():
                        agent_id = memory_data['agent_id']
                        if agent_id not in stats['agent_breakdown']:
                            stats['agent_breakdown'][agent_id] = 0
                        stats['agent_breakdown'][agent_id] += 1
            
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
        Check if an agent has a specific permission for a memory.
        
        Args:
            agent_id: ID of the agent
            memory_id: ID of the memory
            permission: Permission to check
            
        Returns:
            True if the agent has the permission, False otherwise
        """
        try:
            # Handle both string and AccessPermission enum inputs
            if isinstance(permission, AccessPermission):
                permission_enum = permission
            else:
                permission_enum = AccessPermission(permission.upper())
            return self.permission_controller.check_permission(agent_id, memory_id, permission_enum)
        except (ValueError, Exception):
            return False
    
    def _find_memory(self, memory_id: str) -> Optional[Dict[str, Any]]:
        """Find a memory by ID across all scopes and types."""
        for scope in MemoryScope:
            for memory_type in MemoryType:
                if memory_id in self.scope_memories[scope][memory_type]:
                    return self.scope_memories[scope][memory_type][memory_id]
        return None
    
    def create_group(self, group_name: str, agent_ids: List[str], permissions: Optional[Dict[str, List[str]]] = None) -> Dict[str, Any]:
        """
        Create an agent group.
        
        Args:
            group_name: Name of the group
            agent_ids: List of agent IDs to include in the group
            permissions: Optional permissions configuration
            
        Returns:
            Dictionary containing the group creation result
        """
        try:
            # Check if group already exists
            if group_name in self.agent_groups:
                raise ValueError(f"Group '{group_name}' already exists")
            
            # Validate agent IDs
            for agent_id in agent_ids:
                if not agent_id or not isinstance(agent_id, str):
                    raise ValueError(f"Invalid agent ID: {agent_id}")
            
            # Set default permissions if not provided
            if permissions is None:
                permissions = {
                    'owner': ['read', 'write', 'delete', 'admin'],
                    'collaborator': ['read', 'write'],
                    'viewer': ['read']
                }
            
            # Create the group
            self.agent_groups[group_name] = {
                'members': agent_ids.copy(),
                'permissions': permissions.copy(),
                'created_at': datetime.now().isoformat(),
                'created_by': 'system'  # Could be passed as parameter
            }
            
            # Update agent memberships
            for agent_id in agent_ids:
                if agent_id not in self.agent_memberships:
                    self.agent_memberships[agent_id] = []
                if group_name not in self.agent_memberships[agent_id]:
                    self.agent_memberships[agent_id].append(group_name)
            
            logger.info(f"Created group '{group_name}' with {len(agent_ids)} members")
            
            return {
                'success': True,
                'group_name': group_name,
                'members': agent_ids,
                'permissions': permissions,
                'created_at': self.agent_groups[group_name]['created_at']
            }
            
        except Exception as e:
            logger.error(f"Failed to create group '{group_name}': {e}")
            return {
                'success': False,
                'error': str(e),
                'group_name': group_name
            }
