"""
PowerMem CLI Output Formatting Utilities

This module provides utilities for formatting CLI output in different formats:
- JSON: For scripting and automation
- Table: Human-readable tabular format
- Plain: Simple text output
"""

import json
from typing import Any, Dict, List, Optional, Union
from datetime import datetime


class OutputFormatter:
    """Formatter for CLI output in various formats."""
    
    FORMAT_JSON = "json"
    FORMAT_TABLE = "table"
    FORMAT_PLAIN = "plain"
    
    def __init__(self, format_type: str = FORMAT_TABLE):
        """
        Initialize the formatter.
        
        Args:
            format_type: Output format type (json, table, plain)
        """
        self.format_type = format_type
    
    def format(self, data: Any, data_type: str = "generic") -> str:
        """
        Format data based on type and format settings.
        
        Args:
            data: Data to format
            data_type: Type of data (memory, memories, stats, config, etc.)
            
        Returns:
            Formatted string
        """
        if self.format_type == self.FORMAT_JSON:
            return self._format_json(data)
        elif self.format_type == self.FORMAT_PLAIN:
            return self._format_plain(data, data_type)
        else:
            return self._format_table(data, data_type)
    
    def _format_json(self, data: Any) -> str:
        """Format data as JSON."""
        return json.dumps(data, indent=2, default=str, ensure_ascii=False)
    
    def _format_plain(self, data: Any, data_type: str) -> str:
        """Format data as plain text."""
        if data_type == "memory":
            return self._format_memory_plain(data)
        elif data_type == "memories":
            return self._format_memories_plain(data)
        elif data_type == "stats":
            return self._format_stats_plain(data)
        elif data_type == "config":
            return self._format_config_plain(data)
        elif data_type == "search_results":
            return self._format_search_results_plain(data)
        else:
            return str(data)
    
    def _format_table(self, data: Any, data_type: str) -> str:
        """Format data as a table."""
        if data_type == "memory":
            return self._format_memory_table(data)
        elif data_type == "memories":
            return self._format_memories_table(data)
        elif data_type == "stats":
            return self._format_stats_table(data)
        elif data_type == "config":
            return self._format_config_table(data)
        elif data_type == "search_results":
            return self._format_search_results_table(data)
        else:
            return self._format_json(data)
    
    # Memory formatting
    def _format_memory_plain(self, memory: Dict[str, Any]) -> str:
        """Format a single memory as plain text."""
        lines = []
        memory_id = memory.get("id") or memory.get("memory_id", "N/A")
        content = memory.get("memory") or memory.get("content", "N/A")
        user_id = memory.get("user_id", "N/A")
        agent_id = memory.get("agent_id", "N/A")
        created_at = memory.get("created_at", "N/A")
        
        lines.append(f"ID: {memory_id}")
        lines.append(f"Content: {content}")
        lines.append(f"User ID: {user_id}")
        lines.append(f"Agent ID: {agent_id}")
        lines.append(f"Created: {created_at}")
        
        metadata = memory.get("metadata", {})
        if metadata:
            lines.append(f"Metadata: {json.dumps(metadata, default=str)}")
        
        return "\n".join(lines)
    
    def _format_memory_table(self, memory: Dict[str, Any]) -> str:
        """Format a single memory as a table."""
        lines = []
        lines.append("=" * 60)
        lines.append("Memory Details")
        lines.append("=" * 60)
        
        memory_id = memory.get("id") or memory.get("memory_id", "N/A")
        content = memory.get("memory") or memory.get("content", "N/A")
        user_id = memory.get("user_id", "N/A")
        agent_id = memory.get("agent_id", "N/A")
        run_id = memory.get("run_id", "N/A")
        created_at = memory.get("created_at", "N/A")
        updated_at = memory.get("updated_at", "N/A")
        
        lines.append(f"{'ID:':<15} {memory_id}")
        lines.append(f"{'Content:':<15} {self._truncate(content, 80)}")
        lines.append(f"{'User ID:':<15} {user_id}")
        lines.append(f"{'Agent ID:':<15} {agent_id}")
        lines.append(f"{'Run ID:':<15} {run_id}")
        lines.append(f"{'Created:':<15} {created_at}")
        lines.append(f"{'Updated:':<15} {updated_at}")
        
        metadata = memory.get("metadata", {})
        if metadata:
            lines.append(f"{'Metadata:':<15} {json.dumps(metadata, default=str)}")
        
        lines.append("=" * 60)
        return "\n".join(lines)
    
    def _format_memories_plain(self, memories: List[Dict[str, Any]]) -> str:
        """Format multiple memories as plain text."""
        if not memories:
            return "No memories found."
        
        lines = []
        for i, memory in enumerate(memories, 1):
            memory_id = memory.get("id") or memory.get("memory_id", "N/A")
            content = memory.get("memory") or memory.get("content", "N/A")
            lines.append(f"{i}. [{memory_id}] {self._truncate(content, 60)}")
        
        return "\n".join(lines)
    
    def _format_memories_table(self, memories: List[Dict[str, Any]]) -> str:
        """Format multiple memories as a table."""
        if not memories:
            return "No memories found."
        
        lines = []
        
        # Header
        header = f"{'ID':<20} {'User ID':<15} {'Agent ID':<15} {'Content':<40}"
        lines.append("=" * len(header))
        lines.append(f"Found {len(memories)} memories")
        lines.append("=" * len(header))
        lines.append(header)
        lines.append("-" * len(header))
        
        # Rows
        for memory in memories:
            memory_id = str(memory.get("id") or memory.get("memory_id", "N/A"))[:18]
            user_id = str(memory.get("user_id", "N/A"))[:13]
            agent_id = str(memory.get("agent_id", "N/A"))[:13]
            content = memory.get("memory") or memory.get("content", "N/A")
            content = self._truncate(content, 38)
            
            lines.append(f"{memory_id:<20} {user_id:<15} {agent_id:<15} {content:<40}")
        
        lines.append("=" * len(header))
        return "\n".join(lines)
    
    # Search results formatting
    def _format_search_results_plain(self, results: Dict[str, Any]) -> str:
        """Format search results as plain text."""
        memories = results.get("results", [])
        if not memories:
            return "No results found."
        
        lines = []
        for i, memory in enumerate(memories, 1):
            memory_id = memory.get("id") or memory.get("memory_id", "N/A")
            content = memory.get("memory") or memory.get("content", "N/A")
            score = memory.get("score", "N/A")
            lines.append(f"{i}. [{memory_id}] (score: {score:.4f}) {self._truncate(content, 50)}")
        
        return "\n".join(lines)
    
    def _format_search_results_table(self, results: Dict[str, Any]) -> str:
        """Format search results as a table."""
        memories = results.get("results", [])
        if not memories:
            return "No results found."
        
        lines = []
        
        # Header
        header = f"{'ID':<20} {'Score':<10} {'User ID':<12} {'Content':<45}"
        lines.append("=" * len(header))
        lines.append(f"Found {len(memories)} results")
        lines.append("=" * len(header))
        lines.append(header)
        lines.append("-" * len(header))
        
        # Rows
        for memory in memories:
            memory_id = str(memory.get("id") or memory.get("memory_id", "N/A"))[:18]
            score = memory.get("score", 0)
            score_str = f"{score:.4f}" if isinstance(score, float) else str(score)
            user_id = str(memory.get("user_id", "N/A"))[:10]
            content = memory.get("memory") or memory.get("content", "N/A")
            content = self._truncate(content, 43)
            
            lines.append(f"{memory_id:<20} {score_str:<10} {user_id:<12} {content:<45}")
        
        lines.append("=" * len(header))
        
        # Relations if present
        relations = results.get("relations", [])
        if relations:
            lines.append(f"\nRelations: {len(relations)} found")
        
        return "\n".join(lines)
    
    # Stats formatting
    def _format_stats_plain(self, stats: Dict[str, Any]) -> str:
        """Format statistics as plain text."""
        lines = []
        for key, value in stats.items():
            if isinstance(value, dict):
                lines.append(f"{key}:")
                for k, v in value.items():
                    lines.append(f"  {k}: {v}")
            else:
                lines.append(f"{key}: {value}")
        return "\n".join(lines)
    
    def _format_stats_table(self, stats: Dict[str, Any]) -> str:
        """Format statistics as a table."""
        lines = []
        lines.append("=" * 50)
        lines.append("PowerMem Statistics")
        lines.append("=" * 50)
        
        # Basic stats
        total = stats.get("total_memories", stats.get("total", "N/A"))
        lines.append(f"{'Total Memories:':<25} {total}")
        
        # By type
        by_type = stats.get("by_type", {})
        if by_type:
            lines.append("\nBy Type:")
            for type_name, count in by_type.items():
                lines.append(f"  {type_name:<20} {count}")
        
        # Age distribution
        age_dist = stats.get("age_distribution", {})
        if age_dist:
            lines.append("\nAge Distribution:")
            for age_range, count in age_dist.items():
                lines.append(f"  {age_range:<20} {count}")
        
        # Average importance
        avg_importance = stats.get("avg_importance")
        if avg_importance is not None:
            lines.append(f"\n{'Avg Importance:':<25} {avg_importance:.4f}")
        
        # Growth trend
        growth = stats.get("growth_trend", {})
        if growth:
            lines.append("\nRecent Growth (last 7 days):")
            sorted_dates = sorted(growth.keys())[-7:]
            for date in sorted_dates:
                lines.append(f"  {date:<20} {growth[date]}")
        
        lines.append("=" * 50)
        return "\n".join(lines)
    
    # Config formatting
    def _format_config_plain(self, config: Dict[str, Any]) -> str:
        """Format configuration as plain text."""
        lines = []
        
        def format_dict(d: dict, prefix: str = "") -> None:
            for key, value in d.items():
                if isinstance(value, dict):
                    lines.append(f"{prefix}{key}:")
                    format_dict(value, prefix + "  ")
                else:
                    # Mask sensitive values
                    if "key" in key.lower() or "password" in key.lower() or "secret" in key.lower():
                        value = "***" if value else "Not set"
                    lines.append(f"{prefix}{key}: {value}")
        
        format_dict(config)
        return "\n".join(lines)
    
    def _format_config_table(self, config: Dict[str, Any]) -> str:
        """Format configuration as a table."""
        lines = []
        lines.append("=" * 60)
        lines.append("PowerMem Configuration")
        lines.append("=" * 60)
        
        # Main sections
        sections = ["llm", "embedder", "vector_store", "graph_store", 
                   "intelligent_memory", "agent_memory", "reranker"]
        
        for section in sections:
            section_config = config.get(section, {})
            if section_config:
                lines.append(f"\n[{section.upper()}]")
                if isinstance(section_config, dict):
                    provider = section_config.get("provider", "N/A")
                    enabled = section_config.get("enabled", True)
                    lines.append(f"  Provider: {provider}")
                    if "enabled" in section_config:
                        lines.append(f"  Enabled: {enabled}")
                    
                    # Show config details (masked)
                    inner_config = section_config.get("config", {})
                    if isinstance(inner_config, dict):
                        for key, value in inner_config.items():
                            if "key" in key.lower() or "password" in key.lower():
                                value = "***" if value else "Not set"
                            lines.append(f"  {key}: {value}")
        
        lines.append("\n" + "=" * 60)
        return "\n".join(lines)
    
    def _truncate(self, text: str, max_length: int) -> str:
        """Truncate text to max length with ellipsis."""
        if not text:
            return ""
        text = str(text).replace("\n", " ").strip()
        if len(text) > max_length:
            return text[:max_length - 3] + "..."
        return text


def format_output(
    data: Any,
    data_type: str = "generic",
    json_output: bool = False,
    plain: bool = False
) -> str:
    """
    Convenience function for formatting output.
    
    Args:
        data: Data to format
        data_type: Type of data
        json_output: Use JSON format
        plain: Use plain text format
        
    Returns:
        Formatted string
    """
    if json_output:
        format_type = OutputFormatter.FORMAT_JSON
    elif plain:
        format_type = OutputFormatter.FORMAT_PLAIN
    else:
        format_type = OutputFormatter.FORMAT_TABLE
    
    formatter = OutputFormatter(format_type)
    return formatter.format(data, data_type)


def print_success(message: str) -> None:
    """Print a success message."""
    import click
    click.echo(click.style(f"[SUCCESS] {message}", fg="green"))


def print_error(message: str) -> None:
    """Print an error message."""
    import click
    click.echo(click.style(f"[ERROR] {message}", fg="red"), err=True)


def print_warning(message: str) -> None:
    """Print a warning message."""
    import click
    click.echo(click.style(f"[WARNING] {message}", fg="yellow"))


def print_info(message: str) -> None:
    """Print an info message."""
    import click
    click.echo(click.style(f"[INFO] {message}", fg="blue"))
