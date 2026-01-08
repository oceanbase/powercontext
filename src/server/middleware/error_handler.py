"""
Error handling middleware for PowerMem API
"""

import logging
from typing import Union
from fastapi import Request, status
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from ..models.errors import ErrorCode, APIError
from ..models.response import ErrorResponse
from ..utils.metrics import get_metrics_collector
from datetime import datetime, timezone

try:
    from powermem.utils.utils import get_current_datetime
except ImportError:
    # Fallback if powermem utils not available
    def get_current_datetime():
        return datetime.now(timezone.utc)

logger = logging.getLogger("server")


async def error_handler(request: Request, exc: Exception) -> JSONResponse:
    """
    Global error handler for FastAPI application.
    
    Args:
        request: FastAPI request object
        exc: Exception that was raised
        
    Returns:
        JSONResponse with error details
    """
    # Record error metrics
    metrics_collector = get_metrics_collector()
    endpoint = metrics_collector.normalize_endpoint(request.url.path)
    
    # Handle APIError
    if isinstance(exc, APIError):
        error_type = exc.code.value
        metrics_collector.record_error(error_type, endpoint)
        
        error_response = ErrorResponse(
            error=exc.to_dict(),
            timestamp=get_current_datetime(),
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=error_response.model_dump(mode='json'),
        )
    
    # Handle HTTPException
    if isinstance(exc, StarletteHTTPException):
        error_detail = exc.detail
        if isinstance(error_detail, dict):
            error_code = error_detail.get("code", ErrorCode.INTERNAL_ERROR.value)
            error_message = error_detail.get("message", str(exc))
        else:
            error_code = ErrorCode.INTERNAL_ERROR.value
            error_message = str(error_detail)
        
        metrics_collector.record_error(error_code, endpoint)
        
        error_response = ErrorResponse(
            error={
                "code": error_code,
                "message": error_message,
                "details": {},
            },
            timestamp=get_current_datetime(),
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=error_response.model_dump(mode='json'),
        )
    
    # Handle validation errors
    if isinstance(exc, RequestValidationError):
        metrics_collector.record_error("VALIDATION_ERROR", endpoint)
        
        error_response = ErrorResponse(
            error={
                "code": ErrorCode.INVALID_REQUEST.value,
                "message": "Request validation failed",
                "details": {
                    "errors": exc.errors(),
                },
            },
            timestamp=get_current_datetime(),
        )
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=error_response.model_dump(mode='json'),
        )
    
    # Handle unexpected errors
    logger.exception(f"Unhandled error: {exc}")
    metrics_collector.record_error(ErrorCode.INTERNAL_ERROR.value, endpoint)
    
    error_response = ErrorResponse(
        error={
            "code": ErrorCode.INTERNAL_ERROR.value,
            "message": "Internal server error",
            "details": {},
        },
        timestamp=datetime.utcnow(),
    )
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=error_response.model_dump(mode='json'),
    )
