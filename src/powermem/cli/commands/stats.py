"""
PowerMem CLI Statistics Commands

This module provides CLI commands for viewing statistics:
- stats: Display memory statistics
"""

import click
import sys
from typing import Optional

from ..main import pass_context, CLIContext
from ..utils.output import (
    format_output,
    print_success,
    print_error,
    print_warning,
    print_info,
)


@click.command(name="stats")
@click.option("--user-id", "-u", help="Filter statistics by user ID")
@click.option("--agent-id", "-a", help="Filter statistics by agent ID")
@click.option("--detailed", "-d", is_flag=True, help="Show detailed statistics")
@pass_context
def stats_cmd(ctx: CLIContext, user_id, agent_id, detailed):
    """
    Display memory statistics.
    
    Shows information about total memories, distribution by type,
    age distribution, and growth trends.
    
    \b
    Examples:
        pmem stats
        pmem stats --user-id user123
        pmem stats --agent-id agent1 --json
        pmem stats --detailed
    """
    try:
        print_info("Gathering statistics...")
        
        # Get statistics from memory
        stats = ctx.memory.get_statistics(
            user_id=user_id,
            agent_id=agent_id,
        )
        
        # Add filter info to stats
        if user_id or agent_id:
            stats["filters"] = {}
            if user_id:
                stats["filters"]["user_id"] = user_id
            if agent_id:
                stats["filters"]["agent_id"] = agent_id
        
        # Get additional details if requested
        if detailed:
            try:
                # Get unique users count
                users = ctx.memory.get_users() if hasattr(ctx.memory, 'get_users') else []
                if users:
                    stats["unique_users"] = len(users)
                    if ctx.verbose:
                        stats["user_list"] = users[:10]  # Show first 10 users
            except Exception:
                pass  # Ignore if get_users is not available
        
        # Format output
        output = format_output(
            stats,
            "stats",
            json_output=ctx.json_output
        )
        click.echo(output)
        
    except Exception as e:
        print_error(f"Failed to get statistics: {e}")
        if ctx.verbose:
            import traceback
            traceback.print_exc()
        sys.exit(1)
