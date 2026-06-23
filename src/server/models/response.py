"""
Response models for PowerMem API
"""

from typing import Any, Dict, List, Optional
from datetime import datetime, timezone
from pydantic import BaseModel, Field, field_serializer, computed_field

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
    
    @computed_field
    @property
    def id(self) -> str:
        """Computed field: alias for memory_id serialized as string to prevent
        JavaScript large integer precision loss (Snowflake IDs exceed 2^53)."""
        return str(self.memory_id)
    
    @field_serializer('memory_id')
    def serialize_memory_id(self, value: int, _info) -> str:
        """Serialize as string to prevent JavaScript precision loss for large Snowflake IDs."""
        return str(value)
    
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


class SessionSummaryResponse(BaseModel):
    """Response model for one coding-agent session summary."""

    run_id: str = Field(..., description="Run/session identifier")
    user_id: Optional[str] = Field(None, description="User ID")
    agent_id: Optional[str] = Field(None, description="Agent ID")
    first_seen: Optional[str] = Field(None, description="First event timestamp")
    last_seen: Optional[str] = Field(None, description="Latest event timestamp")
    event_count: int = Field(0, description="Number of projected timeline events")
    memory_count: int = Field(0, description="Number of unique memories in the session")
    latest_preview: str = Field("", description="Latest memory/event preview")
    precision: str = Field("memory_snapshot", description="Timeline precision mode")


class SessionListResponse(BaseModel):
    """Response model for session summaries."""

    sessions: List[SessionSummaryResponse] = Field(
        default_factory=list,
        description="List of session summaries",
    )
    total: int = Field(0, description="Total number of matching sessions")
    limit: int = Field(0, description="Limit applied")
    offset: int = Field(0, description="Offset applied")
    precision: str = Field("memory_snapshot", description="Timeline precision mode")
    capabilities: Dict[str, bool] = Field(
        default_factory=dict,
        description="Supported timeline capabilities",
    )


class SessionStatsResponse(BaseModel):
    """Response model for session timeline overview metrics."""

    total_sessions: int = Field(0, description="Number of sessions")
    total_events: int = Field(0, description="Number of projected events")
    changed_memories: int = Field(0, description="Number of unique memory records")
    no_op_events: int = Field(0, description="Number of no-op events")
    no_op_rate: float = Field(0.0, description="No-op event ratio")
    event_types: Dict[str, int] = Field(
        default_factory=dict,
        description="Event type distribution",
    )
    precision: str = Field("memory_snapshot", description="Timeline precision mode")
    capabilities: Dict[str, bool] = Field(
        default_factory=dict,
        description="Supported timeline capabilities",
    )


class TimelineEventResponse(BaseModel):
    """Response model for one projected timeline event."""

    event_id: str = Field(..., description="Stable event identifier")
    occurred_at: Optional[str] = Field(None, description="Event timestamp")
    run_id: Optional[str] = Field(None, description="Run/session identifier")
    user_id: Optional[str] = Field(None, description="User ID")
    agent_id: Optional[str] = Field(None, description="Agent ID")
    memory_id: Optional[str] = Field(None, description="Linked memory ID")
    event_type: str = Field("memory", description="Projected event type")
    pipeline_mode: Optional[str] = Field(None, description="Pipeline mode, when recorded")
    content_preview: str = Field("", description="Preview of memory or source content")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Event metadata")
    source_preview: Optional[str] = Field(None, description="Source preview when requested")
    source_content: Optional[str] = Field(None, description="Full source content when requested")
    precision: str = Field("memory_snapshot", description="Timeline precision mode")


class TimelineResponse(BaseModel):
    """Response model for a timeline event page."""

    events: List[TimelineEventResponse] = Field(
        default_factory=list,
        description="Timeline events",
    )
    total: int = Field(0, description="Total number of matching events")
    limit: int = Field(0, description="Limit applied")
    next_cursor: Optional[str] = Field(None, description="Opaque cursor for the next page")
    order: str = Field("desc", description="Sort order")
    precision: str = Field("memory_snapshot", description="Timeline precision mode")
    capabilities: Dict[str, bool] = Field(
        default_factory=dict,
        description="Supported timeline capabilities",
    )


