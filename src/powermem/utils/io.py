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


def export_to_json(memories: List[Dict[str, Any]]) -> str:
    """Export memories to JSON format."""
    def default_serializer(obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        raise TypeError(f"Object of type {type(obj)} is not JSON serializable")
    return json.dumps(memories, indent=2, default=default_serializer)


def export_to_csv(memories: List[Dict[str, Any]]) -> str:
    """Export memories to CSV format."""
    if not memories:
        return ""
    output = io_module.StringIO()
    fieldnames = ['id', 'content', 'role', 'metadata', 'created_at', 'updated_at']
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    for memory in memories:
        row = {
            'id': memory.get('id', ''),
            'content': memory.get('content', ''),
            'role': memory.get('role', 'user'),
            'metadata': json.dumps(memory.get('metadata', {})),
            'created_at': str(memory.get('created_at', '')),
            'updated_at': str(memory.get('updated_at', ''))
        }
        writer.writerow(row)
    return output.getvalue()


def import_from_json(json_str: str) -> List[Dict[str, Any]]:
    """Import memories from JSON format."""
    memories = json.loads(json_str)
    result = []
    for memory in memories:
        if isinstance(memory, dict):
            cleaned = {
                'id': memory.get('id'),
                'content': memory.get('content', ''),
                'role': memory.get('role', 'user'),
                'metadata': memory.get('metadata', {}),
                'created_at': memory.get('created_at'),
                'updated_at': memory.get('updated_at')
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
        memory = {
            'id': row.get('id'),
            'content': row.get('content', ''),
            'role': row.get('role', 'user'),
            'metadata': json.loads(row.get('metadata', '{}')),
            'created_at': row.get('created_at'),
            'updated_at': row.get('updated_at')
        }
        memories.append(memory)
    return memories
