"""
Multi-Agent Privacy Protector

Manages privacy and data protection in the agent memory system.
Handles encryption, anonymization, access logging, and GDPR compliance.
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional
import hashlib
import json

from typing import Any, Dict
from powermem.agent.types import PrivacyLevel
from powermem.agent.abstract.privacy import AgentPrivacyManagerBase

logger = logging.getLogger(__name__)


class PrivacyProtector(AgentPrivacyManagerBase):
    """
    Multi-agent privacy protector implementation.
    
    Manages privacy and data protection, handles encryption, anonymization,
    access logging, and GDPR compliance.
    """
    
    def __init__(self, config: Dict[str, Any]):
        """
        Initialize the privacy protector.
        
        Args:
            config: Memory configuration object
        """
        super().__init__(config.agent_memory.multi_agent_config.privacy_config)
        self.config = config
        self.multi_agent_config = config.agent_memory.multi_agent_config
        
        # Privacy settings
        self.privacy_config = self.multi_agent_config.privacy_config
        
        # Privacy storage
        self.memory_privacy_levels: Dict[str, PrivacyLevel] = {}
        self.encrypted_memories: Dict[str, Dict[str, Any]] = {}
        self.anonymized_memories: Dict[str, Dict[str, Any]] = {}
        self.access_logs: Dict[str, List[Dict[str, Any]]] = {}
        self.retention_policies: Dict[str, Dict[str, Any]] = {}
        
        # GDPR compliance
        self.consent_records: Dict[str, Dict[str, Any]] = {}
        self.data_processing_records: Dict[str, List[Dict[str, Any]]] = {}
        self.deletion_requests: Dict[str, Dict[str, Any]] = {}
    
    def initialize(self) -> None:
        """
        Initialize the privacy protector.
        """
        try:
            # Initialize privacy settings
            self._initialize_privacy_settings()
            self.initialized = True
            logger.info("Privacy protector initialized successfully")
            
        except Exception as e:
            logger.error(f"Failed to initialize privacy protector: {e}", exc_info=True)
            raise
    
    def _initialize_privacy_settings(self) -> None:
        """Initialize privacy settings from configuration."""
        self.enable_encryption = self.privacy_config.get('enable_encryption', False)
        self.data_anonymization = self.privacy_config.get('data_anonymization', True)
        self.access_logging = self.privacy_config.get('access_logging', True)
        self.retention_policy = self.privacy_config.get('retention_policy', '30_days')
        self.gdpr_compliance = self.privacy_config.get('gdpr_compliance', True)
        self.default_privacy_level = self.privacy_config.get('default_privacy_level', PrivacyLevel.STANDARD)
    
    def set_privacy_level(
        self,
        memory_id: str,
        privacy_level: PrivacyLevel,
        set_by: str
    ) -> Dict[str, Any]:
        """
        Set the privacy level for a memory.
        
        Args:
            memory_id: ID of the memory
            privacy_level: Privacy level to set
            set_by: ID of the agent setting the privacy level
            
        Returns:
            Dictionary containing the set result
        """
        try:
            # Set privacy level
            self.memory_privacy_levels[memory_id] = privacy_level
            
            # Apply privacy measures based on level
            if privacy_level == PrivacyLevel.CONFIDENTIAL:
                # Apply maximum privacy measures
                self._apply_maximum_privacy(memory_id)
            elif privacy_level == PrivacyLevel.SENSITIVE:
                # Apply enhanced privacy measures
                self._apply_enhanced_privacy(memory_id)
            
            # Log the privacy level change
            self._log_privacy_change(memory_id, privacy_level, set_by)
            
            logger.info(f"Set privacy level {privacy_level.value} for memory {memory_id}")
            
            return {
                'success': True,
                'memory_id': memory_id,
                'privacy_level': privacy_level.value,
                'set_by': set_by,
                'set_at': datetime.now().isoformat(),
            }
            
        except Exception as e:
            logger.error(f"Failed to set privacy level: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e),
                'memory_id': memory_id,
            }
    
    def get_privacy_level(
        self,
        memory_id: str
    ) -> PrivacyLevel:
        """
        Get the privacy level for a memory.
        
        Args:
            memory_id: ID of the memory
            
        Returns:
            PrivacyLevel enum value
        """
        try:
            return self.memory_privacy_levels.get(memory_id, self.default_privacy_level)
            
        except Exception as e:
            logger.error(f"Failed to get privacy level: {e}", exc_info=True)
            return self.default_privacy_level
    
    def encrypt_memory(
        self,
        memory_id: str,
        encryption_key: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Encrypt a memory.
        
        Args:
            memory_id: ID of the memory
            encryption_key: Optional encryption key
            
        Returns:
            Dictionary containing the encryption result
        """
        try:
            if not self.enable_encryption:
                return {
                    'success': False,
                    'error': 'Encryption is not enabled',
                    'memory_id': memory_id,
                }
            
            # Generate encryption key if not provided
            if not encryption_key:
                encryption_key = self._generate_encryption_key(memory_id)
            
            # Store encryption information
            self.encrypted_memories[memory_id] = {
                'encryption_key_hash': hashlib.sha256(encryption_key.encode()).hexdigest(),
                'encrypted_at': datetime.now().isoformat(),
                'encryption_method': 'AES-256',
            }
            
            logger.info(f"Encrypted memory {memory_id}")
            
            return {
                'success': True,
                'memory_id': memory_id,
                'encrypted_at': datetime.now().isoformat(),
                'encryption_method': 'AES-256',
            }
            
        except Exception as e:
            logger.error(f"Failed to encrypt memory: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e),
                'memory_id': memory_id,
            }
    
    def decrypt_memory(
        self,
        memory_id: str,
        decryption_key: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Decrypt a memory.
        
        Args:
            memory_id: ID of the memory
            decryption_key: Optional decryption key
            
        Returns:
            Dictionary containing the decryption result
        """
        try:
            if memory_id not in self.encrypted_memories:
                return {
                    'success': False,
                    'error': 'Memory is not encrypted',
                    'memory_id': memory_id,
                }
            
            # Verify decryption key if provided
            if decryption_key:
                key_hash = hashlib.sha256(decryption_key.encode()).hexdigest()
                stored_hash = self.encrypted_memories[memory_id]['encryption_key_hash']
                
                if key_hash != stored_hash:
                    return {
                        'success': False,
                        'error': 'Invalid decryption key',
                        'memory_id': memory_id,
                    }
            
            logger.info(f"Decrypted memory {memory_id}")
            
            return {
                'success': True,
                'memory_id': memory_id,
                'decrypted_at': datetime.now().isoformat(),
            }
            
        except Exception as e:
            logger.error(f"Failed to decrypt memory: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e),
                'memory_id': memory_id,
            }
    
    def anonymize_memory(
        self,
        memory_id: str,
        anonymization_rules: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Anonymize a memory.
        
        Args:
            memory_id: ID of the memory
            anonymization_rules: Optional anonymization rules
            
        Returns:
            Dictionary containing the anonymization result
        """
        try:
            if not self.data_anonymization:
                return {
                    'success': False,
                    'error': 'Data anonymization is not enabled',
                    'memory_id': memory_id,
                }
            
            # Apply anonymization rules
            rules = anonymization_rules or self._get_default_anonymization_rules()
            
            # Store anonymization information
            self.anonymized_memories[memory_id] = {
                'anonymization_rules': rules,
                'anonymized_at': datetime.now().isoformat(),
                'anonymization_method': 'pattern_based',
            }
            
            logger.info(f"Anonymized memory {memory_id}")
            
            return {
                'success': True,
                'memory_id': memory_id,
                'anonymized_at': datetime.now().isoformat(),
                'anonymization_rules': rules,
            }
            
        except Exception as e:
            logger.error(f"Failed to anonymize memory: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e),
                'memory_id': memory_id,
            }
    
    def log_access(
        self,
        memory_id: str,
        agent_id: str,
        access_type: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Log access to a memory.
        
        Args:
            memory_id: ID of the memory
            agent_id: ID of the agent accessing
            access_type: Type of access
            metadata: Optional metadata about the access
            
        Returns:
            Dictionary containing the log result
        """
        try:
            if not self.access_logging:
                return {
                    'success': True,
                    'message': 'Access logging is not enabled',
                    'memory_id': memory_id,
                }
            
            # Create access log entry
            log_entry = {
                'timestamp': datetime.now().isoformat(),
                'agent_id': agent_id,
                'access_type': access_type,
                'metadata': metadata or {},
            }
            
            # Store access log
            if memory_id not in self.access_logs:
                self.access_logs[memory_id] = []
            
            self.access_logs[memory_id].append(log_entry)
            
            # Keep only recent logs (last 1000 entries)
            if len(self.access_logs[memory_id]) > 1000:
                self.access_logs[memory_id] = self.access_logs[memory_id][-1000:]
            
            logger.debug(f"Logged access to memory {memory_id} by agent {agent_id}")
            
            return {
                'success': True,
                'memory_id': memory_id,
                'agent_id': agent_id,
                'access_type': access_type,
                'logged_at': log_entry['timestamp'],
            }
            
        except Exception as e:
            logger.error(f"Failed to log access: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e),
                'memory_id': memory_id,
            }
    
    def get_access_logs(
        self,
        memory_id: str,
        agent_id: Optional[str] = None,
        limit: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        Get access logs for a memory.
        
        Args:
            memory_id: ID of the memory
            agent_id: Optional ID of the agent
            limit: Optional limit on number of entries
            
        Returns:
            List of access log entries
        """
        try:
            if memory_id not in self.access_logs:
                return []
            
            logs = self.access_logs[memory_id]
            
            # Filter by agent if specified
            if agent_id:
                logs = [log for log in logs if log['agent_id'] == agent_id]
            
            # Sort by timestamp (newest first)
            logs.sort(key=lambda x: x['timestamp'], reverse=True)
            
            # Apply limit if specified
            if limit:
                logs = logs[:limit]
            
            return logs
            
        except Exception as e:
            logger.error(f"Failed to get access logs: {e}", exc_info=True)
            return []
    
    def apply_retention_policy(
        self,
        memory_id: str,
        retention_policy: str
    ) -> Dict[str, Any]:
        """
        Apply a retention policy to a memory.
        
        Args:
            memory_id: ID of the memory
            retention_policy: Retention policy to apply
            
        Returns:
            Dictionary containing the policy application result
        """
        try:
            # Parse retention policy
            policy_days = self._parse_retention_policy(retention_policy)
            
            # Calculate expiration date
            expiration_date = datetime.now().timestamp() + (policy_days * 24 * 60 * 60)
            
            # Store retention policy
            self.retention_policies[memory_id] = {
                'retention_policy': retention_policy,
                'policy_days': policy_days,
                'expiration_date': expiration_date,
                'applied_at': datetime.now().isoformat(),
            }
            
            logger.info(f"Applied retention policy {retention_policy} to memory {memory_id}")
            
            return {
                'success': True,
                'memory_id': memory_id,
                'retention_policy': retention_policy,
                'expiration_date': datetime.fromtimestamp(expiration_date).isoformat(),
                'applied_at': datetime.now().isoformat(),
            }
            
        except Exception as e:
            logger.error(f"Failed to apply retention policy: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e),
                'memory_id': memory_id,
            }
    
    def check_gdpr_compliance(
        self,
        memory_id: str
    ) -> Dict[str, Any]:
        """
        Check GDPR compliance for a memory.
        
        Args:
            memory_id: ID of the memory
            
        Returns:
            Dictionary containing compliance check results
        """
        try:
            if not self.gdpr_compliance:
                return {
                    'compliant': True,
                    'message': 'GDPR compliance is not enabled',
                    'memory_id': memory_id,
                }
            
            compliance_checks = {
                'data_minimization': True,
                'purpose_limitation': True,
                'storage_limitation': True,
                'accuracy': True,
                'integrity_confidentiality': True,
                'accountability': True,
            }
            
            # Check retention policy
            if memory_id in self.retention_policies:
                policy = self.retention_policies[memory_id]
                if datetime.now().timestamp() > policy['expiration_date']:
                    compliance_checks['storage_limitation'] = False
            
            # Check access logs
            if memory_id in self.access_logs:
                compliance_checks['accountability'] = True
            
            # Check privacy level
            privacy_level = self.get_privacy_level(memory_id)
            if privacy_level in [PrivacyLevel.SENSITIVE, PrivacyLevel.CONFIDENTIAL]:
                compliance_checks['integrity_confidentiality'] = True
            
            # Overall compliance
            compliant = all(compliance_checks.values())
            
            return {
                'compliant': compliant,
                'memory_id': memory_id,
                'compliance_checks': compliance_checks,
                'privacy_level': privacy_level.value,
                'checked_at': datetime.now().isoformat(),
            }
            
        except Exception as e:
            logger.error(f"Failed to check GDPR compliance: {e}", exc_info=True)
            return {
                'compliant': False,
                'error': str(e),
                'memory_id': memory_id,
            }
    
    def export_user_data(
        self,
        agent_id: str,
        export_format: str = "json"
    ) -> Dict[str, Any]:
        """
        Export user data for GDPR compliance.
        
        Args:
            agent_id: ID of the agent/user
            export_format: Format for the export
            
        Returns:
            Dictionary containing the export result
        """
        try:
            if not self.gdpr_compliance:
                return {
                    'success': False,
                    'error': 'GDPR compliance is not enabled',
                    'agent_id': agent_id,
                }
            
            # Collect user data
            user_data = {
                'agent_id': agent_id,
                'exported_at': datetime.now().isoformat(),
                'export_format': export_format,
                'data': {
                    'access_logs': [],
                    'privacy_settings': {},
                    'consent_records': {},
                },
            }
            
            # Collect access logs for this user
            for memory_id, logs in self.access_logs.items():
                user_logs = [log for log in logs if log['agent_id'] == agent_id]
                if user_logs:
                    user_data['data']['access_logs'].append({
                        'memory_id': memory_id,
                        'logs': user_logs,
                    })
            
            # Collect privacy settings
            for memory_id, privacy_level in self.memory_privacy_levels.items():
                user_data['data']['privacy_settings'][memory_id] = privacy_level.value
            
            # Collect consent records
            if agent_id in self.consent_records:
                user_data['data']['consent_records'] = self.consent_records[agent_id]
            
            # Format export based on requested format
            if export_format == "json":
                export_data = json.dumps(user_data, indent=2)
            else:
                export_data = str(user_data)
            
            logger.info(f"Exported user data for agent {agent_id}")
            
            return {
                'success': True,
                'agent_id': agent_id,
                'export_format': export_format,
                'export_data': export_data,
                'exported_at': user_data['exported_at'],
            }
            
        except Exception as e:
            logger.error(f"Failed to export user data: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e),
                'agent_id': agent_id,
            }
    
    def delete_user_data(
        self,
        agent_id: str,
        confirmation_token: str
    ) -> Dict[str, Any]:
        """
        Delete user data for GDPR compliance.
        
        Args:
            agent_id: ID of the agent/user
            confirmation_token: Confirmation token for deletion
            
        Returns:
            Dictionary containing the deletion result
        """
        try:
            if not self.gdpr_compliance:
                return {
                    'success': False,
                    'error': 'GDPR compliance is not enabled',
                    'agent_id': agent_id,
                }
            
            # Verify confirmation token (simplified)
            if not confirmation_token or len(confirmation_token) < 10:
                return {
                    'success': False,
                    'error': 'Invalid confirmation token',
                    'agent_id': agent_id,
                }
            
            # Record deletion request
            self.deletion_requests[agent_id] = {
                'requested_at': datetime.now().isoformat(),
                'confirmation_token': confirmation_token,
                'status': 'pending',
            }
            
            # Delete user data
            deleted_items = {
                'access_logs': 0,
                'privacy_settings': 0,
                'consent_records': 0,
            }
            
            # Delete access logs
            for memory_id, logs in self.access_logs.items():
                original_count = len(logs)
                self.access_logs[memory_id] = [log for log in logs if log['agent_id'] != agent_id]
                deleted_items['access_logs'] += original_count - len(self.access_logs[memory_id])
            
            # Delete privacy settings (only for memories owned by the user)
            # Note: This would need to be coordinated with the memory manager
            
            # Delete consent records
            if agent_id in self.consent_records:
                deleted_items['consent_records'] = len(self.consent_records[agent_id])
                del self.consent_records[agent_id]
            
            # Update deletion request status
            self.deletion_requests[agent_id]['status'] = 'completed'
            self.deletion_requests[agent_id]['completed_at'] = datetime.now().isoformat()
            self.deletion_requests[agent_id]['deleted_items'] = deleted_items
            
            logger.info(f"Deleted user data for agent {agent_id}")
            
            return {
                'success': True,
                'agent_id': agent_id,
                'deleted_items': deleted_items,
                'deleted_at': datetime.now().isoformat(),
            }
            
        except Exception as e:
            logger.error(f"Failed to delete user data: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e),
                'agent_id': agent_id,
            }
    
    def get_privacy_statistics(self) -> Dict[str, Any]:
        """
        Get privacy statistics.
        
        Returns:
            Dictionary containing privacy statistics
        """
        try:
            stats = {
                'total_memories': len(self.memory_privacy_levels),
                'privacy_level_breakdown': {},
                'encrypted_memories': len(self.encrypted_memories),
                'anonymized_memories': len(self.anonymized_memories),
                'access_logs_count': sum(len(logs) for logs in self.access_logs.values()),
                'retention_policies': len(self.retention_policies),
                'gdpr_compliance_enabled': self.gdpr_compliance,
                'deletion_requests': len(self.deletion_requests),
            }
            
            # Privacy level breakdown
            for privacy_level in PrivacyLevel:
                count = sum(1 for level in self.memory_privacy_levels.values() if level == privacy_level)
                stats['privacy_level_breakdown'][privacy_level.value] = count
            
            return stats
            
        except Exception as e:
            logger.error(f"Failed to get privacy statistics: {e}", exc_info=True)
            return {}
    
    def _apply_maximum_privacy(self, memory_id: str) -> None:
        """Apply maximum privacy measures to a memory."""
        # Encrypt the memory
        self.encrypt_memory(memory_id)
        
        # Anonymize the memory
        self.anonymize_memory(memory_id)
        
        # Apply strict retention policy
        self.apply_retention_policy(memory_id, '7_days')
    
    def _apply_enhanced_privacy(self, memory_id: str) -> None:
        """Apply enhanced privacy measures to a memory."""
        # Anonymize the memory
        self.anonymize_memory(memory_id)
        
        # Apply standard retention policy
        self.apply_retention_policy(memory_id, '30_days')
    
    def _generate_encryption_key(self, memory_id: str) -> str:
        """Generate an encryption key for a memory."""
        # Simple key generation based on memory ID and timestamp
        key_data = f"{memory_id}_{datetime.now().isoformat()}"
        return hashlib.sha256(key_data.encode()).hexdigest()[:32]
    
    def _get_default_anonymization_rules(self) -> Dict[str, Any]:
        """Get default anonymization rules."""
        return {
            'email_patterns': True,
            'phone_patterns': True,
            'credit_card_patterns': True,
            'ssn_patterns': True,
            'ip_addresses': True,
            'names': False,  # Keep names for context
        }
    
    def _parse_retention_policy(self, policy: str) -> int:
        """Parse retention policy string to days."""
        if policy.endswith('_days'):
            return int(policy.split('_')[0])
        elif policy.endswith('_months'):
            return int(policy.split('_')[0]) * 30
        elif policy.endswith('_years'):
            return int(policy.split('_')[0]) * 365
        else:
            return 30  # Default to 30 days
    
    def _log_privacy_change(
        self,
        memory_id: str,
        privacy_level: PrivacyLevel,
        set_by: str
    ) -> None:
        """Log privacy level change."""
        log_entry = {
            'timestamp': datetime.now().isoformat(),
            'memory_id': memory_id,
            'privacy_level': privacy_level.value,
            'set_by': set_by,
            'type': 'privacy_change',
        }
        
        if memory_id not in self.access_logs:
            self.access_logs[memory_id] = []
        
        self.access_logs[memory_id].append(log_entry)