class ObservationIngestResponse(BaseModel):
    """Response model for a structured observation ingest operation."""

    observation_id: Optional[str] = Field(None, description="Observation ID")
    raw_memory: Optional[MemoryResponse] = Field(None, description="Persisted raw observation memory")
    semantic_memories: List[MemoryResponse] = Field(
        default_factory=list,
        description="Semantic memories extracted from the observation",
    )
    memories: List[MemoryResponse] = Field(
        default_factory=list,
        description="All memories created or returned by this ingest operation",
    )
    saved_raw: bool = Field(False, description="Whether a raw observation was saved")
    inferred: bool = Field(False, description="Whether intelligent extraction was requested")
    deduped: bool = Field(False, description="Whether an existing raw observation was reused")


class ObservationBatchItemResponse(BaseModel):
    """Response model for one item in a batch observation ingest operation."""

    index: int = Field(..., description="Original item index")
    success: bool = Field(..., description="Whether the item was ingested successfully")
    observation_id: Optional[str] = Field(None, description="Observation ID")
    result: Optional[ObservationIngestResponse] = Field(None, description="Ingest result")
    error: Optional[str] = Field(None, description="Failure message")


class ObservationBatchIngestResponse(BaseModel):
    """Response model for batch observation ingest."""

    items: List[ObservationBatchItemResponse] = Field(
        default_factory=list,
        description="Per-item ingest results",
    )
    total: int = Field(0, description="Total number of observations")
    success_count: int = Field(0, description="Number of successful observations")
    failed_count: int = Field(0, description="Number of failed observations")


class SearchResult(BaseModel):
    """Single search result"""

    memory_id: int = Field(..., description="Memory ID")
    content: str = Field(..., description="Memory content")
    score: Optional[float] = Field(None, description="Relevance score")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Metadata")
    user_id: Optional[str] = Field(None, description="User ID")
    agent_id: Optional[str] = Field(None, description="Agent ID")
    run_id: Optional[str] = Field(None, description="Run ID")
    created_at: Optional[datetime] = Field(None, description="Creation timestamp")
    updated_at: Optional[datetime] = Field(None, description="Update timestamp")

    @computed_field
    @property
    def id(self) -> str:
        """Computed field: alias for memory_id serialized as string to prevent
        JavaScript large integer precision loss (Snowflake IDs exceed 2^53)."""
        return str(self.memory_id)

    @field_serializer('memory_id')
    def serialize_memory_id(self, value: int, _info) -> str:
        """Serialize as string to prevent JavaScript precision loss for large Snowflake IDs."""
        return str(value)

    @field_serializer('created_at', 'updated_at')
    def serialize_datetime(self, value: Optional[datetime], _info):
        """Serialize datetime to ISO format string with Z suffix (UTC)"""
        if value is None:
            return None
        if value.tzinfo is not None:
            utc_value = value.astimezone(timezone.utc)
        else:
            utc_value = value
        return utc_value.replace(tzinfo=None).isoformat() + "Z"


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


class DependencyStatus(BaseModel):
    """Response model for dependency health status"""
    
    name: str = Field(..., description="Dependency name")
    status: str = Field(..., description="Health status: healthy | degraded | unavailable | disabled")
    latency_ms: Optional[float] = Field(None, description="Connection latency in milliseconds")
    error_message: Optional[str] = Field(None, description="Error message if unhealthy")
    last_checked: datetime = Field(default_factory=get_current_datetime, description="Last check timestamp")
    
    @field_serializer('last_checked')
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
    uptime_seconds: Optional[float] = Field(None, description="Service uptime in seconds")
    started_at: Optional[datetime] = Field(None, description="Service start time")
    dependencies: Optional[Dict[str, Dict[str, Any]]] = Field(None, description="Dependency health status")
    timestamp: datetime = Field(default_factory=get_current_datetime, description="Status timestamp")
    
    @field_serializer('timestamp', 'started_at')
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


class MemoryQualityMetrics(BaseModel):
    """Response model for memory quality metrics"""
    
    total_memories: int = Field(..., description="Total number of memories")
    low_quality_count: int = Field(..., description="Number of low quality memories")
    low_quality_ratio: float = Field(..., description="Low quality ratio (0.0-1.0)")
    quality_criteria: Dict[str, int] = Field(default_factory=dict, description="Quality issues distribution")
