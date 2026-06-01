"""
Multi-Agent Permission Controller

Manages access permissions for memories across different agents and scopes.
Implements role-based and attribute-based access control. Migrated and
refactored from the original multi_agent implementation.
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

from typing import Any, Dict
from powermem.agent.types import AccessPermission
from powermem.agent.abstract.permission import AgentPermissionManagerBase

logger = logging.getLogger(__name__)


class PermissionController(AgentPermissionManagerBase):
    """
    Multi-agent permission controller implementation.
    
    Manages access permissions for memories, implements RBAC and ABAC,
    handles permission grants, revocations, and audits.
    """
    
    def __init__(self, config: Dict[str, Any]):
        """
        Initialize the permission controller.
        
        Args:
            config: Memory configuration object
        """
        super().__init__(config.agent_memory.multi_agent_config.__dict__)
        self.config = config
        self.multi_agent_config = config.agent_memory.multi_agent_config
        
        # Permission storage: memory_id -> agent_id -> permissions
        self.memory_permissions: Dict[str, Dict[str, Set[AccessPermission]]] = {}
        
        # Role-based permissions
        self.role_permissions = self.multi_agent_config.default_permissions
        
        # Access audit log
        self.access_log: List[Dict[str, Any]] = []
        
        # Agent roles mapping
        self.agent_roles: Dict[str, List[str]] = {}
        
        # Initialize default permissions
        self._initialize_default_permissions()
    
    def initialize(self) -> None:
        """
        Initialize the permission controller.
        """
        try:
            # Initialize default permissions
            self._initialize_default_permissions()
            
            # Initialize agent roles
            self._initialize_agent_roles()
            
            self.initialized = True
            logger.info("Permission controller initialized successfully")
            
        except Exception as e:
            logger.error(f"Failed to initialize permission controller: {e}", exc_info=True)
            raise
    
    def _initialize_default_permissions(self) -> None:
        """Initialize default permissions from configuration."""
        # Default permissions are already loaded from config
        pass
    
    def _initialize_agent_roles(self) -> None:
        """Initialize agent roles from configuration."""
        # Initialize agent roles from config if available
        if hasattr(self.multi_agent_config, 'agent_groups'):
            for group_name, group_config in self.multi_agent_config.agent_groups.items():
                for agent_id in group_config.get('members', []):
                    if agent_id not in self.agent_roles:
                        self.agent_roles[agent_id] = []
                    self.agent_roles[agent_id].append(group_name)
    
    def check_permission(
        self,
        agent_id: str,
        memory_id: str,
        permission: AccessPermission
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
            # Check direct memory permissions
            if memory_id in self.memory_permissions:
                if agent_id in self.memory_permissions[memory_id]:
                    if permission in self.memory_permissions[memory_id][agent_id]:
                        self._log_access(agent_id, memory_id, permission, "granted")
                        return True
            
            # Check role-based permissions
            if agent_id in self.agent_roles:
                for role in self.agent_roles[agent_id]:
                    if role in self.role_permissions:
                        if permission in self.role_permissions[role]:
                            self._log_access(agent_id, memory_id, permission, "granted_by_role")
                            return True
            
            # Check default permissions
            if "public" in self.role_permissions:
                if permission in self.role_permissions["public"]:
                    self._log_access(agent_id, memory_id, permission, "granted_by_default")
                    return True
            
            # Log detailed debug information
            logger.debug(f"Permission check failed for agent {agent_id} on memory {memory_id}: "
                        f"memory_permissions={memory_id in self.memory_permissions}, "
                        f"agent_roles={agent_id in self.agent_roles}, "
                        f"permission={permission.value}")
            
            self._log_access(agent_id, memory_id, permission, "denied")
            return False
            
        except Exception as e:
            logger.error(f"Error checking permission: {e}", exc_info=True)
            return False
    
    def grant_permission(
        self,
        memory_id: str,
        agent_id: str,
        permission: AccessPermission,
        granted_by: str
    ) -> Dict[str, Any]:
        """
        Grant a permission to an agent for a memory.
        
        Args:
            memory_id: ID of the memory
            agent_id: ID of the agent to grant permission to
            permission: Permission to grant
            granted_by: ID of the agent granting the permission
            
        Returns:
            Dictionary containing the grant result
        """
        try:
            # Initialize memory permissions if not exists
            if memory_id not in self.memory_permissions:
                self.memory_permissions[memory_id] = {}
            
            if agent_id not in self.memory_permissions[memory_id]:
                self.memory_permissions[memory_id][agent_id] = set()
            
            # Grant permission
            self.memory_permissions[memory_id][agent_id].add(permission)
            
            # Log the grant
            self._log_permission_change(
                memory_id, agent_id, permission, "granted", granted_by
            )
            
            logger.info(f"Granted {permission.value} permission to {agent_id} for memory {memory_id}")
            
            return {
                'success': True,
                'memory_id': memory_id,
                'agent_id': agent_id,
                'permission': permission.value,
                'granted_by': granted_by,
                'granted_at': datetime.now().isoformat(),
            }
            
        except Exception as e:
            logger.error(f"Error granting permission: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e),
                'memory_id': memory_id,
                'agent_id': agent_id,
            }
    
    def revoke_permission(
        self,
        memory_id: str,
        agent_id: str,
        permission: AccessPermission,
        revoked_by: str
    ) -> Dict[str, Any]:
        """
        Revoke a permission from an agent for a memory.
        
        Args:
            memory_id: ID of the memory
            agent_id: ID of the agent to revoke permission from
            permission: Permission to revoke
            revoked_by: ID of the agent revoking the permission
            
        Returns:
            Dictionary containing the revoke result
        """
        try:
            # Check if memory and agent exist
            if memory_id not in self.memory_permissions:
                return {
                    'success': False,
                    'error': 'Memory not found',
                    'memory_id': memory_id,
                }
            
            if agent_id not in self.memory_permissions[memory_id]:
                return {
                    'success': False,
                    'error': 'Agent not found for this memory',
                    'memory_id': memory_id,
                    'agent_id': agent_id,
                }
            
            # Revoke permission
            if permission in self.memory_permissions[memory_id][agent_id]:
                self.memory_permissions[memory_id][agent_id].remove(permission)
                
                # Log the revocation
                self._log_permission_change(
                    memory_id, agent_id, permission, "revoked", revoked_by
                )
                
                logger.info(f"Revoked {permission.value} permission from {agent_id} for memory {memory_id}")
                
                return {
                    'success': True,
                    'memory_id': memory_id,
                    'agent_id': agent_id,
                    'permission': permission.value,
                    'revoked_by': revoked_by,
                    'revoked_at': datetime.now().isoformat(),
                }
            else:
                return {
                    'success': False,
                    'error': 'Permission not found',
                    'memory_id': memory_id,
                    'agent_id': agent_id,
                    'permission': permission.value,
                }
            
        except Exception as e:
            logger.error(f"Error revoking permission: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e),
                'memory_id': memory_id,
                'agent_id': agent_id,
            }
    
    def get_permissions(
        self,
        memory_id: str,
        agent_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get permissions for a memory or agent.
        
        Args:
            memory_id: ID of the memory
            agent_id: Optional ID of the agent
            
        Returns:
            Dictionary containing permission information
        """
        try:
            if agent_id:
                # Get permissions for specific agent
                if memory_id in self.memory_permissions:
                    if agent_id in self.memory_permissions[memory_id]:
                        permissions = [
                            perm.value for perm in self.memory_permissions[memory_id][agent_id]
                        ]
                        return {
                            'memory_id': memory_id,
                            'agent_id': agent_id,
                            'permissions': permissions,
                            'permission_count': len(permissions),
                        }
                
                return {
                    'memory_id': memory_id,
                    'agent_id': agent_id,
                    'permissions': [],
                    'permission_count': 0,
                }
            else:
                # Get all permissions for memory
                if memory_id in self.memory_permissions:
                    all_permissions = {}
                    for agent_id, permissions in self.memory_permissions[memory_id].items():
                        all_permissions[agent_id] = [perm.value for perm in permissions]
                    
                    return {
                        'memory_id': memory_id,
                        'all_permissions': all_permissions,
                        'agent_count': len(all_permissions),
                    }
                
                return {
                    'memory_id': memory_id,
                    'all_permissions': {},
                    'agent_count': 0,
                }
            
        except Exception as e:
            logger.error(f"Error getting permissions: {e}", exc_info=True)
            return {
                'memory_id': memory_id,
                'error': str(e),
            }
    
    def set_default_permissions(
        self,
        memory_id: str,
        permissions: Dict[str, List[AccessPermission]],
        set_by: str
    ) -> Dict[str, Any]:
        """
        Set default permissions for a memory.
        
        Args:
            memory_id: ID of the memory
            permissions: Dictionary of default permissions
            set_by: ID of the agent setting the permissions
            
        Returns:
            Dictionary containing the set result
        """
        try:
            # Initialize memory permissions if not exists
            if memory_id not in self.memory_permissions:
                self.memory_permissions[memory_id] = {}
            
            # Set default permissions
            for agent_id, agent_permissions in permissions.items():
                if agent_id not in self.memory_permissions[memory_id]:
                    self.memory_permissions[memory_id][agent_id] = set()
                
                self.memory_permissions[memory_id][agent_id].update(agent_permissions)
            
            # Log the setting
            self._log_permission_change(
                memory_id, "default", AccessPermission.ADMIN, "set_default", set_by
            )
            
            logger.info(f"Set default permissions for memory {memory_id}")
            
            return {
                'success': True,
                'memory_id': memory_id,
                'permissions': {k: [p.value for p in v] for k, v in permissions.items()},
                'set_by': set_by,
                'set_at': datetime.now().isoformat(),
            }
            
        except Exception as e:
            logger.error(f"Error setting default permissions: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e),
                'memory_id': memory_id,
            }
    
    def inherit_permissions(
        self,
        target_memory_id: str,
        source_memory_id: str,
        inherited_by: str
    ) -> Dict[str, Any]:
        """
        Inherit permissions from another memory.
        
        Args:
            target_memory_id: ID of the target memory
            source_memory_id: ID of the source memory
            inherited_by: ID of the agent inheriting the permissions
            
        Returns:
            Dictionary containing the inherit result
        """
        try:
            # Check if source memory exists
            if source_memory_id not in self.memory_permissions:
                return {
                    'success': False,
                    'error': 'Source memory not found',
                    'source_memory_id': source_memory_id,
                }
            
            # Initialize target memory permissions if not exists
            if target_memory_id not in self.memory_permissions:
                self.memory_permissions[target_memory_id] = {}
            
            # Copy permissions from source to target
            source_permissions = self.memory_permissions[source_memory_id]
            inherited_count = 0
            
            for agent_id, permissions in source_permissions.items():
                if agent_id not in self.memory_permissions[target_memory_id]:
                    self.memory_permissions[target_memory_id][agent_id] = set()
                
                self.memory_permissions[target_memory_id][agent_id].update(permissions)
                inherited_count += len(permissions)
            
            # Log the inheritance
            self._log_permission_change(
                target_memory_id, "inherited", AccessPermission.ADMIN, "inherited", inherited_by
            )
            
            logger.info(f"Inherited permissions from {source_memory_id} to {target_memory_id}")
            
            return {
                'success': True,
                'target_memory_id': target_memory_id,
                'source_memory_id': source_memory_id,
                'inherited_count': inherited_count,
                'inherited_by': inherited_by,
                'inherited_at': datetime.now().isoformat(),
            }
            
        except Exception as e:
            logger.error(f"Error inheriting permissions: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e),
                'target_memory_id': target_memory_id,
                'source_memory_id': source_memory_id,
            }
    
    def get_permission_history(
        self,
        memory_id: str,
        agent_id: Optional[str] = None,
        limit: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        Get permission history for a memory or agent.
        
        Args:
            memory_id: ID of the memory
            agent_id: Optional ID of the agent
            limit: Optional limit on number of entries
            
        Returns:
            List of permission history entries
        """
        try:
            history = []
            
            # Filter access log for this memory and agent
            for entry in self.access_log:
                if entry.get('memory_id') == memory_id:
                    if agent_id is None or entry.get('agent_id') == agent_id:
                        history.append(entry)
            
            # Sort by timestamp (newest first)
            history.sort(key=lambda x: x.get('timestamp', ''), reverse=True)
            
            # Apply limit if specified
            if limit:
                history = history[:limit]
            
            return history
            
        except Exception as e:
            logger.error(f"Error getting permission history: {e}", exc_info=True)
            return []
    
    def validate_permission_chain(
        self,
        agent_id: str,
        memory_id: str,
        permission: AccessPermission
    ) -> Dict[str, Any]:
        """
        Validate the permission chain for an agent and memory.
        
        Args:
            agent_id: ID of the agent
            memory_id: ID of the memory
            permission: Permission to validate
            
        Returns:
            Dictionary containing validation results
        """
        try:
            validation_result = {
                'valid': False,
                'agent_id': agent_id,
                'memory_id': memory_id,
                'permission': permission.value,
                'validation_path': [],
                'errors': [],
            }
            
            # Check direct memory permissions
            if memory_id in self.memory_permissions:
                if agent_id in self.memory_permissions[memory_id]:
                    if permission in self.memory_permissions[memory_id][agent_id]:
                        validation_result['valid'] = True
                        validation_result['validation_path'].append('direct_memory_permission')
                        return validation_result
            
            # Check role-based permissions
            if agent_id in self.agent_roles:
                for role in self.agent_roles[agent_id]:
                    if role in self.role_permissions:
                        if permission in self.role_permissions[role]:
                            validation_result['valid'] = True
                            validation_result['validation_path'].append(f'role_permission:{role}')
                            return validation_result
            
            # Check default permissions
            if "public" in self.role_permissions:
                if permission in self.role_permissions["public"]:
                    validation_result['valid'] = True
                    validation_result['validation_path'].append('default_permission')
                    return validation_result
            
            validation_result['errors'].append('No valid permission path found')
            return validation_result
            
        except Exception as e:
            logger.error(f"Error validating permission chain: {e}", exc_info=True)
            return {
                'valid': False,
                'error': str(e),
                'agent_id': agent_id,
                'memory_id': memory_id,
                'permission': permission.value,
            }
    
    def _log_access(
        self,
        agent_id: str,
        memory_id: str,
        permission: AccessPermission,
        result: str
    ) -> None:
        """Log access attempt."""
        log_entry = {
            'timestamp': datetime.now().isoformat(),
            'agent_id': agent_id,
            'memory_id': memory_id,
            'permission': permission.value,
            'result': result,
            'type': 'access_check',
        }
        self.access_log.append(log_entry)
    
    def _log_permission_change(
        self,
        memory_id: str,
        agent_id: str,
        permission: AccessPermission,
        action: str,
        performed_by: str
    ) -> None:
        """Log permission change."""
        log_entry = {
            'timestamp': datetime.now().isoformat(),
            'memory_id': memory_id,
            'agent_id': agent_id,
            'permission': permission.value,
            'action': action,
            'performed_by': performed_by,
            'type': 'permission_change',
        }
        self.access_log.append(log_entry)
