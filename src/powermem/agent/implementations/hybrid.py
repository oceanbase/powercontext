"""
Hybrid Memory Manager Implementation

This module provides the concrete implementation of the hybrid memory manager,
which dynamically switches between multi-agent and multi-user modes based on context.
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from typing import Any, Dict
from powermem.intelligence.intelligent_memory_manager import IntelligentMemoryManager
from powermem.agent.abstract.manager import AgentMemoryManagerBase
from powermem.agent.implementations.multi_agent import MultiAgentMemoryManager
from powermem.agent.implementations.multi_user import MultiUserMemoryManager

logger = logging.getLogger(__name__)


class HybridMemoryManager(AgentMemoryManagerBase):
    """
    Hybrid memory manager implementation.
    
    Dynamically switches between multi-agent and multi-user modes based on context,
    providing intelligent mode selection and seamless transitions.
    """
    
    def __init__(self, config: Dict[str, Any]):
        """
        Initialize the hybrid memory manager.
        
        Args:
            config: Memory configuration object
        """
        super().__init__(config)
        self.hybrid_config = config.agent_memory.hybrid_config
        
        # Initialize components
        self.intelligent_manager = None
        self.multi_agent_manager = None
        self.multi_user_manager = None
        
        # Mode management
        self.current_mode = self.hybrid_config.primary_mode
        self.mode_switch_history = []
        self.mode_switch_cooldown = {}
        
        # Context analysis
        self.context_analyzer = None
        self.mode_confidence_scores = {}
        
        # Unified memory storage for cross-mode access
        self.unified_memory_index = {}
        self.mode_specific_memories = {
            'multi_agent': {},
            'multi_user': {},
        }
    
    def initialize(self) -> None:
        """
        Initialize the memory manager and all components.
        """
        try:
            # Initialize intelligent memory manager
            self.intelligent_manager = IntelligentMemoryManager(self.config)
            
            # Initialize mode-specific managers
            self._initialize_mode_managers()
            
            # Initialize context analyzer
            self._initialize_context_analyzer()
            
            # Set initial mode
            self._switch_mode(self.hybrid_config.primary_mode, "initialization")
            
            self.initialized = True
            logger.info(f"Hybrid memory manager initialized with primary mode: {self.current_mode}")
            
        except Exception as e:
            logger.error(f"Failed to initialize hybrid memory manager: {e}", exc_info=True)
            raise
    
    def _initialize_mode_managers(self) -> None:
        """Initialize mode-specific managers."""
        # Create multi-agent manager
        multi_agent_config = self.config.copy()
        # Ensure multi_agent_config exists
        if 'multi_agent_config' not in multi_agent_config.agent_memory._data:
            multi_agent_config.agent_memory._data['multi_agent_config'] = {
                'enabled': True,
                'default_permissions': {
                    'owner': ['read', 'write', 'delete', 'admin'],
                    'collaborator': ['read', 'write'],
                    'viewer': ['read']
                },
                'collaborative_memory_config': {
                    'enabled': True,
                    'auto_collaboration': True,
                    'collaboration_threshold': 0.7,
                    'max_collaborators': 5,
                    'collaboration_timeout': 3600
                },
                'privacy_config': {
                    'enabled': True,
                    'default_privacy_level': 'standard',
                    'encryption_enabled': False,
                    'anonymization_enabled': True
                },
                'default_scope': 'private'
            }
        multi_agent_config.agent_memory.mode = "multi_agent"
        multi_agent_config.agent_memory.enabled = True
        self.multi_agent_manager = MultiAgentMemoryManager(multi_agent_config)
        self.multi_agent_manager.initialize()
        
        # Create multi-user manager
        multi_user_config = self.config.copy()
        # Ensure multi_user_config exists
        if 'multi_user_config' not in multi_user_config.agent_memory._data:
            multi_user_config.agent_memory._data['multi_user_config'] = {
                'enabled': True,
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
        multi_user_config.agent_memory.mode = "multi_user"
        multi_user_config.agent_memory.enabled = True
        self.multi_user_manager = MultiUserMemoryManager(multi_user_config)
        self.multi_user_manager.initialize()
    
    def _initialize_context_analyzer(self) -> None:
        """Initialize context analyzer for mode switching."""
        self.context_analyzer = {
            'collaboration_keywords': [
                'team', 'collaborate', 'meeting', 'discuss', 'share', 'group',
                'project', 'work together', 'joint', 'collective'
            ],
            'personal_keywords': [
                'personal', 'private', 'individual', 'my', 'own', 'self',
                'personal preference', 'private note', 'individual task'
            ],
            'context_weights': {
                'collaboration_level': 0.4,
                'participants_count': 0.3,
                'content_keywords': 0.2,
                'metadata_hints': 0.1,
            }
        }
    
    def process_memory(
        self,
        content: str,
        agent_id: str,
        context: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Process and store a new memory with intelligent mode selection.
        
        Args:
            content: The memory content to store
            agent_id: ID of the agent/user creating the memory
            context: Additional context information
            metadata: Additional metadata for the memory
            
        Returns:
            Dictionary containing the processed memory information
        """
        try:
            # Analyze context to determine optimal mode
            optimal_mode = self._analyze_context_for_mode(content, context, metadata)
            
            # Switch mode if necessary
            if optimal_mode != self.current_mode:
                self._switch_mode(optimal_mode, "context_analysis")
            
            # Process memory with current mode manager
            if self.current_mode == "multi_agent":
                result = self.multi_agent_manager.process_memory(content, agent_id, context, metadata)
            else:
                result = self.multi_user_manager.process_memory(content, agent_id, context, metadata)
            
            # Store in unified index
            memory_id = result['id']
            self.unified_memory_index[memory_id] = {
                'id': memory_id,
                'content': content,
                'agent_id': agent_id,
                'mode': self.current_mode,
                'processed_at': datetime.now().isoformat(),
                'context': context or {},
                'metadata': metadata or {},
            }
            
            # Store in mode-specific storage
            if self.current_mode not in self.mode_specific_memories:
                self.mode_specific_memories[self.current_mode] = {}
            self.mode_specific_memories[self.current_mode][memory_id] = result
            
            # Add mode information to result
            result['processed_in_mode'] = self.current_mode
            result['mode_switch_reason'] = "context_analysis" if optimal_mode != self.hybrid_config.primary_mode else "primary_mode"
            
            logger.info(f"Processed memory {memory_id} in {self.current_mode} mode")
            return result
            
        except Exception as e:
            logger.error(f"Failed to process memory for {agent_id}: {e}", exc_info=True)
            raise
    
    def _analyze_context_for_mode(
        self,
        content: str,
        context: Optional[Dict[str, Any]],
        metadata: Optional[Dict[str, Any]]
    ) -> str:
        """
        Analyze context to determine the optimal mode for processing.
        
        Args:
            content: Memory content
            context: Context information
            metadata: Metadata information
            
        Returns:
            Optimal mode ('multi_agent' or 'multi_user')
        """
        try:
            scores = {
                'multi_agent': 0.0,
                'multi_user': 0.0,
            }
            
            # Analyze collaboration level
            collaboration_level = context.get('collaboration_level', 'none') if context else 'none'
            if collaboration_level in ['high', 'medium']:
                scores['multi_agent'] += 0.4
            else:
                scores['multi_user'] += 0.3
            
            # Analyze participants
            participants = context.get('participants', []) if context else []
            if len(participants) > 1:
                scores['multi_agent'] += 0.3
            else:
                scores['multi_user'] += 0.2
            
            # Analyze content keywords
            content_lower = content.lower()
            collaboration_keywords = sum(1 for keyword in self.context_analyzer['collaboration_keywords'] 
                                       if keyword in content_lower)
            personal_keywords = sum(1 for keyword in self.context_analyzer['personal_keywords'] 
                                  if keyword in content_lower)
            
            if collaboration_keywords > personal_keywords:
                scores['multi_agent'] += 0.2
            else:
                scores['multi_user'] += 0.2
            
            # Analyze metadata hints
            if metadata:
                if metadata.get('collaboration') or metadata.get('meeting_type'):
                    scores['multi_agent'] += 0.1
                elif metadata.get('personal') or metadata.get('privacy_level') == 'private':
                    scores['multi_user'] += 0.1
            
            # Apply confidence threshold
            confidence_threshold = self.hybrid_config.mode_switching_config.get('confidence_threshold', 0.7)
            
            if scores['multi_agent'] > scores['multi_user'] and scores['multi_agent'] > confidence_threshold:
                return 'multi_agent'
            elif scores['multi_user'] > scores['multi_agent'] and scores['multi_user'] > confidence_threshold:
                return 'multi_user'
            else:
                # Default to primary mode if confidence is low
                return self.hybrid_config.primary_mode
                
        except Exception as e:
            logger.error(f"Failed to analyze context for mode: {e}", exc_info=True)
            return self.hybrid_config.primary_mode
    
    def _switch_mode(self, new_mode: str, reason: str) -> None:
        """
        Switch to a new mode.
        
        Args:
            new_mode: New mode to switch to
            reason: Reason for the switch
        """
        try:
            # Check cooldown
            if self._is_in_cooldown(new_mode):
                logger.info(f"Mode switch to {new_mode} blocked by cooldown")
                return
            
            old_mode = self.current_mode
            self.current_mode = new_mode
            
            # Record switch
            switch_record = {
                'from_mode': old_mode,
                'to_mode': new_mode,
                'reason': reason,
                'timestamp': datetime.now().isoformat(),
            }
            self.mode_switch_history.append(switch_record)
            
            # Set cooldown
            cooldown_seconds = self.hybrid_config.mode_switching_config.get('switch_cooldown_seconds', 300)
            self.mode_switch_cooldown[new_mode] = datetime.now().timestamp() + cooldown_seconds
            
            logger.info(f"Switched from {old_mode} to {new_mode} mode (reason: {reason})")
            
        except Exception as e:
            logger.error(f"Failed to switch mode to {new_mode}: {e}", exc_info=True)
    
    def _is_in_cooldown(self, mode: str) -> bool:
        """Check if a mode is in cooldown."""
        if mode not in self.mode_switch_cooldown:
            return False
        
        return datetime.now().timestamp() < self.mode_switch_cooldown[mode]
    
    def get_memories(
        self,
        agent_id: str,
        query: Optional[str] = None,
        filters: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """
        Retrieve memories for a specific agent/user from both modes.
        
        Args:
            agent_id: ID of the agent/user
            query: Optional query string for filtering
            filters: Optional additional filters
            
        Returns:
            List of memory dictionaries from both modes
        """
        try:
            all_memories = []
            
            # Get memories from current mode
            if self.current_mode == "multi_agent":
                current_memories = self.multi_agent_manager.get_memories(agent_id, query, filters)
            else:
                current_memories = self.multi_user_manager.get_memories(agent_id, query, filters)
            
            all_memories.extend(current_memories)
            
            # Get memories from other mode if cross-mode access is enabled
            if self.hybrid_config.mode_switching_config.get('enable_cross_mode_access', True):
                other_mode = "multi_user" if self.current_mode == "multi_agent" else "multi_agent"
                
                if other_mode == "multi_agent":
                    other_memories = self.multi_agent_manager.get_memories(agent_id, query, filters)
                else:
                    other_memories = self.multi_user_manager.get_memories(agent_id, query, filters)
                
                # Mark memories from other mode
                for memory in other_memories:
                    memory['source_mode'] = other_mode
                    memory['cross_mode_access'] = True
                
                all_memories.extend(other_memories)
            
            # Remove duplicates based on content
            unique_memories = []
            seen_content = set()
            
            for memory in all_memories:
                content = memory.get('content', '')
                if content not in seen_content:
                    seen_content.add(content)
                    unique_memories.append(memory)
            
            logger.info(f"Retrieved {len(unique_memories)} memories for {agent_id} (current mode: {self.current_mode})")
            return unique_memories
            
        except Exception as e:
            logger.error(f"Failed to get memories for {agent_id}: {e}", exc_info=True)
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
            agent_id: ID of the agent/user making the update
            updates: Dictionary of updates to apply
            
        Returns:
            Dictionary containing the updated memory information
        """
        try:
            # Find which mode the memory belongs to
            memory_mode = self._get_memory_mode(memory_id)
            
            if not memory_mode:
                raise ValueError(f"Memory {memory_id} not found in any mode")
            
            # Update with appropriate manager
            if memory_mode == "multi_agent":
                result = self.multi_agent_manager.update_memory(memory_id, agent_id, updates)
            else:
                result = self.multi_user_manager.update_memory(memory_id, agent_id, updates)
            
            # Update unified index
            if memory_id in self.unified_memory_index:
                self.unified_memory_index[memory_id]['updated_at'] = datetime.now().isoformat()
                self.unified_memory_index[memory_id]['updated_by'] = agent_id
            
            result['updated_in_mode'] = memory_mode
            logger.info(f"Updated memory {memory_id} in {memory_mode} mode")
            return result
            
        except Exception as e:
            logger.error(f"Failed to update memory {memory_id}: {e}", exc_info=True)
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
            agent_id: ID of the agent/user making the deletion
            
        Returns:
            Dictionary containing the deletion result
        """
        try:
            # Find which mode the memory belongs to
            memory_mode = self._get_memory_mode(memory_id)
            
            if not memory_mode:
                raise ValueError(f"Memory {memory_id} not found in any mode")
            
            # Delete with appropriate manager
            if memory_mode == "multi_agent":
                result = self.multi_agent_manager.delete_memory(memory_id, agent_id)
            else:
                result = self.multi_user_manager.delete_memory(memory_id, agent_id)
            
            # Clean up unified index
            if memory_id in self.unified_memory_index:
                del self.unified_memory_index[memory_id]
            
            # Clean up mode-specific storage
            if memory_mode in self.mode_specific_memories:
                if memory_id in self.mode_specific_memories[memory_mode]:
                    del self.mode_specific_memories[memory_mode][memory_id]
            
            result['deleted_from_mode'] = memory_mode
            logger.info(f"Deleted memory {memory_id} from {memory_mode} mode")
            return result
            
        except Exception as e:
            logger.error(f"Failed to delete memory {memory_id}: {e}", exc_info=True)
            raise
    
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
            memory_id: ID of the memory to share
            from_agent: ID of the agent/user sharing the memory
            to_agents: List of agent/user IDs to share with
            permissions: Optional list of permissions to grant
            
        Returns:
            Dictionary containing the sharing result
        """
        try:
            # Find which mode the memory belongs to
            memory_mode = self._get_memory_mode(memory_id)
            
            if not memory_mode:
                raise ValueError(f"Memory {memory_id} not found in any mode")
            
            # Share with appropriate manager
            if memory_mode == "multi_agent":
                result = self.multi_agent_manager.share_memory(memory_id, from_agent, to_agents, permissions)
            else:
                result = self.multi_user_manager.share_memory(memory_id, from_agent, to_agents, permissions)
            
            result['shared_from_mode'] = memory_mode
            logger.info(f"Shared memory {memory_id} from {memory_mode} mode")
            return result
            
        except Exception as e:
            logger.error(f"Failed to share memory {memory_id}: {e}", exc_info=True)
            raise
    
    def get_context_info(self, agent_id: str) -> Dict[str, Any]:
        """
        Get context information for an agent/user from both modes.
        
        Args:
            agent_id: ID of the agent/user
            
        Returns:
            Dictionary containing context information from both modes
        """
        try:
            # Get context from both modes
            multi_agent_context = self.multi_agent_manager.get_context_info(agent_id)
            multi_user_context = self.multi_user_manager.get_context_info(agent_id)
            
            # Combine context information
            combined_context = {
                'agent_id': agent_id,
                'current_mode': self.current_mode,
                'mode_switch_history': self.mode_switch_history[-5:],  # Last 5 switches
                'multi_agent_context': multi_agent_context,
                'multi_user_context': multi_user_context,
                'hybrid_stats': {
                    'total_memories': len(self.unified_memory_index),
                    'mode_breakdown': {
                        'multi_agent': len(self.mode_specific_memories.get('multi_agent', {})),
                        'multi_user': len(self.mode_specific_memories.get('multi_user', {})),
                    },
                    'switch_count': len(self.mode_switch_history),
                },
            }
            
            return combined_context
            
        except Exception as e:
            logger.error(f"Failed to get context info for {agent_id}: {e}", exc_info=True)
            raise
    
    def update_memory_decay(self) -> Dict[str, Any]:
        """
        Update memory decay for both modes.
        
        Returns:
            Dictionary containing the decay update results
        """
        try:
            # Update decay for both modes
            multi_agent_decay = self.multi_agent_manager.update_memory_decay()
            multi_user_decay = self.multi_user_manager.update_memory_decay()
            
            # Combine results
            combined_decay = {
                'multi_agent_decay': multi_agent_decay,
                'multi_user_decay': multi_user_decay,
                'total_updated': multi_agent_decay.get('updated_memories', 0) + multi_user_decay.get('updated_memories', 0),
                'total_forgotten': multi_agent_decay.get('forgotten_memories', 0) + multi_user_decay.get('forgotten_memories', 0),
                'total_reinforced': multi_agent_decay.get('reinforced_memories', 0) + multi_user_decay.get('reinforced_memories', 0),
            }
            
            logger.info(f"Updated memory decay for both modes: {combined_decay}")
            return combined_decay
            
        except Exception as e:
            logger.error(f"Failed to update memory decay: {e}", exc_info=True)
            raise
    
    def cleanup_forgotten_memories(self) -> Dict[str, Any]:
        """
        Clean up forgotten memories from both modes.
        
        Returns:
            Dictionary containing the cleanup results
        """
        try:
            # Clean up both modes
            multi_agent_cleanup = self.multi_agent_manager.cleanup_forgotten_memories()
            multi_user_cleanup = self.multi_user_manager.cleanup_forgotten_memories()
            
            # Combine results
            combined_cleanup = {
                'multi_agent_cleanup': multi_agent_cleanup,
                'multi_user_cleanup': multi_user_cleanup,
                'total_cleaned': multi_agent_cleanup.get('cleaned_memories', 0) + multi_user_cleanup.get('cleaned_memories', 0),
                'total_archived': multi_agent_cleanup.get('archived_memories', 0) + multi_user_cleanup.get('archived_memories', 0),
                'total_deleted': multi_agent_cleanup.get('deleted_memories', 0) + multi_user_cleanup.get('deleted_memories', 0),
            }
            
            # Clean up unified index
            cleaned_memory_ids = set()
            for cleanup_result in [multi_agent_cleanup, multi_user_cleanup]:
                if 'cleaned_memory_ids' in cleanup_result:
                    cleaned_memory_ids.update(cleanup_result['cleaned_memory_ids'])
            
            for memory_id in cleaned_memory_ids:
                if memory_id in self.unified_memory_index:
                    del self.unified_memory_index[memory_id]
            
            logger.info(f"Cleaned up forgotten memories from both modes: {combined_cleanup}")
            return combined_cleanup
            
        except Exception as e:
            logger.error(f"Failed to cleanup forgotten memories: {e}", exc_info=True)
            raise
    
    def get_memory_statistics(self) -> Dict[str, Any]:
        """
        Get statistics about the memory system from both modes.
        
        Returns:
            Dictionary containing memory statistics
        """
        try:
            # Get statistics from both modes
            multi_agent_stats = self.multi_agent_manager.get_memory_statistics()
            multi_user_stats = self.multi_user_manager.get_memory_statistics()
            
            # Combine statistics
            combined_stats = {
                'current_mode': self.current_mode,
                'mode_switch_history': len(self.mode_switch_history),
                'unified_memory_count': len(self.unified_memory_index),
                'multi_agent_stats': multi_agent_stats,
                'multi_user_stats': multi_user_stats,
                'hybrid_stats': {
                    'total_memories': multi_agent_stats.get('total_memories', 0) + multi_user_stats.get('total_memories', 0),
                    'mode_breakdown': {
                        'multi_agent': multi_agent_stats.get('total_memories', 0),
                        'multi_user': multi_user_stats.get('total_memories', 0),
                    },
                    'switch_frequency': len(self.mode_switch_history) / max(1, (datetime.now() - datetime.fromisoformat(self.mode_switch_history[0]['timestamp'])).days) if self.mode_switch_history else 0,
                },
            }
            
            return combined_stats
            
        except Exception as e:
            logger.error(f"Failed to get memory statistics: {e}", exc_info=True)
            raise
    
    def check_permission(
        self,
        agent_id: str,
        memory_id: str,
        permission: str
    ) -> bool:
        """
        Check if an agent/user has a specific permission for a memory.
        
        Args:
            agent_id: ID of the agent/user
            memory_id: ID of the memory
            permission: Permission to check
            
        Returns:
            True if the agent/user has the permission, False otherwise
        """
        try:
            # Find which mode the memory belongs to
            memory_mode = self._get_memory_mode(memory_id)
            
            if not memory_mode:
                return False
            
            # Check permission with appropriate manager
            if memory_mode == "multi_agent":
                return self.multi_agent_manager.check_permission(agent_id, memory_id, permission)
            else:
                return self.multi_user_manager.check_permission(agent_id, memory_id, permission)
                
        except Exception:
            return False
    
    def _get_memory_mode(self, memory_id: str) -> Optional[str]:
        """Get the mode that a memory belongs to."""
        # Check unified index first
        if memory_id in self.unified_memory_index:
            return self.unified_memory_index[memory_id]['mode']
        
        # Check mode-specific storage
        for mode, memories in self.mode_specific_memories.items():
            if memory_id in memories:
                return mode
        
        return None
    
    def get_mode_info(self) -> Dict[str, Any]:
        """
        Get information about the current mode and switching history.
        
        Returns:
            Dictionary containing mode information
        """
        return {
            'current_mode': self.current_mode,
            'primary_mode': self.hybrid_config.primary_mode,
            'fallback_mode': self.hybrid_config.fallback_mode,
            'auto_switch_enabled': self.hybrid_config.mode_switching_config.get('enable_auto_switch', True),
            'switch_history': self.mode_switch_history[-10:],  # Last 10 switches
            'cooldown_status': {
                mode: self._is_in_cooldown(mode) 
                for mode in ['multi_agent', 'multi_user']
            },
            'confidence_threshold': self.hybrid_config.mode_switching_config.get('confidence_threshold', 0.7),
        }
