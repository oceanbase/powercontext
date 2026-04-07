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
        user_id = self._nullable_display(memory.get("user_id"))
        agent_id = self._nullable_display(memory.get("agent_id"))
        created_at = memory.get("created_at", "N/A")
        
        lines.append(f"ID: {memory_id}")
        lines.append(f"Content: {content}")
        lines.append(f"User ID: {user_id}")
        lines.append(f"Agent ID: {agent_id}")
        lines.append(f"Created: {created_at}")
        
        metadata = memory.get("metadata", {})
        if metadata:
            lines.append(f"Metadata: {json.dumps(metadata, default=str, ensure_ascii=False)}")
        
        return "\n".join(lines)
    
    def _format_memory_table(self, memory: Dict[str, Any]) -> str:
        """Format a single memory as a table."""
        lines = []
        lines.append("=" * 60)
        lines.append("Memory Details")
        lines.append("=" * 60)
        
        memory_id = memory.get("id") or memory.get("memory_id", "N/A")
        content = memory.get("memory") or memory.get("content", "N/A")
        user_id = self._nullable_display(memory.get("user_id"))
        agent_id = self._nullable_display(memory.get("agent_id"))
        run_id = self._nullable_display(memory.get("run_id"))
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
            lines.append(f"{'Metadata:':<15} {json.dumps(metadata, default=str, ensure_ascii=False)}")
        
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
        header = f"{'ID':<20} {'User ID':<22} {'Agent ID':<22} {'Content':<26}"
        lines.append("=" * len(header))
        lines.append(f"Found {len(memories)} memories")
        lines.append("=" * len(header))
        lines.append(header)
        lines.append("-" * len(header))
        
        # Rows
        for memory in memories:
            memory_id = str(memory.get("id") or memory.get("memory_id", "N/A"))[:18]
            user_id = self._truncate(self._nullable_display(memory.get("user_id")), 20)
            agent_id = self._truncate(self._nullable_display(memory.get("agent_id")), 20)
            content = memory.get("memory") or memory.get("content", "N/A")
            content = self._truncate(content, 24)
            
            lines.append(f"{memory_id:<20} {user_id:<22} {agent_id:<22} {content:<26}")
        
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
            user_id = self._nullable_display(memory.get("user_id"))[:10]
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
    
    # Config formatting: order and (Required)/(Optional) follow .env.example; timezone is separate (before Database)
    _CONFIG_SECTIONS = [
        ("Timezone", ""),
        ("Database", "Required"),
        ("LLM", "Required"),
        ("Embedding", "Required"),
        ("Rerank", "Optional"),
        ("Agent", "Optional"),
        ("Intelligent Memory", "Optional"),
        ("Performance", "Optional"),
        ("Security", "Optional"),
        ("Telemetry", "Optional"),
        ("Audit", "Optional"),
        ("Logging", "Optional"),
        ("Graph Store", "Optional"),
        ("Sparse Embedding", "Optional"),
        ("Query Rewrite", "Optional"),
        ("PowerMem HTTP API Server", "Optional"),
    ]
    _CONFIG_SECTION_KEYS = [
        ["timezone"],
        ["vector_store"],
        ["llm"],
        ["embedder"],
        ["reranker"],
        ["agent_memory"],
        ["intelligent_memory", "memory_decay"],
        ["performance"],
        ["security"],
        ["telemetry"],
        ["audit"],
        ["logging"],
        ["graph_store"],
        ["sparse_embedder"],
        ["query_rewrite"],
        ["server"],
    ]

    def _format_config_plain(self, config: Dict[str, Any]) -> str:
        """Format configuration as plain text."""
        lines = []
        
        def format_dict(d: dict, prefix: str = "") -> None:
            for key, value in d.items():
                if isinstance(value, dict):
                    lines.append(f"{prefix}{key}:")
                    format_dict(value, prefix + "  ")
                else:
                    lines.append(f"{prefix}{key}: {value}")
        
        format_dict(config)
        return "\n".join(lines)
    
    def _format_config_table(self, config: Dict[str, Any]) -> str:
        """Format configuration in the order of .env.example (15 blocks); show all keys in each block."""
        lines = []
        lines.append("=" * 60)
        lines.append("PowerMem Configuration")
        lines.append("=" * 60)

        def format_section_value(value: Any, indent: str, keys_shown_above: Optional[set] = None) -> None:
            """Output config keys in uppercase; skip unset/empty; skip key if already shown at parent level (e.g. ENABLED once)."""
            if keys_shown_above is None:
                keys_shown_above = set()
            if isinstance(value, dict):
                for k, v in value.items():
                    if k.lower() in keys_shown_above:
                        continue
                    if v is None:
                        continue
                    if isinstance(v, str) and v.strip() == "":
                        continue
                    if isinstance(v, dict):
                        if not v:
                            continue
                        key_display = k.upper()
                        lines.append(f"{indent}{key_display}:")
                        format_section_value(v, indent + "  ", keys_shown_above)
                    else:
                        key_display = k.upper()
                        lines.append(f"{indent}{key_display}: {v}")
                    keys_shown_above.add(k.lower())
            else:
                lines.append(f"{indent}{value}")

        for i, (title, tag) in enumerate(self._CONFIG_SECTIONS):
            config_keys = self._CONFIG_SECTION_KEYS[i] if i < len(self._CONFIG_SECTION_KEYS) else []
            has_any = False
            for key in config_keys:
                if config.get(key) is not None:
                    has_any = True
                    break
            if not has_any:
                continue  # Only show sections present in config (fixes --section showing others as "(not set)")
            header = f"[{title.upper()}]" if not tag else f"[{title.upper()} ({tag})]"
            lines.append(f"\n{header}")
            for key in config_keys:
                section_config = config.get(key)
                if section_config is not None:
                    indent = "  "
                    if len(config_keys) > 1:
                        sublabel = key.upper().replace("_", " ")
                        lines.append(f"  {sublabel}:")
                        indent = "    "
                    if isinstance(section_config, dict):
                        format_section_value(section_config, indent)
                    else:
                        lines.append(f"{indent}{section_config}")

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

    @staticmethod
    def _nullable_display(value: Any) -> str:
        """Display value for nullable DB fields: None or empty string -> 'NULL' (consistent with database)."""
        if value is None or (isinstance(value, str) and value.strip() == ""):
            return "NULL"
        return str(value)


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
