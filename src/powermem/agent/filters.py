"""Filter helpers for agent memory managers."""

from enum import Enum
from typing import Any, Dict, Iterable, Optional


_MISSING = object()


def _normalize_filter_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    return value


def _get_path_value(data: Dict[str, Any], key_path: str) -> Any:
    current: Any = data
    for key in key_path.split("."):
        if not isinstance(current, dict) or key not in current:
            return _MISSING
        current = current[key]
    return current


def _values_equal(actual: Any, expected: Any) -> bool:
    actual = _normalize_filter_value(actual)
    expected = _normalize_filter_value(expected)

    if isinstance(expected, (list, tuple, set)):
        expected_values = {_normalize_filter_value(item) for item in expected}
        if isinstance(actual, (list, tuple, set)):
            actual_values = {_normalize_filter_value(item) for item in actual}
            return bool(actual_values & expected_values)
        return actual in expected_values

    if isinstance(actual, (list, tuple, set)):
        return expected in {_normalize_filter_value(item) for item in actual}

    return actual == expected


def _candidate_values(memory: Dict[str, Any], key: str) -> Iterable[Any]:
    top_level = _get_path_value(memory, key)
    if top_level is not _MISSING:
        yield top_level

    metadata = memory.get("metadata")
    if isinstance(metadata, dict):
        if key.startswith("metadata."):
            metadata_value = _get_path_value(metadata, key[len("metadata."):])
        else:
            metadata_value = _get_path_value(metadata, key)
        if metadata_value is not _MISSING:
            yield metadata_value


def matches_memory_filters(
    memory: Dict[str, Any],
    filters: Optional[Dict[str, Any]],
) -> bool:
    """Return whether an agent memory satisfies all simple equality filters.

    Agent APIs historically accepted unqualified metadata filters such as
    {"category": "communication"} and docs also show dotted paths like
    {"metadata.scope": "AGENT"}. Check top-level fields first, then metadata.
    """
    if not filters:
        return True

    for key, expected in filters.items():
        if not any(
            _values_equal(actual, expected)
            for actual in _candidate_values(memory, key)
        ):
            return False
    return True
