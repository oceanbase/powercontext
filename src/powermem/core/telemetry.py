"""
Telemetry management for memory operations

This module handles telemetry data collection and reporting.
"""

import logging
import json
import time
from typing import Any, Dict, Optional
from datetime import datetime
from powermem.utils.utils import get_current_datetime
import httpx

logger = logging.getLogger(__name__)


class TelemetryManager:
    """
    Manages telemetry data collection and reporting.
    """
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Initialize telemetry manager.
        
        Args:
            config: Configuration dictionary
        """
        self.config = config or {}
        self.enabled = self.config.get("enable_telemetry", False)
        self.endpoint = self.config.get("telemetry_endpoint", "https://telemetry.powermem.ai")
        self.api_key = self.config.get("telemetry_api_key")
        self.batch_size = self.config.get("telemetry_batch_size", 100)
        self.flush_interval = self.config.get("telemetry_flush_interval", 30)
        
        self.events = []
        self.last_flush = time.time()
        
        logger.info(f"TelemetryManager initialized - enabled: {self.enabled}")
    
    def capture_event(
        self,
        event_name: str,
        properties: Optional[Dict[str, Any]] = None,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> None:
        """
        Capture a telemetry event.
        
        Args:
            event_name: Name of the event
            properties: Event properties
            user_id: User ID associated with the event
            agent_id: Agent ID associated with the event
        """
        if not self.enabled:
            return
        
        try:
            event = {
                "event_name": event_name,
                "properties": properties or {},
                "user_id": user_id,
                "agent_id": agent_id,
                "timestamp": get_current_datetime().isoformat(),
                "version": "0.3.0",
            }
            
            self.events.append(event)
            
            # Flush if batch size reached
            if len(self.events) >= self.batch_size:
                self._flush_events()
            
        except Exception as e:
            logger.error(f"Failed to capture telemetry event: {e}")
    
    def _flush_events(self) -> None:
        """Flush events to the telemetry endpoint."""
        if not self.events or not self.enabled:
            return
        
        try:
            if self.api_key:
                headers = {
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                }
            else:
                headers = {"Content-Type": "application/json"}
            
            payload = {
                "events": self.events.copy(),
                "timestamp": get_current_datetime().isoformat(),
            }
            
            # Send events asynchronously to avoid blocking
            self._send_events_async(payload, headers)
            
            # Clear events after sending
            self.events.clear()
            self.last_flush = time.time()
            
        except Exception as e:
            logger.error(f"Failed to flush telemetry events: {e}")
    
    def _send_events_async(self, payload: Dict[str, Any], headers: Dict[str, str]) -> None:
        """Send events asynchronously."""
        try:
            # Try to get current event loop
            import asyncio
            try:
                loop = asyncio.get_running_loop()
                # If we're in an async context, schedule the task
                asyncio.create_task(self._send_request(payload, headers))
            except RuntimeError:
                # No running event loop, use httpx in sync mode for now
                import httpx
                try:
                    with httpx.Client(timeout=10.0) as client:
                        response = client.post(
                            f"{self.endpoint}/events",
                            json=payload,
                            headers=headers,
                            timeout=10.0
                        )
                        response.raise_for_status()
                except Exception as sync_e:
                    logger.debug(f"Failed to send telemetry events synchronously: {sync_e}")
            
        except Exception as e:
            logger.debug(f"Failed to send telemetry events: {e}")
    
    async def _send_request(self, payload: Dict[str, Any], headers: Dict[str, str]) -> None:
        """Helper method to send HTTP request asynchronously."""
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.endpoint}/events",
                json=payload,
                headers=headers,
                timeout=10.0
            )
            response.raise_for_status()
    
    def flush(self) -> None:
        """Manually flush all pending events."""
        self._flush_events()
    
    def set_user_properties(self, user_id: str, properties: Dict[str, Any]) -> None:
        """
        Set user properties for telemetry.
        
        Args:
            user_id: User ID
            properties: User properties
        """
        if not self.enabled:
            return
        
        try:
            event = {
                "event_name": "user_properties",
                "properties": properties,
                "user_id": user_id,
                "timestamp": get_current_datetime().isoformat(),
                "version": "0.3.0",
            }
            
            self.events.append(event)
            
        except Exception as e:
            logger.error(f"Failed to set user properties: {e}")
    
    def track_performance(self, operation: str, duration: float, metadata: Optional[Dict[str, Any]] = None) -> None:
        """
        Track performance metrics.
        
        Args:
            operation: Operation name
            duration: Duration in seconds
            metadata: Additional metadata
        """
        if not self.enabled:
            return
        
        self.capture_event(
            "performance_metric",
            {
                "operation": operation,
                "duration": duration,
                "metadata": metadata or {},
            }
        )
    
    def track_error(self, error_type: str, error_message: str, context: Optional[Dict[str, Any]] = None) -> None:
        """
        Track error events.
        
        Args:
            error_type: Type of error
            error_message: Error message
            context: Additional context
        """
        if not self.enabled:
            return
        
        self.capture_event(
            "error",
            {
                "error_type": error_type,
                "error_message": error_message,
                "context": context or {},
            }
        )
