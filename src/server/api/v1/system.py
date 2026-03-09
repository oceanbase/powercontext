"""
System management API routes
"""

from fastapi import APIRouter, Depends, Request, Response, Query
from slowapi import Limiter
from typing import Optional
from datetime import datetime, timezone

from ...models.response import APIResponse, HealthResponse, StatusResponse
from ...middleware.auth import verify_api_key
from ...middleware.rate_limit import limiter, get_rate_limit_string
from ...config import config
from ...utils.metrics import get_metrics_collector
from ...utils.health_check import check_all_dependencies
from powermem import auto_config
from powermem.version import __version__ as powermem_version

# Import server start time from state module to avoid circular imports
from ...state import SERVER_START_TIME

router = APIRouter(prefix="/system", tags=["system"])


@router.get(
    "/health",
    response_model=APIResponse,
    summary="Health check",
    description="Check if the API server is healthy (public endpoint, no authentication required)",
)
async def health_check():
    """Health check endpoint"""
    health = HealthResponse(status="healthy")
    
    return APIResponse(
        success=True,
        data=health.model_dump(mode='json'),
        message="Service is healthy",
    )


@router.get(
    "/status",
    response_model=APIResponse,
    summary="System status",
    description="Get system status and configuration information",
)
@limiter.limit(get_rate_limit_string())
async def get_status(
    request: Request,
    api_key: str = Depends(verify_api_key),
):
    """Get system status"""
    try:
        # Get PowerMem config
        powermem_config = auto_config()
        
        storage_type = None
        llm_provider = None
        
        if isinstance(powermem_config, dict):
            # Extract from dict config
            vector_store = powermem_config.get("vector_store") or powermem_config.get("database", {})
            storage_type = vector_store.get("provider") if isinstance(vector_store, dict) else None
            
            llm = powermem_config.get("llm", {})
            llm_provider = llm.get("provider") if isinstance(llm, dict) else None
        else:
            # Extract from config object
            if hasattr(powermem_config, "vector_store") and powermem_config.vector_store:
                storage_type = powermem_config.vector_store.provider
            if hasattr(powermem_config, "llm") and powermem_config.llm:
                llm_provider = powermem_config.llm.provider
        
        # Calculate uptime
        now = datetime.now(timezone.utc)
        uptime_seconds = (now - SERVER_START_TIME).total_seconds()
        
        # Check dependencies
        dependencies = await check_all_dependencies()
        
        # Determine overall system status based on dependencies
        system_status = "operational"
        degraded_count = sum(1 for dep in dependencies.values() if dep.status == "degraded")
        unavailable_count = sum(1 for dep in dependencies.values() if dep.status == "unavailable")
        
        if unavailable_count > 0:
            system_status = "down"
        elif degraded_count > 0:
            system_status = "degraded"
        
        # Convert dependencies to dict for response
        dependencies_dict = {
            name: dep.model_dump(mode='json')
            for name, dep in dependencies.items()
        }
        
        status_data = StatusResponse(
            status=system_status,
            version=powermem_version,
            storage_type=storage_type,
            llm_provider=llm_provider,
            uptime_seconds=uptime_seconds,
            started_at=SERVER_START_TIME,
            dependencies=dependencies_dict,
        )
        
        return APIResponse(
            success=True,
            data=status_data.model_dump(mode='json'),
            message="System status retrieved successfully",
        )
    except Exception as e:
        # Fallback: return basic status even if dependencies check fails
        now = datetime.now(timezone.utc)
        uptime_seconds = (now - SERVER_START_TIME).total_seconds()
        
        status_data = StatusResponse(
            status="degraded",
            version=powermem_version,
            storage_type=None,
            llm_provider=None,
            uptime_seconds=uptime_seconds,
            started_at=SERVER_START_TIME,
            dependencies={},
        )
        
        return APIResponse(
            success=True,
            data=status_data.model_dump(mode='json'),
            message=f"System status retrieved with errors: {str(e)[:100]}",
        )


@router.get(
    "/metrics",
    summary="Prometheus metrics",
    description="Get Prometheus format metrics",
)
@limiter.limit(get_rate_limit_string())
async def get_metrics(
    request: Request,
    api_key: str = Depends(verify_api_key),
):
    """Get Prometheus format metrics"""
    metrics_collector = get_metrics_collector()
    metrics_text = metrics_collector.get_metrics()
    
    return Response(
        content=metrics_text,
        media_type="text/plain; version=0.0.4; charset=utf-8"
    )


@router.delete(
    "/delete-all-memories",
    response_model=APIResponse,
    summary="Delete all memories",
    description="Delete all memories matching the provided filters (requires admin permissions). "
                "This endpoint uses Memory.delete_all() to match the powermem SDK API. "
                "If no filters provided, deletes all memories.",
)
@limiter.limit(get_rate_limit_string())
async def delete_all_memories(
    request: Request,
    user_id: Optional[str] = Query(None, description="Filter by user ID"),
    agent_id: Optional[str] = Query(None, description="Filter by agent ID"),
    run_id: Optional[str] = Query(None, description="Filter by run ID"),
    api_key: str = Depends(verify_api_key),
):
    """
    Delete all memories matching the provided filters.
    
    This endpoint uses Memory.delete_all() to match the powermem SDK API.
    If no filters are provided, all memories will be deleted.
    """
    from powermem import Memory
    from ...models.errors import ErrorCode, APIError
    
    try:
        memory = Memory(config=auto_config())
        result = memory.delete_all(
            user_id=user_id,
            agent_id=agent_id,
            run_id=run_id,
        )
        
        filters = {}
        if user_id:
            filters["user_id"] = user_id
        if agent_id:
            filters["agent_id"] = agent_id
        if run_id:
            filters["run_id"] = run_id
        
        filter_desc = f" with filters: {filters}" if filters else ""
        
        return APIResponse(
            success=True,
            data={"deleted": result, "filters": filters},
            message=f"All memories{filter_desc} deleted successfully",
        )
    except Exception as e:
        raise APIError(
            code=ErrorCode.INTERNAL_ERROR,
            message=f"Failed to delete all memories: {str(e)}",
            status_code=500,
        )
