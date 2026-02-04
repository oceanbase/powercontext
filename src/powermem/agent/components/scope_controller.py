"""
Multi-Agent Scope Controller

Manages memory scopes and determines appropriate scope for new memories
based on content analysis and context. Migrated and refactored from
the original multi_agent implementation.
"""

import json
import logging
from typing import Any, Dict, List, Optional

from powermem.agent.agent import ConfigObject
from powermem.agent.types import MemoryScope, MemoryType
from powermem.agent.abstract.scope import AgentScopeManagerBase
from powermem.integrations import LLMFactory

logger = logging.getLogger(__name__)


class ScopeController(AgentScopeManagerBase):
    """
    Multi-agent scope controller implementation.
    
    Determines memory scope based on content analysis and context,
    manages scope-specific storage and retrieval.
    """
    
    def __init__(self, config: Dict[str, Any]):
        """
        Initialize the scope controller.
        
        Args:
            config: Memory configuration object
        """
        super().__init__(config.agent_memory.multi_agent_config.__dict__)
        self.config = config
        self.multi_agent_config = config.agent_memory.multi_agent_config
        
        # Extract llm config as dict (handle both ConfigObject and dict)
        try:
            llm_provider = config.llm.provider if hasattr(config, 'llm') else 'mock'
            llm_config_obj = config.llm.config if hasattr(config, 'llm') else {}
            
            # Convert to dict if ConfigObject
            if isinstance(llm_config_obj, ConfigObject):
                llm_config = llm_config_obj.to_dict()
            else:
                llm_config = dict(llm_config_obj) if llm_config_obj else {}
        except Exception as e:
            logger.warning(f"Failed to extract LLM config: {e}")
            llm_provider = 'mock'
            llm_config = {}
        
        self.llm = LLMFactory.create(llm_provider, llm_config)
        
        # Scope-specific storage areas
        self.scope_storage = {
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
            }
        }
    
    def initialize(self) -> None:
        """
        Initialize the scope controller.
        """
        try:
            # Initialize scope-specific configurations
            self._initialize_scope_configs()
            self.initialized = True
            logger.info("Scope controller initialized successfully")
            
        except Exception as e:
            logger.error(f"Failed to initialize scope controller: {e}")
            raise
    
    def _initialize_scope_configs(self) -> None:
        """Initialize scope-specific configurations."""
        # Load scope-specific configurations from config
        if hasattr(self.multi_agent_config, 'scope_specific_configs'):
            self.scope_configs = self.multi_agent_config.scope_specific_configs
        else:
            # Default configurations
            self.scope_configs = {
                "private": {
                    "decay_rate_multiplier": 1.0,
                    "importance_threshold": 0.5,
                    "max_size": 5000
                },
                "public": {
                    "decay_rate_multiplier": 0.8,
                    "importance_threshold": 0.7,
                    "consensus_required": True,
                    "max_size": 10000
                },
                "agent_group": {
                    "decay_rate_multiplier": 0.9,
                    "importance_threshold": 0.6,
                    "max_size": 8000
                },
                "restricted": {
                    "decay_rate_multiplier": 1.2,
                    "importance_threshold": 0.8,
                    "max_size": 3000
                }
            }
    
    def determine_scope(
        self,
        agent_id: str,
        context: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> MemoryScope:
        """
        Determine memory scope based on content analysis and context.
        
        Args:
            agent_id: ID of the agent creating the memory
            context: Additional context information
            metadata: Additional metadata information
            
        Returns:
            Determined memory scope
        """
        try:
            # Extract content from metadata if available
            content = metadata.get('content', '') if metadata else ''
            
            # Use LLM to analyze content and determine scope
            scope_analysis_prompt = self._build_scope_analysis_prompt(content, agent_id, context, metadata)
            
            response = self.llm.generate_response(
                messages=[{"role": "user", "content": scope_analysis_prompt}],
                response_format={"type": "json_object"}
            )
            
            analysis = json.loads(response)
            suggested_scope = analysis.get("suggested_scope", "PRIVATE")
            
            # Validate the suggested scope
            try:
                # Try to convert string to MemoryScope enum
                if isinstance(suggested_scope, str):
                    # Handle case-insensitive matching
                    suggested_scope = suggested_scope.upper()
                    # Map common variations
                    scope_mapping = {
                        'PRIVATE': MemoryScope.PRIVATE,
                        'PUBLIC': MemoryScope.PUBLIC,
                        'AGENT_GROUP': MemoryScope.AGENT_GROUP,
                        'AGENT': MemoryScope.AGENT_GROUP,
                        'USER_GROUP': MemoryScope.USER_GROUP,
                        'USER': MemoryScope.USER_GROUP,
                        'RESTRICTED': MemoryScope.RESTRICTED,
                    }
                    
                    if suggested_scope in scope_mapping:
                        scope = scope_mapping[suggested_scope]
                        logger.info(f"Determined scope '{scope.value}' for content from agent '{agent_id}'")
                        return scope
                    else:
                        # Try direct enum creation as fallback
                        scope = MemoryScope(suggested_scope.lower())
                        logger.info(f"Determined scope '{scope.value}' for content from agent '{agent_id}'")
                        return scope
                else:
                    # Already a MemoryScope enum
                    if isinstance(suggested_scope, MemoryScope):
                        logger.info(f"Determined scope '{suggested_scope.value}' for content from agent '{agent_id}'")
                        return suggested_scope
                    # Fallback if it's some other type (e.g. a string that somehow got here)
                    raise ValueError(f"Invalid scope type: {type(suggested_scope)}")
                    
            except (ValueError, AttributeError):
                logger.warning(f"Invalid scope '{suggested_scope}' suggested by LLM, using default")
                
                # Ensure we return a MemoryScope enum
                default_scope = self.multi_agent_config.default_scope
                if isinstance(default_scope, MemoryScope):
                    return default_scope
                
                # Handle common string mappings for default scope
                default_scope_str = str(default_scope).upper()
                fallback_mapping = {
                    'PRIVATE': MemoryScope.PRIVATE,
                    'PUBLIC': MemoryScope.PUBLIC,
                    'AGENT_GROUP': MemoryScope.AGENT_GROUP,
                    'AGENT': MemoryScope.AGENT_GROUP,
                    'USER_GROUP': MemoryScope.USER_GROUP,
                    'USER': MemoryScope.USER_GROUP,
                    'RESTRICTED': MemoryScope.RESTRICTED,
                }
                if default_scope_str in fallback_mapping:
                    return fallback_mapping[default_scope_str]
                
                try:
                    return MemoryScope(str(default_scope).lower())
                except ValueError:
                    return MemoryScope.PRIVATE
                
        except Exception as e:
            logger.error(f"Error determining scope: {e}")
            
            # Ensure we return a MemoryScope enum
            default_scope = self.multi_agent_config.default_scope
            if isinstance(default_scope, MemoryScope):
                return default_scope
            
            # Handle common string mappings for default scope
            default_scope_str = str(default_scope).upper()
            fallback_mapping = {
                'PRIVATE': MemoryScope.PRIVATE,
                'PUBLIC': MemoryScope.PUBLIC,
                'AGENT_GROUP': MemoryScope.AGENT_GROUP,
                'AGENT': MemoryScope.AGENT_GROUP,
                'USER_GROUP': MemoryScope.USER_GROUP,
                'USER': MemoryScope.USER_GROUP,
                'RESTRICTED': MemoryScope.RESTRICTED,
            }
            if default_scope_str in fallback_mapping:
                return fallback_mapping[default_scope_str]
                
            try:
                return MemoryScope(str(default_scope).lower())
            except ValueError:
                return MemoryScope.PRIVATE
    
    def get_accessible_memories(
        self,
        agent_id: str,
        scope: Optional[MemoryScope] = None
    ) -> List[str]:
        """
        Get list of memory IDs accessible to an agent.
        
        Args:
            agent_id: ID of the agent
            scope: Optional scope filter
            
        Returns:
            List of accessible memory IDs
        """
        try:
            accessible_memories = []
            
            # Define scopes to check based on agent's access level
            scopes_to_check = [scope] if scope else [
                MemoryScope.PRIVATE,
                MemoryScope.AGENT_GROUP,
                MemoryScope.PUBLIC,
                MemoryScope.RESTRICTED
            ]
            
            for scope_to_check in scopes_to_check:
                if scope_to_check in self.scope_storage:
                    for memory_type in MemoryType:
                        for memory_id in self.scope_storage[scope_to_check][memory_type].keys():
                            # Check if agent has access to this memory
                            if self._check_agent_scope_access(agent_id, memory_id, scope_to_check):
                                accessible_memories.append(memory_id)
            
            return accessible_memories
            
        except Exception as e:
            logger.error(f"Error getting accessible memories for agent {agent_id}: {e}")
            return []
    
    def check_scope_access(
        self,
        agent_id: str,
        memory_id: str
    ) -> bool:
        """
        Check if an agent has access to a memory based on scope.
        
        Args:
            agent_id: ID of the agent
            memory_id: ID of the memory
            
        Returns:
            True if access is allowed, False otherwise
        """
        try:
            # Find the memory and its scope
            memory_scope = self._find_memory_scope(memory_id)
            if not memory_scope:
                return False
            
            return self._check_agent_scope_access(agent_id, memory_id, memory_scope)
            
        except Exception as e:
            logger.error(f"Error checking scope access for agent {agent_id} and memory {memory_id}: {e}")
            return False
    
    def update_memory_scope(
        self,
        memory_id: str,
        new_scope: MemoryScope,
        agent_id: str
    ) -> Dict[str, Any]:
        """
        Update the scope of a memory.
        
        Args:
            memory_id: ID of the memory
            new_scope: New scope for the memory
            agent_id: ID of the agent making the change
            
        Returns:
            Dictionary containing the update result
        """
        try:
            # Find current scope and memory data
            current_scope = self._find_memory_scope(memory_id)
            if not current_scope:
                raise ValueError(f"Memory {memory_id} not found")
            
            # Check if agent has permission to change scope
            if not self._check_scope_change_permission(agent_id, memory_id, current_scope, new_scope):
                raise PermissionError(f"Agent {agent_id} does not have permission to change scope")
            
            # Move memory to new scope
            memory_data = self._move_memory_to_scope(memory_id, current_scope, new_scope)
            
            logger.info(f"Updated memory {memory_id} scope from {current_scope.value} to {new_scope.value}")
            
            return {
                'success': True,
                'memory_id': memory_id,
                'old_scope': current_scope.value,
                'new_scope': new_scope.value,
                'updated_by': agent_id,
            }
            
        except Exception as e:
            logger.error(f"Error updating memory scope: {e}")
            return {
                'success': False,
                'error': str(e),
                'memory_id': memory_id,
            }
    
    def get_scope_members(
        self,
        scope: MemoryScope,
        memory_id: Optional[str] = None
    ) -> List[str]:
        """
        Get list of agents with access to a scope.
        
        Args:
            scope: Memory scope
            memory_id: Optional specific memory ID
            
        Returns:
            List of agent IDs with access
        """
        try:
            members = []
            
            if memory_id:
                # Get members for specific memory
                memory_data = self._find_memory_data(memory_id)
                if memory_data:
                    # Add memory owner
                    if 'agent_id' in memory_data:
                        members.append(memory_data['agent_id'])
                    
                    # Add shared members if applicable
                    if 'shared_with' in memory_data:
                        members.extend(memory_data['shared_with'])
            else:
                # Get all agents with access to this scope type
                if scope == MemoryScope.PUBLIC:
                    # All agents have access to public memories
                    members = ['*']  # Wildcard for all agents
                elif scope == MemoryScope.PRIVATE:
                    # Only memory owners have access
                    members = self._get_all_memory_owners(scope)
                elif scope == MemoryScope.AGENT_GROUP:
                    # Group members have access
                    members = self._get_group_members(scope)
                elif scope == MemoryScope.RESTRICTED:
                    # Restricted access - need specific permissions
                    members = self._get_restricted_members(scope)
            
            return list(set(members))  # Remove duplicates
            
        except Exception as e:
            logger.error(f"Error getting scope members for {scope.value}: {e}")
            return []
    
    def add_scope_member(
        self,
        scope: MemoryScope,
        agent_id: str,
        memory_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Add an agent to a scope.
        
        Args:
            scope: Memory scope
            agent_id: ID of the agent to add
            memory_id: Optional specific memory ID
            
        Returns:
            Dictionary containing the add result
        """
        try:
            if memory_id:
                # Add agent to specific memory
                memory_data = self._find_memory_data(memory_id)
                if memory_data:
                    if 'shared_with' not in memory_data:
                        memory_data['shared_with'] = []
                    
                    if agent_id not in memory_data['shared_with']:
                        memory_data['shared_with'].append(agent_id)
                        logger.info(f"Added agent {agent_id} to memory {memory_id} scope")
                        return {'success': True, 'agent_id': agent_id, 'memory_id': memory_id}
            else:
                # Add agent to scope in general (for group scopes)
                if scope == MemoryScope.AGENT_GROUP:
                    # This would typically involve adding to a group membership
                    logger.info(f"Added agent {agent_id} to {scope.value} scope")
                    return {'success': True, 'agent_id': agent_id, 'scope': scope.value}
            
            return {'success': False, 'error': 'Could not add agent to scope'}
            
        except Exception as e:
            logger.error(f"Error adding agent {agent_id} to scope {scope.value}: {e}")
            return {'success': False, 'error': str(e)}
    
    def remove_scope_member(
        self,
        scope: MemoryScope,
        agent_id: str,
        memory_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Remove an agent from a scope.
        
        Args:
            scope: Memory scope
            agent_id: ID of the agent to remove
            memory_id: Optional specific memory ID
            
        Returns:
            Dictionary containing the removal result
        """
        try:
            if memory_id:
                # Remove agent from specific memory
                memory_data = self._find_memory_data(memory_id)
                if memory_data and 'shared_with' in memory_data:
                    if agent_id in memory_data['shared_with']:
                        memory_data['shared_with'].remove(agent_id)
                        logger.info(f"Removed agent {agent_id} from memory {memory_id} scope")
                        return {'success': True, 'agent_id': agent_id, 'memory_id': memory_id}
            
            return {'success': False, 'error': 'Could not remove agent from scope'}
            
        except Exception as e:
            logger.error(f"Error removing agent {agent_id} from scope {scope.value}: {e}")
            return {'success': False, 'error': str(e)}
    
    def get_scope_statistics(self) -> Dict[str, Any]:
        """
        Get statistics about scope usage.
        
        Returns:
            Dictionary containing scope statistics
        """
        try:
            stats = {
                'total_memories': 0,
                'scope_breakdown': {},
                'type_breakdown': {},
                'access_patterns': {},
            }
            
            # Count memories by scope and type
            for scope, scope_types in self.scope_storage.items():
                scope_count = 0
                for memory_type, memories in scope_types.items():
                    type_count = len(memories)
                    scope_count += type_count
                    stats['total_memories'] += type_count
                    
                    # Type breakdown
                    type_key = memory_type.value
                    if type_key not in stats['type_breakdown']:
                        stats['type_breakdown'][type_key] = 0
                    stats['type_breakdown'][type_key] += type_count
                
                stats['scope_breakdown'][scope.value] = scope_count
            
            return stats
            
        except Exception as e:
            logger.error(f"Error getting scope statistics: {e}")
            return {}
    
    def _build_scope_analysis_prompt(
        self,
        content: str,
        agent_id: str,
        context: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> str:
        """Build prompt for scope analysis."""
        prompt = f"""
        Analyze the following memory content and determine the appropriate scope for storage.
        
        Content: {content}
        Agent ID: {agent_id}
        
        Context: {context or {}}
        Metadata: {metadata or {}}
        
        Consider the following scope options:
        - PRIVATE: Personal/private information, only accessible to the creating agent
        - AGENT_GROUP: Information relevant to a specific group of agents
        - PUBLIC: Information that should be accessible to all agents
        - RESTRICTED: Information requiring special permissions
        
        Return a JSON response with:
        {{
            "suggested_scope": "PRIVATE|AGENT_GROUP|PUBLIC|RESTRICTED",
            "reasoning": "Explanation for the scope choice",
            "confidence": 0.0-1.0
        }}
        """
        return prompt
    
    def _check_agent_scope_access(
        self,
        agent_id: str,
        memory_id: str,
        scope: MemoryScope
    ) -> bool:
        """Check if an agent has access to a memory in a specific scope."""
        try:
            if scope == MemoryScope.PUBLIC:
                return True  # All agents have access to public memories
            elif scope == MemoryScope.PRIVATE:
                # Only the creating agent has access
                memory_data = self._find_memory_data(memory_id)
                if memory_data:
                    creator_id = memory_data.get('agent_id')
                    logger.debug(f"Private memory {memory_id}: creator={creator_id}, requester={agent_id}")
                    return creator_id == agent_id
                else:
                    logger.debug(f"Private memory {memory_id}: memory data not found")
                    return False
            elif scope == MemoryScope.AGENT_GROUP:
                # Group members have access
                memory_data = self._find_memory_data(memory_id)
                if memory_data:
                    # Check if agent is in the group or shared with
                    if memory_data.get('agent_id') == agent_id:
                        return True
                    if agent_id in memory_data.get('shared_with', []):
                        return True
                return False
            elif scope == MemoryScope.RESTRICTED:
                # Need specific permissions
                memory_data = self._find_memory_data(memory_id)
                if memory_data:
                    # Check if agent has restricted access
                    return agent_id in memory_data.get('restricted_access', [])
                return False
            
            return False
            
        except Exception as e:
            logger.error(f"Error checking agent scope access: {e}")
            return False
    
    def _find_memory_scope(self, memory_id: str) -> Optional[MemoryScope]:
        """Find the scope of a memory."""
        for scope, scope_types in self.scope_storage.items():
            for memory_type, memories in scope_types.items():
                if memory_id in memories:
                    return scope
        return None
    
    def _find_memory_data(self, memory_id: str) -> Optional[Dict[str, Any]]:
        """Find memory data by ID."""
        for scope, scope_types in self.scope_storage.items():
            for memory_type, memories in scope_types.items():
                if memory_id in memories:
                    return memories[memory_id]
        return None
    
    def _move_memory_to_scope(
        self,
        memory_id: str,
        from_scope: MemoryScope,
        to_scope: MemoryScope
    ) -> Dict[str, Any]:
        """Move a memory from one scope to another."""
        # Find memory data
        memory_data = None
        memory_type = None
        
        for mt in MemoryType:
            if memory_id in self.scope_storage[from_scope][mt]:
                memory_data = self.scope_storage[from_scope][mt][memory_id]
                memory_type = mt
                break
        
        if not memory_data or not memory_type:
            raise ValueError(f"Memory {memory_id} not found in scope {from_scope.value}")
        
        # Remove from old scope
        del self.scope_storage[from_scope][memory_type][memory_id]
        
        # Add to new scope
        self.scope_storage[to_scope][memory_type][memory_id] = memory_data
        
        # Update memory metadata
        memory_data['scope'] = to_scope
        memory_data['scope_updated_at'] = memory_data.get('updated_at', '')
        
        return memory_data
    
    def _check_scope_change_permission(
        self,
        agent_id: str,
        memory_id: str,
        current_scope: MemoryScope,
        new_scope: MemoryScope
    ) -> bool:
        """Check if an agent has permission to change memory scope."""
        # Only memory owner can change scope
        memory_data = self._find_memory_data(memory_id)
        if memory_data and memory_data.get('agent_id') == agent_id:
            return True
        
        # Check if it's a scope upgrade (more restrictive to less restrictive)
        scope_hierarchy = {
            MemoryScope.PRIVATE: 0,
            MemoryScope.AGENT_GROUP: 1,
            MemoryScope.PUBLIC: 2,
            MemoryScope.RESTRICTED: 3,
        }
        
        current_level = scope_hierarchy.get(current_scope, 0)
        new_level = scope_hierarchy.get(new_scope, 0)
        
        # Allow scope changes within the same level or to more restrictive
        return new_level <= current_level
    
    def _get_all_memory_owners(self, scope: MemoryScope) -> List[str]:
        """Get all agents who own memories in a scope."""
        owners = set()
        for memory_type in MemoryType:
            for memory_data in self.scope_storage[scope][memory_type].values():
                if 'agent_id' in memory_data:
                    owners.add(memory_data['agent_id'])
        return list(owners)
    
    def _get_group_members(self, scope: MemoryScope) -> List[str]:
        """Get all group members for a scope."""
        # This would typically involve checking group memberships
        # For now, return all agents who have memories in this scope
        return self._get_all_memory_owners(scope)
    
    def _get_restricted_members(self, scope: MemoryScope) -> List[str]:
        """Get all agents with restricted access to a scope."""
        restricted_members = set()
        for memory_type in MemoryType:
            for memory_data in self.scope_storage[scope][memory_type].values():
                if 'restricted_access' in memory_data:
                    restricted_members.update(memory_data['restricted_access'])
        return list(restricted_members)
