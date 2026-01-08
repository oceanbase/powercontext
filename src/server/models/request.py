"""
Request models for PowerMem API
"""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class MemoryCreateRequest(BaseModel):
    """Request model for creating a memory"""
    
    content: str = Field(..., description="Memory content (string, dict, or list of dicts)")
    user_id: Optional[str] = Field(None, description="User identifier")
    agent_id: Optional[str] = Field(None, description="Agent identifier")
    run_id: Optional[str] = Field(None, description="Run/conversation identifier")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Additional metadata")
    filters: Optional[Dict[str, Any]] = Field(None, description="Filter metadata for advanced filtering")
    scope: Optional[str] = Field(None, description="Memory scope (e.g., 'user', 'agent', 'session')")
    memory_type: Optional[str] = Field(None, description="Memory type classification")
    infer: bool = Field(True, description="Enable intelligent memory processing")


class MemoryItem(BaseModel):
    """Single memory item for batch creation"""
    
    content: str = Field(..., description="Memory content")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Additional metadata for this memory")
    filters: Optional[Dict[str, Any]] = Field(None, description="Filter metadata for this memory")
    scope: Optional[str] = Field(None, description="Memory scope")
    memory_type: Optional[str] = Field(None, description="Memory type classification")


class MemoryBatchCreateRequest(BaseModel):
    """Request model for creating multiple memories in batch"""
    
    memories: List[MemoryItem] = Field(..., description="List of memories to create", min_length=1, max_length=100)
    user_id: Optional[str] = Field(None, description="User identifier (applied to all memories)")
    agent_id: Optional[str] = Field(None, description="Agent identifier (applied to all memories)")
    run_id: Optional[str] = Field(None, description="Run/conversation identifier (applied to all memories)")
    infer: bool = Field(True, description="Enable intelligent memory processing")


class MemoryUpdateRequest(BaseModel):
    """Request model for updating a memory"""
    
    content: Optional[str] = Field(None, description="New content for the memory")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Updated metadata")


class MemoryUpdateItem(BaseModel):
    """Single memory update item for batch update"""
    
    memory_id: int = Field(..., description="Memory ID to update")
    content: Optional[str] = Field(None, description="New content for the memory (optional)")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Updated metadata (optional)")


class MemoryBatchUpdateRequest(BaseModel):
    """Request model for updating multiple memories in batch"""
    
    updates: List[MemoryUpdateItem] = Field(..., description="List of memory updates", min_length=1, max_length=100)
    user_id: Optional[str] = Field(None, description="User ID for access control")
    agent_id: Optional[str] = Field(None, description="Agent ID for access control")


class SearchRequest(BaseModel):
    """Request model for searching memories"""
    
    query: str = Field(..., description="Search query")
    user_id: Optional[str] = Field(None, description="Filter by user ID")
    agent_id: Optional[str] = Field(None, description="Filter by agent ID")
    run_id: Optional[str] = Field(None, description="Filter by run ID")
    filters: Optional[Dict[str, Any]] = Field(None, description="Additional filters")
    limit: int = Field(default=30, ge=1, le=100, description="Maximum number of results")


class UserProfileUpdateRequest(BaseModel):
    """Request model for updating user profile"""
    
    profile_content: Optional[str] = Field(None, description="Profile content text")
    topics: Optional[Dict[str, Any]] = Field(None, description="Structured topics dictionary")


class AgentMemoryCreateRequest(BaseModel):
    """Request model for creating agent memory"""
    
    content: str = Field(..., description="Memory content")
    user_id: Optional[str] = Field(None, description="User ID")
    run_id: Optional[str] = Field(None, description="Run ID")


class AgentMemoryShareRequest(BaseModel):
    """Request model for sharing memories between agents"""
    
    target_agent_id: str = Field(..., description="Target agent ID to share with")
    memory_ids: Optional[List[int]] = Field(None, description="Specific memory IDs to share (None for all)")


class BulkDeleteRequest(BaseModel):
    """Request model for bulk deleting memories"""
    
    memory_ids: List[int] = Field(..., description="List of memory IDs to delete", min_length=1, max_length=100)
    user_id: Optional[str] = Field(None, description="User ID for access control")
    agent_id: Optional[str] = Field(None, description="Agent ID for access control")
