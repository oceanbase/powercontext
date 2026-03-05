"""
Health check utilities for system dependencies
"""

import time
from typing import Dict
from datetime import datetime

from ..models.response import DependencyStatus


async def check_database() -> DependencyStatus:
    """
    Check database health and measure latency
    
    Returns:
        DependencyStatus with health information
    """
    start_time = time.time()
    
    try:
        from powermem import Memory, auto_config
        
        # Get config
        config = auto_config()
        
        # For now, database check is tied to vector store initialization
        # In the future, this could be a separate check for SQL databases
        memory = Memory(config=config)
        
        latency_ms = (time.time() - start_time) * 1000
        
        return DependencyStatus(
            name="database",
            status="healthy",
            latency_ms=round(latency_ms, 2),
            last_checked=datetime.utcnow(),
        )
    except Exception as e:
        latency_ms = (time.time() - start_time) * 1000
        error_msg = str(e)
        # Truncate long error messages
        if len(error_msg) > 200:
            error_msg = error_msg[:197] + "..."
            
        return DependencyStatus(
            name="database",
            status="unavailable",
            latency_ms=round(latency_ms, 2),
            error_message=error_msg,
            last_checked=datetime.utcnow(),
        )


async def check_llm() -> DependencyStatus:
    """
    Check LLM provider health and measure latency
    
    Returns:
        DependencyStatus with health information
    """
    start_time = time.time()
    
    try:
        from powermem import auto_config
        
        config = auto_config()
        
        # Extract LLM config
        llm_provider = None
        if isinstance(config, dict):
            llm = config.get("llm", {})
            llm_provider = llm.get("provider") if isinstance(llm, dict) else None
        else:
            if hasattr(config, "llm") and config.llm:
                llm_provider = config.llm.provider
        
        if not llm_provider:
            return DependencyStatus(
                name="llm",
                status="unavailable",
                error_message="LLM provider not configured",
                last_checked=datetime.utcnow(),
            )
        
        # For now, just check if LLM is configured
        # In the future, could make a test API call
        latency_ms = (time.time() - start_time) * 1000
        
        return DependencyStatus(
            name="llm",
            status="healthy",
            latency_ms=round(latency_ms, 2),
            last_checked=datetime.utcnow(),
        )
    except Exception as e:
        latency_ms = (time.time() - start_time) * 1000
        error_msg = str(e)
        # Truncate long error messages
        if len(error_msg) > 200:
            error_msg = error_msg[:197] + "..."
            
        return DependencyStatus(
            name="llm",
            status="unavailable",
            latency_ms=round(latency_ms, 2),
            error_message=error_msg,
            last_checked=datetime.utcnow(),
        )


async def check_all_dependencies() -> Dict[str, DependencyStatus]:
    """
    Check all system dependencies
    
    Returns:
        Dictionary mapping dependency name to status
    """
    database_status = await check_database()
    llm_status = await check_llm()
    
    return {
        "database": database_status,
        "llm": llm_status,
    }
