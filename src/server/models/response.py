"""
Response models for PowerMem API
"""

from typing import Any, Dict, List, Optional
from datetime import datetime, timezone
from pydantic import BaseModel, Field, field_serializer

try:
    from powermem.utils.utils import get_current_datetime
except ImportError:
    # Fallback if powermem utils not available
    def get_current_datetime():
        return datetime.now(timezone.utc)


class APIResponse(BaseModel):
    """Standard API response wrapper"""
    
    success: bool = Field(..., description="Whether the operation was successful")
    data: Optional[Any] = Field(None, description="Response data")
    message: Optional[str] = Field(None, description="Response message")
    timestamp: datetime = Field(default_factory=get_current_datetime, description="Response timestamp")
    
    @field_serializer('timestamp')
    def serialize_timestamp(self, value: datetime, _info):
        """Serialize datetime to ISO format string with Z suffix (UTC)"""
        if value is None:
            return None
        # Convert to UTC if timezone-aware, otherwise assume UTC
        if value.tzinfo is not None:
            utc_value = value.astimezone(timezone.utc)
        else:
            utc_value = value
        # Format as ISO 8601 with Z suffix
        return utc_value.replace(tzinfo=None).isoformat() + "Z"


class MemoryResponse(BaseModel):
    """Response model for a single memory"""
    
    memory_id: int = Field(..., description="Memory ID")
    content: str = Field(..., description="Memory content")
    user_id: Optional[str] = Field(None, description="User ID")
    agent_id: Optional[str] = Field(None, description="Agent ID")
    run_id: Optional[str] = Field(None, description="Run ID")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Metadata")
    created_at: Optional[datetime] = Field(None, description="Creation timestamp")
    updated_at: Optional[datetime] = Field(None, description="Update timestamp")
    
    @field_serializer('created_at', 'updated_at')
    def serialize_datetime(self, value: Optional[datetime], _info):
        """Serialize datetime to ISO format string with Z suffix (UTC)"""
        if value is None:
            return None
        # Convert to UTC if timezone-aware, otherwise assume UTC
        if value.tzinfo is not None:
            utc_value = value.astimezone(timezone.utc)
        else:
            utc_value = value
        # Format as ISO 8601 with Z suffix
        return utc_value.replace(tzinfo=None).isoformat() + "Z"


class MemoryListResponse(BaseModel):
    """Response model for a list of memories"""
    
    memories: List[MemoryResponse] = Field(default_factory=list, description="List of memories")
    total: int = Field(0, description="Total number of memories")
    limit: int = Field(0, description="Limit applied")
    offset: int = Field(0, description="Offset applied")


class SearchResult(BaseModel):
    """Single search result"""
    
    memory_id: int = Field(..., description="Memory ID")
    content: str = Field(..., description="Memory content")
    score: Optional[float] = Field(None, description="Relevance score")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Metadata")


class SearchResponse(BaseModel):
    """Response model for search results"""
    
    results: List[SearchResult] = Field(default_factory=list, description="Search results")
    total: int = Field(0, description="Total number of results")
    query: str = Field(..., description="Search query")


class UserProfileResponse(BaseModel):
    """Response model for user profile"""
    
    user_id: str = Field(..., description="User ID")
    profile_content: Optional[str] = Field(None, description="Profile content text")
    topics: Optional[Dict[str, Any]] = Field(None, description="Structured topics")
    updated_at: Optional[datetime] = Field(None, description="Last update timestamp")
    
    @field_serializer('updated_at')
    def serialize_datetime(self, value: Optional[datetime], _info):
        """Serialize datetime to ISO format string with Z suffix (UTC)"""
        if value is None:
            return None
        # Convert to UTC if timezone-aware, otherwise assume UTC
        if value.tzinfo is not None:
            utc_value = value.astimezone(timezone.utc)
        else:
            utc_value = value
        # Format as ISO 8601 with Z suffix
        return utc_value.replace(tzinfo=None).isoformat() + "Z"


class HealthResponse(BaseModel):
    """Response model for health check"""
    
    status: str = Field(..., description="Health status")
    timestamp: datetime = Field(default_factory=get_current_datetime, description="Check timestamp")
    
    @field_serializer('timestamp')
    def serialize_datetime(self, value: datetime, _info):
        """Serialize datetime to ISO format string with Z suffix (UTC)"""
        if value is None:
            return None
        # Convert to UTC if timezone-aware, otherwise assume UTC
        if value.tzinfo is not None:
            utc_value = value.astimezone(timezone.utc)
        else:
            utc_value = value
        # Format as ISO 8601 with Z suffix
        return utc_value.replace(tzinfo=None).isoformat() + "Z"


class StatusResponse(BaseModel):
    """Response model for system status"""
    
    status: str = Field(..., description="System status")
    version: str = Field(..., description="API version")
    storage_type: Optional[str] = Field(None, description="Storage backend type")
    llm_provider: Optional[str] = Field(None, description="LLM provider")
    timestamp: datetime = Field(default_factory=get_current_datetime, description="Status timestamp")
    
    @field_serializer('timestamp')
    def serialize_datetime(self, value: datetime, _info):
        """Serialize datetime to ISO format string with Z suffix (UTC)"""
        if value is None:
            return None
        # Convert to UTC if timezone-aware, otherwise assume UTC
        if value.tzinfo is not None:
            utc_value = value.astimezone(timezone.utc)
        else:
            utc_value = value
        # Format as ISO 8601 with Z suffix
        return utc_value.replace(tzinfo=None).isoformat() + "Z"


class ErrorResponse(BaseModel):
    """Error response model"""
    
    success: bool = Field(False, description="Always false for errors")
    error: Dict[str, Any] = Field(..., description="Error details")
    timestamp: datetime = Field(default_factory=get_current_datetime, description="Error timestamp")
    
    @field_serializer('timestamp')
    def serialize_datetime(self, value: datetime, _info):
        """Serialize datetime to ISO format string with Z suffix (UTC)"""
        if value is None:
            return None
        # Convert to UTC if timezone-aware, otherwise assume UTC
        if value.tzinfo is not None:
            utc_value = value.astimezone(timezone.utc)
        else:
            utc_value = value
        # Format as ISO 8601 with Z suffix
        return utc_value.replace(tzinfo=None).isoformat() + "Z"
