"""
Audit logging for memory operations

This module handles audit logging for compliance and security.
"""

import logging
import json
from typing import Any, Dict, Optional
from datetime import datetime
from powermem.utils.utils import get_current_datetime
import os

logger = logging.getLogger(__name__)


class AuditLogger:
    """
    Manages audit logging for memory operations.
    """
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Initialize audit logger.
        
        Args:
            config: Configuration dictionary
        """
        self.config = config or {}
        self.enabled = self.config.get("enable_audit", True)
        self.log_file = self.config.get("audit_log_file", "audit.log")
        self.log_level = self.config.get("audit_log_level", "INFO")
        self.retention_days = self.config.get("audit_retention_days", 90)
        
        # Setup audit logger
        self.audit_logger = logging.getLogger("audit")
        self.audit_logger.setLevel(getattr(logging, self.log_level.upper()))
        
        # Create file handler if not exists
        if not self.audit_logger.handlers:
            handler = logging.FileHandler(self.log_file)
            formatter = logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
            )
            handler.setFormatter(formatter)
            self.audit_logger.addHandler(handler)
        
        logger.info(f"AuditLogger initialized - enabled: {self.enabled}, log_file: {self.log_file}")
    
    def log_event(
        self,
        event_type: str,
        details: Dict[str, Any],
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> None:
        """
        Log an audit event.
        
        Args:
            event_type: Type of event (e.g., 'memory.add', 'memory.delete')
            details: Event details
            user_id: User ID associated with the event
            agent_id: Agent ID associated with the event
        """
        if not self.enabled:
            return
        
        try:
            audit_entry = {
                "timestamp": get_current_datetime().isoformat(),
                "event_type": event_type,
                "user_id": user_id,
                "agent_id": agent_id,
                "details": details,
                "version": "0.3.0",
            }
            
            # Log to file
            self.audit_logger.info(json.dumps(audit_entry))
            
            # Also log to console in debug mode
            if self.config.get("debug", False):
                logger.debug(f"Audit event: {event_type} - {details}")
            
        except Exception as e:
            logger.error(f"Failed to log audit event: {e}")
    
    async def log_event_async(
        self,
        event_type: str,
        details: Dict[str, Any],
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> None:
        """
        Log an audit event asynchronously.
        
        Args:
            event_type: Type of event
            details: Event details
            user_id: User ID associated with the event
            agent_id: Agent ID associated with the event
        """
        # For now, just call the sync version
        # In a real implementation, this would use async file I/O
        self.log_event(event_type, details, user_id, agent_id)
    
    def log_access(
        self,
        resource_type: str,
        resource_id: str,
        action: str,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        success: bool = True,
    ) -> None:
        """
        Log access to resources.
        
        Args:
            resource_type: Type of resource (e.g., 'memory', 'user')
            resource_id: ID of the resource
            action: Action performed (e.g., 'read', 'write', 'delete')
            user_id: User ID
            agent_id: Agent ID
            success: Whether the action was successful
        """
        self.log_event(
            "access",
            {
                "resource_type": resource_type,
                "resource_id": resource_id,
                "action": action,
                "success": success,
            },
            user_id=user_id,
            agent_id=agent_id,
        )
    
    def log_security_event(
        self,
        event_type: str,
        severity: str,
        details: Dict[str, Any],
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> None:
        """
        Log security-related events.
        
        Args:
            event_type: Type of security event
            severity: Severity level (low, medium, high, critical)
            details: Event details
            user_id: User ID
            agent_id: Agent ID
        """
        self.log_event(
            "security",
            {
                "event_type": event_type,
                "severity": severity,
                "details": details,
            },
            user_id=user_id,
            agent_id=agent_id,
        )
    
    def log_data_change(
        self,
        change_type: str,
        resource_id: str,
        old_data: Optional[Dict[str, Any]] = None,
        new_data: Optional[Dict[str, Any]] = None,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> None:
        """
        Log data changes for compliance.
        
        Args:
            change_type: Type of change (create, update, delete)
            resource_id: ID of the resource
            old_data: Previous data (for updates/deletes)
            new_data: New data (for creates/updates)
            user_id: User ID
            agent_id: Agent ID
        """
        self.log_event(
            "data_change",
            {
                "change_type": change_type,
                "resource_id": resource_id,
                "old_data": old_data,
                "new_data": new_data,
            },
            user_id=user_id,
            agent_id=agent_id,
        )
    
    def get_audit_logs(
        self,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        event_type: Optional[str] = None,
        limit: int = 1000,
    ) -> list:
        """
        Retrieve audit logs with filtering.
        
        Args:
            start_date: Start date for filtering
            end_date: End date for filtering
            user_id: Filter by user ID
            agent_id: Filter by agent ID
            event_type: Filter by event type
            limit: Maximum number of logs to return
            
        Returns:
            List of audit log entries
        """
        # This is a simplified implementation
        # In a real system, you would query a proper audit database
        logs = []
        
        try:
            if os.path.exists(self.log_file):
                with open(self.log_file, 'r') as f:
                    for line in f:
                        try:
                            log_entry = json.loads(line.strip())
                            
                            # Apply filters
                            if start_date and log_entry['timestamp'] < start_date.isoformat():
                                continue
                            if end_date and log_entry['timestamp'] > end_date.isoformat():
                                continue
                            if user_id and log_entry.get('user_id') != user_id:
                                continue
                            if agent_id and log_entry.get('agent_id') != agent_id:
                                continue
                            if event_type and log_entry.get('event_type') != event_type:
                                continue
                            
                            logs.append(log_entry)
                            
                            if len(logs) >= limit:
                                break
                                
                        except json.JSONDecodeError:
                            continue
            
        except Exception as e:
            logger.error(f"Failed to retrieve audit logs: {e}")
        
        return logs
