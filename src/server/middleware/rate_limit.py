"""
Rate limiting middleware for PowerMem API
"""

from typing import Callable
from fastapi import Request, HTTPException
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from ..config import config
from ..models.errors import ErrorCode

# Initialize rate limiter with Redis or in-memory storage
# For now, use in-memory storage (can be upgraded to Redis later)
limiter = Limiter(key_func=get_remote_address, storage_uri="memory://")


def rate_limit_middleware(app):
    """
    Setup rate limiting middleware for FastAPI app.
    
    Args:
        app: FastAPI application instance
    """
    if not config.rate_limit_enabled:
        return
    
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


def get_rate_limit_string() -> str:
    """
    Get rate limit string from config.
    
    Returns:
        Rate limit string (e.g., "100/minute")
    """
    return f"{config.rate_limit_per_minute}/minute"
