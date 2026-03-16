"""
Shared statistics calculation for memories.

Used by both the CLI (pmem stats) and the API server (Dashboard) so that
stats are computed with the same logic and data path (get_all + this module).
"""

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from powermem.utils.utils import parse_created_at


def _parse_datetime_for_stats(date_str: Optional[str]) -> Optional[datetime]:
    """Parse created_at for stats; return UTC-aware datetime or None."""
    dt = parse_created_at(date_str)
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt


def _extract_importance(memory: Dict[str, Any]) -> Optional[float]:
    """Extract importance from memory dict; same logic as MemoryService."""
    metadata = memory.get("metadata") if isinstance(memory.get("metadata"), dict) else {}
    intelligence = metadata.get("intelligence") if isinstance(metadata.get("intelligence"), dict) else {}
    candidates = (
        intelligence.get("importance_score"),
        metadata.get("importance"),
        metadata.get("importance_score"),
        memory.get("importance"),
        memory.get("importance_score"),
    )
    for value in candidates:
        if value is None or value == "":
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def calculate_stats_from_memories(memories: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Compute statistics from a list of memory dicts (same logic as Dashboard/API).

    Used by Memory.get_statistics(time_range=...) and MemoryService.get_statistics()
    so CLI and Dashboard show identical results.

    Args:
        memories: List of memory dicts as returned by get_all() (each has
                  created_at, category/metadata, access_count, id, content/memory, etc.)

    Returns:
        Stats dict with total_memories, by_type, avg_importance, top_accessed,
        growth_trend, age_distribution.
    """
    total_memories = len(memories)
    if total_memories == 0:
        return {
            "total_memories": 0,
            "by_type": {},
            "avg_importance": 0.0,
            "top_accessed": [],
            "growth_trend": {},
            "age_distribution": {
                "< 1 day": 0,
                "1-7 days": 0,
                "7-30 days": 0,
                "> 30 days": 0,
            },
        }

    by_type = defaultdict(int)
    total_importance = 0.0
    importance_count = 0
    access_counts = []
    growth_by_date = defaultdict(int)
    age_distribution = {
        "< 1 day": 0,
        "1-7 days": 0,
        "7-30 days": 0,
        "> 30 days": 0,
    }
    now_utc = datetime.now(timezone.utc)

    for m in memories:
        metadata = m.get("metadata") if isinstance(m.get("metadata"), dict) else {}
        mem_type = (
            m.get("category")
            or metadata.get("category")
            or "unknown"
        )
        by_type[mem_type] += 1

        importance = _extract_importance(m)
        if importance is not None and importance > 0:
            total_importance += importance
            importance_count += 1

        raw_access_count = m.get("access_count") or metadata.get("access_count", 0)
        try:
            access_count = int(raw_access_count)
        except (TypeError, ValueError):
            access_count = 0
        access_counts.append({
            "id": m.get("id") or m.get("memory_id"),
            "content": (m.get("memory") or m.get("content") or "")[:100],
            "access_count": access_count,
        })

        created_at = m.get("created_at")
        if created_at:
            date_obj = _parse_datetime_for_stats(created_at)
            if date_obj is not None:
                date_key = date_obj.strftime("%Y-%m-%d")
                growth_by_date[date_key] += 1
                age = (now_utc - date_obj).days
                if age < 1:
                    age_distribution["< 1 day"] += 1
                elif age < 7:
                    age_distribution["1-7 days"] += 1
                elif age < 30:
                    age_distribution["7-30 days"] += 1
                else:
                    age_distribution["> 30 days"] += 1

    access_counts.sort(key=lambda x: x["access_count"], reverse=True)
    top_accessed = access_counts[:10]

    return {
        "total_memories": total_memories,
        "by_type": dict(by_type),
        "avg_importance": round(total_importance / importance_count, 2) if importance_count > 0 else 0.0,
        "top_accessed": top_accessed,
        "growth_trend": dict(growth_by_date),
        "age_distribution": age_distribution,
    }
