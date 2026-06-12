"""
Data conversion utilities for PowerMem API
"""

import logging
from typing import Any, Dict, List, Optional
from datetime import datetime
from ..models.response import MemoryResponse, SearchResult, UserProfileResponse


logger = logging.getLogger("server")


def memory_to_response(memory_data: Dict[str, Any]) -> MemoryResponse:
    """
    Convert memory dictionary to MemoryResponse model.
    
    Args:
        memory_data: Memory data dictionary
        
    Returns:
        MemoryResponse instance
    """
    # Handle different memory data formats
    memory_id = memory_data.get("memory_id") or memory_data.get("id")
    # Handle field name mismatch: storage uses "data" but API expects "content"
    # get_all returns "memory" field, get_memory returns "content" field
    content = memory_data.get("memory") or memory_data.get("data") or memory_data.get("content") or memory_data.get("memory_content", "")
    
    # Parse timestamps
    created_at = None
    updated_at = None
    
    if "created_at" in memory_data:
        created_at = _parse_datetime(memory_data["created_at"])
    if "updated_at" in memory_data:
        updated_at = _parse_datetime(memory_data["updated_at"])
    
    return MemoryResponse(
        memory_id=memory_id,
        content=content,
        user_id=memory_data.get("user_id"),
        agent_id=memory_data.get("agent_id"),
        run_id=memory_data.get("run_id"),
        metadata=memory_data.get("metadata", {}),
        created_at=created_at,
        updated_at=updated_at,
    )


def memory_dict_to_response(memory_dict: Dict[str, Any]) -> MemoryResponse:
    """
    Convert memory dict from SDK to MemoryResponse.
    
    Args:
        memory_dict: Memory dictionary from SDK
        
    Returns:
        MemoryResponse instance
    """
    return memory_to_response(memory_dict)


def search_result_to_response(result: Dict[str, Any]) -> SearchResult:
    """
    Convert search result dictionary to SearchResult model.
    
    Args:
        result: Search result dictionary
        
    Returns:
        SearchResult instance
    """
    # Handle different field names for content: "memory", "content", "memory_content", "data"
    content = (
        result.get("memory") or
        result.get("content") or
        result.get("memory_content") or
        result.get("data") or
        ""
    )

    memory_id = result.get("memory_id") or result.get("id")
    raw_score = result.get("score")
    similarity = result.get("similarity")
    resolved_score = raw_score if raw_score is not None else similarity
    created_at = _parse_datetime(result.get("created_at")) if "created_at" in result else None
    updated_at = _parse_datetime(result.get("updated_at")) if "updated_at" in result else None

    logger.debug(
        "Search result score conversion: memory_id=%s raw_score=%r similarity=%r resolved_score=%r",
        memory_id,
        raw_score,
        similarity,
        resolved_score,
    )
    if resolved_score is None:
        logger.warning(
            "Search result score resolved to null during API conversion: "
            "memory_id=%s raw_score=%r similarity=%r score_key_present=%s similarity_key_present=%s",
            memory_id,
            raw_score,
            similarity,
            "score" in result,
            "similarity" in result,
        )

    return SearchResult(
        memory_id=memory_id,
        content=content,
        score=resolved_score,
        metadata=result.get("metadata", {}),
        user_id=result.get("user_id"),
        agent_id=result.get("agent_id"),
        run_id=result.get("run_id"),
        created_at=created_at,
        updated_at=updated_at,
    )


def user_profile_to_response(
    user_id: str,
    profile_data: Optional[Dict[str, Any]] = None,
) -> UserProfileResponse:
    """
    Convert user profile data to UserProfileResponse model.
    
    Args:
        user_id: User ID
        profile_data: Profile data dictionary
        
    Returns:
        UserProfileResponse instance
    """
    if not profile_data:
        return UserProfileResponse(
            user_id=user_id,
            profile_content=None,
            topics=None,
            updated_at=None,
        )
    
    updated_at = None
    if "updated_at" in profile_data:
        updated_at = _parse_datetime(profile_data["updated_at"])
    
    return UserProfileResponse(
        user_id=user_id,
        profile_content=profile_data.get("profile_content"),
        topics=profile_data.get("topics"),
        updated_at=updated_at,
    )


def _parse_datetime(value: Any) -> Optional[datetime]:
    """
    Parse datetime from various formats.
    
    Args:
        value: Datetime value (str, datetime, or None)
        
    Returns:
        datetime object or None
    """
    if value is None:
        return None
    
    if isinstance(value, datetime):
        return value
    
    if isinstance(value, str):
        try:
            # Try ISO format
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            try:
                # Try other common formats
                return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                return None
    
    return None
