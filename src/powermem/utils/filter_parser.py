from typing import Any, Dict, Optional, Union

def parse_advanced_filters(filters: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    Parse PowerMem advanced filters into storage-compatible filter format.

    Handles:
    - start_time/end_time -> created_at ($gte/$lte)
    - tags -> tags ($in if list)
    - type -> category
    - importance -> importance ($gte)

    Args:
        filters: Raw filters dictionary from API

    Returns:
        Processed filters dictionary compatible with vector stores
    """
    if not filters:
        return None

    # Create a copy to avoid modifying original dict
    parsed = filters.copy()

    # 1. Time Range -> created_at filtering
    # Maps start_time/end_time to created_at range queries
    if "start_time" in parsed or "end_time" in parsed:
        # If created_at filter already exists, use it, otherwise create new dict
        if "created_at" not in parsed:
            parsed["created_at"] = {}
        elif not isinstance(parsed["created_at"], dict):
            # If created_at exists but is not a dict (exact match), wrap it (complex case)
            # For simplicity, we overwrite or assume dict. Let's ensure it's a dict.
            parsed["created_at"] = {"$eq": parsed["created_at"]}

        if "start_time" in parsed:
            parsed["created_at"]["$gte"] = parsed.pop("start_time")
        if "end_time" in parsed:
            parsed["created_at"]["$lte"] = parsed.pop("end_time")

    # 2. Tags -> tags field filtering
    if "tags" in parsed:
        tags = parsed.pop("tags")
        if isinstance(tags, list) and tags:
            # If tags is a list, map to $in operator
            parsed["tags"] = {"$in": tags}
        elif tags:
            # Single tag
            parsed["tags"] = tags

    # 3. Type -> category mapping
    # PowerMem uses 'category' field internally for memory types
    if "type" in parsed:
        parsed["category"] = parsed.pop("type")

    # 4. Importance filtering
    # Usually users want memories with importance >= X
    if "importance" in parsed:
        importance = parsed.pop("importance")
        if isinstance(importance, (int, float)):
            parsed["importance"] = {"$gte": importance}

    return parsed
