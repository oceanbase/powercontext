"""
Multi-Agent Collaboration Coordinator

Manages collaboration and coordination between agents in the memory system.
Handles collaborative memory creation, conflict resolution, and consensus.
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional
import uuid

from typing import Any, Dict
from powermem.agent.types import CollaborationType, CollaborationStatus
from powermem.agent.abstract.collaboration import AgentCollaborationManagerBase

logger = logging.getLogger(__name__)


class CollaborationCoordinator(AgentCollaborationManagerBase):
    """
    Multi-agent collaboration coordinator implementation.
    
    Manages collaboration and coordination between agents, handles
    collaborative memory creation, conflict resolution, and consensus.
    """
    
    def __init__(self, config: Dict[str, Any]):
        """
        Initialize the collaboration coordinator.
        
        Args:
            config: Memory configuration object
        """
        super().__init__(config.agent_memory.multi_agent_config.collaborative_memory_config)
        self.config = config
        self.multi_agent_config = config.agent_memory.multi_agent_config
        
        # Collaboration storage
        self.collaborations: Dict[str, Dict[str, Any]] = {}
        self.collaboration_memories: Dict[str, List[str]] = {}
        self.collaboration_participants: Dict[str, List[str]] = {}
        
        # Conflict resolution
        self.conflicts: Dict[str, Dict[str, Any]] = {}
        self.resolutions: Dict[str, Dict[str, Any]] = {}
        
        # Consensus tracking
        self.consensus_votes: Dict[str, Dict[str, Any]] = {}
        self.consensus_results: Dict[str, Dict[str, Any]] = {}
        
        # Initialize settings immediately
        self._initialize_collaboration_settings()
    
    def initialize(self) -> None:
        """
        Initialize the collaboration coordinator.
        """
        try:
            # Initialize collaboration settings
            self._initialize_collaboration_settings()
            self.initialized = True
            logger.info("Collaboration coordinator initialized successfully")
            
        except Exception as e:
            logger.error(f"Failed to initialize collaboration coordinator: {e}", exc_info=True)
            raise
    
    def _initialize_collaboration_settings(self) -> None:
        """Initialize collaboration settings from configuration."""
        self.collaboration_config = self.multi_agent_config.collaborative_memory_config
        self.max_participants = self.collaboration_config.get('max_participants', 10)
        self.consensus_required = self.collaboration_config.get('consensus_required', True)
        self.voting_threshold = self.collaboration_config.get('voting_threshold', 0.6)
        self.merge_strategy = self.collaboration_config.get('merge_strategy', 'weighted_average')
        self.conflict_resolution = self.collaboration_config.get('conflict_resolution', 'llm_arbitration')
        
        # Initialize collaboration storage
        self.collaborations: Dict[str, Dict[str, Any]] = {}
        self.collaboration_memories: Dict[str, List[str]] = {}
        self.collaboration_participants: Dict[str, List[str]] = {}
        self.conflicts: Dict[str, Dict[str, Any]] = {}
        self.resolutions: Dict[str, Dict[str, Any]] = {}
        self.consensus_votes: Dict[str, Dict[str, Any]] = {}
        self.consensus_results: Dict[str, Dict[str, Any]] = {}
    
    def initiate_collaboration(
        self,
        initiator_id: str,
        participant_ids: List[str],
        collaboration_type: CollaborationType,
        context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Initiate a collaboration between agents.
        
        Args:
            initiator_id: ID of the agent initiating the collaboration
            participant_ids: List of agent IDs participating in the collaboration
            collaboration_type: Type of collaboration
            context: Optional context information
            
        Returns:
            Dictionary containing the collaboration initiation result
        """
        try:
            # Validate participants
            if len(participant_ids) > self.max_participants:
                raise ValueError(f"Too many participants. Maximum allowed: {self.max_participants}")
            
            # Create collaboration ID
            collaboration_id = str(uuid.uuid4())
            
            # Create collaboration record
            collaboration = {
                'id': collaboration_id,
                'initiator_id': initiator_id,
                'participants': [initiator_id] + participant_ids,
                'collaboration_type': collaboration_type.value,
                'status': CollaborationStatus.INITIATED.value,
                'context': context or {},
                'created_at': datetime.now().isoformat(),
                'updated_at': datetime.now().isoformat(),
                'memories': [],
                'conflicts': [],
                'consensus_votes': {},
            }
            
            # Store collaboration
            self.collaborations[collaboration_id] = collaboration
            self.collaboration_participants[collaboration_id] = collaboration['participants']
            self.collaboration_memories[collaboration_id] = []
            
            logger.info(f"Initiated collaboration {collaboration_id} with {len(collaboration['participants'])} participants")
            
            return {
                'success': True,
                'collaboration_id': collaboration_id,
                'participants': collaboration['participants'],
                'collaboration_type': collaboration_type.value,
                'status': CollaborationStatus.INITIATED.value,
            }
            
        except Exception as e:
            logger.error(f"Failed to initiate collaboration: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e),
                'initiator_id': initiator_id,
            }
    
    def join_collaboration(
        self,
        collaboration_id: str,
        agent_id: str
    ) -> Dict[str, Any]:
        """
        Join an existing collaboration.
        
        Args:
            collaboration_id: ID of the collaboration
            agent_id: ID of the agent joining
            
        Returns:
            Dictionary containing the join result
        """
        try:
            if collaboration_id not in self.collaborations:
                raise ValueError(f"Collaboration {collaboration_id} not found")
            
            collaboration = self.collaborations[collaboration_id]
            
            # Check if agent is already a participant
            if agent_id in collaboration['participants']:
                return {
                    'success': True,
                    'collaboration_id': collaboration_id,
                    'agent_id': agent_id,
                    'message': 'Agent is already a participant',
                }
            
            # Check participant limit
            if len(collaboration['participants']) >= self.max_participants:
                raise ValueError(f"Collaboration has reached maximum participants: {self.max_participants}")
            
            # Add agent to collaboration
            collaboration['participants'].append(agent_id)
            collaboration['updated_at'] = datetime.now().isoformat()
            
            # Update participants tracking
            self.collaboration_participants[collaboration_id] = collaboration['participants']
            
            logger.info(f"Agent {agent_id} joined collaboration {collaboration_id}")
            
            return {
                'success': True,
                'collaboration_id': collaboration_id,
                'agent_id': agent_id,
                'participants': collaboration['participants'],
            }
            
        except Exception as e:
            logger.error(f"Failed to join collaboration: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e),
                'collaboration_id': collaboration_id,
                'agent_id': agent_id,
            }
    
    def leave_collaboration(
        self,
        collaboration_id: str,
        agent_id: str
    ) -> Dict[str, Any]:
        """
        Leave a collaboration.
        
        Args:
            collaboration_id: ID of the collaboration
            agent_id: ID of the agent leaving
            
        Returns:
            Dictionary containing the leave result
        """
        try:
            if collaboration_id not in self.collaborations:
                raise ValueError(f"Collaboration {collaboration_id} not found")
            
            collaboration = self.collaborations[collaboration_id]
            
            # Check if agent is a participant
            if agent_id not in collaboration['participants']:
                return {
                    'success': False,
                    'error': 'Agent is not a participant in this collaboration',
                    'collaboration_id': collaboration_id,
                    'agent_id': agent_id,
                }
            
            # Remove agent from collaboration
            collaboration['participants'].remove(agent_id)
            collaboration['updated_at'] = datetime.now().isoformat()
            
            # Update participants tracking
            self.collaboration_participants[collaboration_id] = collaboration['participants']
            
            # If no participants left, mark collaboration as completed
            if not collaboration['participants']:
                collaboration['status'] = CollaborationStatus.COMPLETED.value
            
            logger.info(f"Agent {agent_id} left collaboration {collaboration_id}")
            
            return {
                'success': True,
                'collaboration_id': collaboration_id,
                'agent_id': agent_id,
                'participants': collaboration['participants'],
            }
            
        except Exception as e:
            logger.error(f"Failed to leave collaboration: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e),
                'collaboration_id': collaboration_id,
                'agent_id': agent_id,
            }
    
    def get_collaboration_status(
        self,
        collaboration_id: str
    ) -> CollaborationStatus:
        """
        Get the status of a collaboration.
        
        Args:
            collaboration_id: ID of the collaboration
            
        Returns:
            CollaborationStatus enum value
        """
        try:
            if collaboration_id not in self.collaborations:
                return CollaborationStatus.FAILED
            
            status_str = self.collaborations[collaboration_id]['status']
            return CollaborationStatus(status_str)
            
        except Exception as e:
            logger.error(f"Failed to get collaboration status: {e}", exc_info=True)
            return CollaborationStatus.FAILED
    
    def update_collaboration_status(
        self,
        collaboration_id: str,
        status: CollaborationStatus,
        updated_by: str
    ) -> Dict[str, Any]:
        """
        Update the status of a collaboration.
        
        Args:
            collaboration_id: ID of the collaboration
            status: New status
            updated_by: ID of the agent updating the status
            
        Returns:
            Dictionary containing the update result
        """
        try:
            if collaboration_id not in self.collaborations:
                raise ValueError(f"Collaboration {collaboration_id} not found")
            
            collaboration = self.collaborations[collaboration_id]
            
            # Check if agent is a participant
            if updated_by not in collaboration['participants']:
                raise PermissionError(f"Agent {updated_by} is not a participant in this collaboration")
            
            # Update status
            old_status = collaboration['status']
            collaboration['status'] = status.value
            collaboration['updated_at'] = datetime.now().isoformat()
            collaboration['updated_by'] = updated_by
            
            logger.info(f"Updated collaboration {collaboration_id} status from {old_status} to {status.value}")
            
            return {
                'success': True,
                'collaboration_id': collaboration_id,
                'old_status': old_status,
                'new_status': status.value,
                'updated_by': updated_by,
            }
            
        except Exception as e:
            logger.error(f"Failed to update collaboration status: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e),
                'collaboration_id': collaboration_id,
            }
    
    def get_collaboration_participants(
        self,
        collaboration_id: str
    ) -> List[str]:
        """
        Get list of participants in a collaboration.
        
        Args:
            collaboration_id: ID of the collaboration
            
        Returns:
            List of agent IDs participating in the collaboration
        """
        try:
            if collaboration_id not in self.collaboration_participants:
                return []
            
            return self.collaboration_participants[collaboration_id].copy()
            
        except Exception as e:
            logger.error(f"Failed to get collaboration participants: {e}", exc_info=True)
            return []
    
    def share_memory_in_collaboration(
        self,
        collaboration_id: str,
        memory_id: str,
        shared_by: str
    ) -> Dict[str, Any]:
        """
        Share a memory within a collaboration.
        
        Args:
            collaboration_id: ID of the collaboration
            memory_id: ID of the memory to share
            shared_by: ID of the agent sharing the memory
            
        Returns:
            Dictionary containing the share result
        """
        try:
            if collaboration_id not in self.collaborations:
                raise ValueError(f"Collaboration {collaboration_id} not found")
            
            collaboration = self.collaborations[collaboration_id]
            
            # Check if agent is a participant
            if shared_by not in collaboration['participants']:
                raise PermissionError(f"Agent {shared_by} is not a participant in this collaboration")
            
            # Add memory to collaboration
            if memory_id not in collaboration['memories']:
                collaboration['memories'].append(memory_id)
                collaboration['updated_at'] = datetime.now().isoformat()
                
                # Update collaboration memories tracking
                self.collaboration_memories[collaboration_id] = collaboration['memories']
            
            logger.info(f"Shared memory {memory_id} in collaboration {collaboration_id}")
            
            return {
                'success': True,
                'collaboration_id': collaboration_id,
                'memory_id': memory_id,
                'shared_by': shared_by,
                'participants': collaboration['participants'],
            }
            
        except Exception as e:
            logger.error(f"Failed to share memory in collaboration: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e),
                'collaboration_id': collaboration_id,
                'memory_id': memory_id,
            }
    
    def get_collaboration_memories(
        self,
        collaboration_id: str,
        agent_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Get memories shared in a collaboration.
        
        Args:
            collaboration_id: ID of the collaboration
            agent_id: Optional ID of the agent requesting
            
        Returns:
            List of memory dictionaries
        """
        try:
            if collaboration_id not in self.collaborations:
                return []
            
            collaboration = self.collaborations[collaboration_id]
            
            # Check if agent is a participant
            if agent_id and agent_id not in collaboration['participants']:
                return []
            
            # Return memory IDs (actual memory data would be retrieved from memory manager)
            memories = []
            for memory_id in collaboration['memories']:
                memories.append({
                    'id': memory_id,
                    'collaboration_id': collaboration_id,
                    'shared_in_collaboration': True,
                })
            
            return memories
            
        except Exception as e:
            logger.error(f"Failed to get collaboration memories: {e}", exc_info=True)
            return []
    
    def resolve_collaboration_conflict(
        self,
        collaboration_id: str,
        conflict_data: Dict[str, Any],
        resolver_id: str
    ) -> Dict[str, Any]:
        """
        Resolve a conflict in a collaboration.
        
        Args:
            collaboration_id: ID of the collaboration
            conflict_data: Data about the conflict
            resolver_id: ID of the agent resolving the conflict
            
        Returns:
            Dictionary containing the resolution result
        """
        try:
            if collaboration_id not in self.collaborations:
                raise ValueError(f"Collaboration {collaboration_id} not found")
            
            collaboration = self.collaborations[collaboration_id]
            
            # Check if agent is a participant
            if resolver_id not in collaboration['participants']:
                raise PermissionError(f"Agent {resolver_id} is not a participant in this collaboration")
            
            # Create conflict record
            conflict_id = str(uuid.uuid4())
            conflict = {
                'id': conflict_id,
                'collaboration_id': collaboration_id,
                'conflict_data': conflict_data,
                'resolver_id': resolver_id,
                'resolved_at': datetime.now().isoformat(),
                'resolution_method': self.conflict_resolution,
            }
            
            # Store conflict and resolution
            self.conflicts[conflict_id] = conflict
            self.resolutions[conflict_id] = conflict
            
            # Add to collaboration conflicts
            collaboration['conflicts'].append(conflict_id)
            collaboration['updated_at'] = datetime.now().isoformat()
            
            logger.info(f"Resolved conflict {conflict_id} in collaboration {collaboration_id}")
            
            return {
                'success': True,
                'conflict_id': conflict_id,
                'collaboration_id': collaboration_id,
                'resolver_id': resolver_id,
                'resolution_method': self.conflict_resolution,
            }
            
        except Exception as e:
            logger.error(f"Failed to resolve collaboration conflict: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e),
                'collaboration_id': collaboration_id,
            }
    
    def get_collaboration_history(
        self,
        collaboration_id: str,
        limit: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        Get history of a collaboration.
        
        Args:
            collaboration_id: ID of the collaboration
            limit: Optional limit on number of entries
            
        Returns:
            List of collaboration history entries
        """
        try:
            if collaboration_id not in self.collaborations:
                return []
            
            collaboration = self.collaborations[collaboration_id]
            
            # Build history from collaboration data
            history = [
                {
                    'timestamp': collaboration['created_at'],
                    'event': 'collaboration_initiated',
                    'agent_id': collaboration['initiator_id'],
                    'data': {
                        'participants': collaboration['participants'],
                        'collaboration_type': collaboration['collaboration_type'],
                    },
                },
                {
                    'timestamp': collaboration['updated_at'],
                    'event': 'collaboration_updated',
                    'agent_id': collaboration.get('updated_by'),
                    'data': {
                        'status': collaboration['status'],
                    },
                },
            ]
            
            # Add memory sharing events
            for memory_id in collaboration['memories']:
                history.append({
                    'timestamp': collaboration['updated_at'],
                    'event': 'memory_shared',
                    'data': {
                        'memory_id': memory_id,
                    },
                })
            
            # Add conflict resolution events
            for conflict_id in collaboration['conflicts']:
                if conflict_id in self.resolutions:
                    resolution = self.resolutions[conflict_id]
                    history.append({
                        'timestamp': resolution['resolved_at'],
                        'event': 'conflict_resolved',
                        'agent_id': resolution['resolver_id'],
                        'data': {
                            'conflict_id': conflict_id,
                            'resolution_method': resolution['resolution_method'],
                        },
                    })
            
            # Sort by timestamp
            history.sort(key=lambda x: x['timestamp'])
            
            # Apply limit if specified
            if limit:
                history = history[-limit:]
            
            return history
            
        except Exception as e:
            logger.error(f"Failed to get collaboration history: {e}", exc_info=True)
            return []
    
    def get_agent_collaborations(
        self,
        agent_id: str,
        status: Optional[CollaborationStatus] = None
    ) -> List[Dict[str, Any]]:
        """
        Get collaborations for an agent.
        
        Args:
            agent_id: ID of the agent
            status: Optional status filter
            
        Returns:
            List of collaboration dictionaries
        """
        try:
            agent_collaborations = []
            
            for collaboration_id, collaboration in self.collaborations.items():
                if agent_id in collaboration['participants']:
                    # Apply status filter if specified
                    if status and CollaborationStatus(collaboration['status']) != status:
                        continue
                    
                    agent_collaborations.append({
                        'collaboration_id': collaboration_id,
                        'initiator_id': collaboration['initiator_id'],
                        'participants': collaboration['participants'],
                        'collaboration_type': collaboration['collaboration_type'],
                        'status': collaboration['status'],
                        'created_at': collaboration['created_at'],
                        'updated_at': collaboration['updated_at'],
                        'memory_count': len(collaboration['memories']),
                        'conflict_count': len(collaboration['conflicts']),
                    })
            
            return agent_collaborations
            
        except Exception as e:
            logger.error(f"Failed to get agent collaborations: {e}", exc_info=True)
            return []
