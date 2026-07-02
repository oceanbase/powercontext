"""
Memory Import/Export utilities

This module provides functions for exporting memories to JSON/CSV
and importing memories from JSON/CSV files.
"""

import json
import csv
import io as io_module
from typing import List, Dict, Any
from datetime import datetime


def _normalize_memory_record(memory: Dict[str, Any]) -> Dict[str, Any]:
    """Return a memory row with a canonical content field for import/export."""
    normalized = dict(memory)
    normalized["content"] = (
        normalized.get("content")
        or normalized.get("memory")
        or normalized.get("messages")
        or ""
    )
    return normalized


def export_to_json(memories: List[Dict[str, Any]]) -> str:
    """Export memories to JSON format."""
    def default_serializer(obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        raise TypeError(f"Object of type {type(obj)} is not JSON serializable")
    normalized = [_normalize_memory_record(memory) for memory in memories]
    return json.dumps(normalized, indent=2, default=default_serializer)


def export_to_csv(memories: List[Dict[str, Any]]) -> str:
    """Export memories to CSV format."""
    if not memories:
        return ""
    output = io_module.StringIO()
    fieldnames = [
        'id', 'content', 'role', 'user_id', 'agent_id', 'run_id',
        'metadata', 'created_at', 'updated_at',
    ]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    for memory in memories:
        normalized = _normalize_memory_record(memory)
        row = {
            'id': normalized.get('id', ''),
            'content': normalized.get('content', ''),
            'role': normalized.get('role', 'user'),
            'user_id': normalized.get('user_id', ''),
            'agent_id': normalized.get('agent_id', ''),
            'run_id': normalized.get('run_id', ''),
            'metadata': json.dumps(normalized.get('metadata', {})),
            'created_at': str(normalized.get('created_at', '')),
            'updated_at': str(normalized.get('updated_at', ''))
        }
        writer.writerow(row)
    return output.getvalue()


def import_from_json(json_str: str) -> List[Dict[str, Any]]:
    """Import memories from JSON format."""
    memories = json.loads(json_str)
    result = []
    for memory in memories:
        if isinstance(memory, dict):
            normalized = _normalize_memory_record(memory)
            cleaned = {
                'id': normalized.get('id'),
                'content': normalized.get('content', ''),
                'role': normalized.get('role', 'user'),
                'user_id': normalized.get('user_id'),
                'agent_id': normalized.get('agent_id'),
                'run_id': normalized.get('run_id'),
                'metadata': normalized.get('metadata', {}),
                'created_at': normalized.get('created_at'),
                'updated_at': normalized.get('updated_at')
            }
            result.append(cleaned)
    return result


def import_from_csv(csv_str: str) -> List[Dict[str, Any]]:
    """Import memories from CSV format."""
    if not csv_str.strip():
        return []
    input_stream = io_module.StringIO(csv_str)
    reader = csv.DictReader(input_stream)
    memories = []
    for row in reader:
        normalized = _normalize_memory_record(row)
        memory = {
            'id': normalized.get('id'),
            'content': normalized.get('content', ''),
            'role': normalized.get('role', 'user'),
            'user_id': normalized.get('user_id'),
            'agent_id': normalized.get('agent_id'),
            'run_id': normalized.get('run_id'),
            'metadata': json.loads(normalized.get('metadata', '{}')),
            'created_at': normalized.get('created_at'),
            'updated_at': normalized.get('updated_at')
        }
        memories.append(memory)
    return memories
