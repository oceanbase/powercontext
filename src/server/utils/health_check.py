"""Health check utilities for system dependencies."""

import asyncio
import logging
import time
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime
from typing import Callable, Dict, Tuple

from ..models.response import DependencyStatus
from ..config import config


logger = logging.getLogger("server")

_DependencyProbe = Callable[[], DependencyStatus]
_DEPENDENCY_STATUS_CACHE: Dict[str, Tuple[float, DependencyStatus]] = {}
_DEPENDENCY_STATUS_LOCKS: Dict[str, asyncio.Lock] = {}
_DEPENDENCY_STATUS_IN_FLIGHT: Dict[str, Future[DependencyStatus]] = {}
_DEPENDENCY_PROBE_EXECUTOR = ThreadPoolExecutor(
    max_workers=2,
    thread_name_prefix="powermem-dependency-probe",
)


def _get_dependency_lock(name: str) -> asyncio.Lock:
    lock = _DEPENDENCY_STATUS_LOCKS.get(name)
    if lock is None:
        lock = asyncio.Lock()
        _DEPENDENCY_STATUS_LOCKS[name] = lock
    return lock


def _timeout_status(name: str, timeout_seconds: float, latency_ms: float) -> DependencyStatus:
    return DependencyStatus(
        name=name,
        status="degraded",
        latency_ms=round(latency_ms, 2),
        error_message=(
            f"Dependency check timed out after {timeout_seconds:g}s; "
            "status probe skipped to keep API server responsive"
        ),
        last_checked=datetime.utcnow(),
    )


async def _run_probe_with_timeout(
    name: str,
    probe: _DependencyProbe,
    timeout_seconds: float,
) -> DependencyStatus:
    start_time = time.time()
    future = _DEPENDENCY_STATUS_IN_FLIGHT.get(name)
    if future is None or future.done():
        future = _DEPENDENCY_PROBE_EXECUTOR.submit(probe)
        _DEPENDENCY_STATUS_IN_FLIGHT[name] = future

    try:
        return await asyncio.wait_for(
            asyncio.wrap_future(future),
            timeout=timeout_seconds,
        )
    except asyncio.TimeoutError:
        latency_ms = (time.time() - start_time) * 1000
        logger.warning(
            "Dependency health check timed out",
            extra={
                "dependency": name,
                "timeout_seconds": timeout_seconds,
                "latency_ms": round(latency_ms, 2),
            },
        )
        return _timeout_status(name, timeout_seconds, latency_ms)
    finally:
        if future.done() and _DEPENDENCY_STATUS_IN_FLIGHT.get(name) is future:
            _DEPENDENCY_STATUS_IN_FLIGHT.pop(name, None)


async def _cached_probe(
    name: str,
    probe: _DependencyProbe,
    *,
    timeout_seconds: float,
    cache_ttl_seconds: float,
) -> DependencyStatus:
    now = time.monotonic()
    cached = _DEPENDENCY_STATUS_CACHE.get(name)
    if cached is not None and now - cached[0] < cache_ttl_seconds:
        return cached[1]

    async with _get_dependency_lock(name):
        now = time.monotonic()
        cached = _DEPENDENCY_STATUS_CACHE.get(name)
        if cached is not None and now - cached[0] < cache_ttl_seconds:
            return cached[1]

        status = await _run_probe_with_timeout(name, probe, timeout_seconds)
        _DEPENDENCY_STATUS_CACHE[name] = (time.monotonic(), status)
        return status


def _check_database_sync() -> DependencyStatus:
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


async def check_database(
    *,
    timeout_seconds: float | None = None,
    cache_ttl_seconds: float | None = None,
) -> DependencyStatus:
    """
    Check database health without blocking the event loop.

    The actual probe may initialize storage clients and perform blocking I/O,
    so run it in a worker thread with a short timeout.
    """

    return await _cached_probe(
        "database",
        _check_database_sync,
        timeout_seconds=(
            config.dependency_check_timeout_seconds
            if timeout_seconds is None
            else timeout_seconds
        ),
        cache_ttl_seconds=(
            config.dependency_status_cache_ttl_seconds
            if cache_ttl_seconds is None
            else cache_ttl_seconds
        ),
    )


def _check_llm_sync() -> DependencyStatus:
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

        if str(llm_provider).lower() == "noop":
            return DependencyStatus(
                name="llm",
                status="disabled",
                error_message="LLM features are disabled by configuration",
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


async def check_llm(
    *,
    timeout_seconds: float | None = None,
    cache_ttl_seconds: float | None = None,
) -> DependencyStatus:
    """Check LLM provider health without blocking the event loop."""

    return await _cached_probe(
        "llm",
        _check_llm_sync,
        timeout_seconds=(
            config.dependency_check_timeout_seconds
            if timeout_seconds is None
            else timeout_seconds
        ),
        cache_ttl_seconds=(
            config.dependency_status_cache_ttl_seconds
            if cache_ttl_seconds is None
            else cache_ttl_seconds
        ),
    )


async def check_all_dependencies(
    *,
    timeout_seconds: float | None = None,
    cache_ttl_seconds: float | None = None,
) -> Dict[str, DependencyStatus]:
    """
    Check all system dependencies
    
    Returns:
        Dictionary mapping dependency name to status
    """
    database_status, llm_status = await asyncio.gather(
        check_database(
            timeout_seconds=timeout_seconds,
            cache_ttl_seconds=cache_ttl_seconds,
        ),
        check_llm(
            timeout_seconds=timeout_seconds,
            cache_ttl_seconds=cache_ttl_seconds,
        ),
    )
    
    return {
        "database": database_status,
        "llm": llm_status,
    }
