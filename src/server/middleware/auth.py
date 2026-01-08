"""
Authentication middleware for PowerMem API
"""

from typing import Optional
from fastapi import Header, HTTPException, Query, Security
from fastapi.security import APIKeyHeader, APIKeyQuery
from ..config import config
from ..models.errors import ErrorCode, APIError

# API Key security schemes
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
api_key_query = APIKeyQuery(name="api_key", auto_error=False)


def get_api_key(
    x_api_key: Optional[str] = Security(api_key_header),
    api_key: Optional[str] = Security(api_key_query),
) -> Optional[str]:
    """
    Extract API key from header or query parameter.
    
    Args:
        x_api_key: API key from X-API-Key header
        api_key: API key from query parameter
        
    Returns:
        API key string or None
    """
    return x_api_key or api_key


def verify_api_key(api_key: Optional[str] = Security(get_api_key)) -> str:
    """
    Verify API key and return it if valid.
    
    Args:
        api_key: API key to verify
        
    Returns:
        Verified API key
        
    Raises:
        HTTPException: If authentication is required but key is invalid
    """
    if not config.auth_enabled:
        return "anonymous"
    
    if not api_key:
        raise HTTPException(
            status_code=401,
            detail={
                "code": ErrorCode.UNAUTHORIZED.value,
                "message": "API key required",
                "details": {}
            }
        )
    
    valid_keys = config.get_api_keys_list()
    if api_key not in valid_keys:
        raise HTTPException(
            status_code=401,
            detail={
                "code": ErrorCode.UNAUTHORIZED.value,
                "message": "Invalid API key",
                "details": {}
            }
        )
    
    return api_key
