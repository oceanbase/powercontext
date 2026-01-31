"""
PowerMem CLI Memory Commands

This module provides CLI commands for memory operations:
- add: Add new memories
- search: Search for memories
- get: Get a specific memory by ID
- update: Update an existing memory
- delete: Delete a memory
- list: List all memories
- delete-all: Delete all memories
"""

import click
import json
import sys
from typing import Optional, Dict, Any

from ..main import pass_context, CLIContext
from ..utils.output import (
    format_output,
    print_success,
    print_error,
    print_warning,
    print_info,
)


@click.group(name="memory")
def memory_group():
    """Memory operations (add, search, get, update, delete, list)."""
    pass


@click.command(name="add")
@click.argument("content", required=True)
@click.option("--user-id", "-u", help="User ID for the memory")
@click.option("--agent-id", "-a", help="Agent ID for the memory")
@click.option("--run-id", "-r", help="Run/Session ID for the memory")
@click.option(
    "--metadata", "-m",
    help="Metadata as JSON string (e.g., '{\"key\": \"value\"}')"
)
@click.option(
    "--scope",
    type=click.Choice(["private", "agent_group", "user_group", "public"]),
    help="Memory scope"
)
@click.option(
    "--memory-type",
    type=click.Choice(["working", "short_term", "long_term"]),
    help="Memory type"
)
@click.option("--no-infer", is_flag=True, help="Disable intelligent inference")
@pass_context
def add_cmd(ctx: CLIContext, content, user_id, agent_id, run_id, metadata,
            scope, memory_type, no_infer):
    """
    Add a new memory.
    
    \b
    Examples:
        pmem add "User prefers dark mode" --user-id user123
        pmem add "API key is stored in vault" --metadata '{"category": "security"}'
    """
    try:
        # Parse metadata if provided
        meta_dict = None
        if metadata:
            try:
                meta_dict = json.loads(metadata)
            except json.JSONDecodeError as e:
                print_error(f"Invalid metadata JSON: {e}")
                sys.exit(1)
        
        # Call memory.add()
        result = ctx.memory.add(
            messages=content,
            user_id=user_id,
            agent_id=agent_id,
            run_id=run_id,
            metadata=meta_dict,
            scope=scope,
            memory_type=memory_type,
            infer=not no_infer,
        )
        
        # Format output
        if ctx.json_output:
            click.echo(format_output(result, "generic", json_output=True))
        else:
            results = result.get("results", [])
            if results:
                for r in results:
                    event = r.get("event", "ADD")
                    memory_id = r.get("id", "N/A")
                    memory_content = r.get("memory", content)[:50]
                    print_success(f"Memory {event}: ID={memory_id}")
                    if ctx.verbose:
                        click.echo(f"  Content: {memory_content}...")
            else:
                print_warning("No memory was added (may have been deduplicated)")
                
    except Exception as e:
        print_error(f"Failed to add memory: {e}")
        if ctx.verbose:
            import traceback
            traceback.print_exc()
        sys.exit(1)


@click.command(name="search")
@click.argument("query", required=True)
@click.option("--user-id", "-u", help="Filter by user ID")
@click.option("--agent-id", "-a", help="Filter by agent ID")
@click.option("--run-id", "-r", help="Filter by run/session ID")
@click.option("--limit", "-l", default=10, type=int, help="Maximum results (default: 10)")
@click.option("--threshold", "-t", type=float, help="Minimum similarity threshold")
@click.option(
    "--filters", "-f",
    help="Additional filters as JSON string"
)
@pass_context
def search_cmd(ctx: CLIContext, query, user_id, agent_id, run_id, limit,
               threshold, filters):
    """
    Search for memories.
    
    \b
    Examples:
        pmem search "user preferences" --user-id user123
        pmem search "dark mode" --limit 5 --json
    """
    try:
        # Parse filters if provided
        filter_dict = None
        if filters:
            try:
                filter_dict = json.loads(filters)
            except json.JSONDecodeError as e:
                print_error(f"Invalid filters JSON: {e}")
                sys.exit(1)
        
        # Call memory.search()
        result = ctx.memory.search(
            query=query,
            user_id=user_id,
            agent_id=agent_id,
            run_id=run_id,
            filters=filter_dict,
            limit=limit,
            threshold=threshold,
        )
        
        # Format output
        output = format_output(
            result, 
            "search_results",
            json_output=ctx.json_output
        )
        click.echo(output)
        
    except Exception as e:
        print_error(f"Search failed: {e}")
        if ctx.verbose:
            import traceback
            traceback.print_exc()
        sys.exit(1)


@click.command(name="get")
@click.argument("memory_id", required=True, type=int)
@click.option("--user-id", "-u", help="User ID for access control")
@click.option("--agent-id", "-a", help="Agent ID for access control")
@pass_context
def get_cmd(ctx: CLIContext, memory_id, user_id, agent_id):
    """
    Get a specific memory by ID.
    
    \b
    Examples:
        pmem get 123456789
        pmem get 123456789 --user-id user123
    """
    try:
        result = ctx.memory.get(
            memory_id=memory_id,
            user_id=user_id,
            agent_id=agent_id,
        )
        
        if result is None:
            print_error(f"Memory not found: {memory_id}")
            sys.exit(1)
        
        # Format output
        output = format_output(
            result,
            "memory",
            json_output=ctx.json_output
        )
        click.echo(output)
        
    except Exception as e:
        print_error(f"Failed to get memory: {e}")
        if ctx.verbose:
            import traceback
            traceback.print_exc()
        sys.exit(1)


@click.command(name="update")
@click.argument("memory_id", required=True, type=int)
@click.argument("content", required=True)
@click.option("--user-id", "-u", help="User ID for access control")
@click.option("--agent-id", "-a", help="Agent ID for access control")
@click.option(
    "--metadata", "-m",
    help="New metadata as JSON string"
)
@pass_context
def update_cmd(ctx: CLIContext, memory_id, content, user_id, agent_id, metadata):
    """
    Update an existing memory.
    
    \b
    Examples:
        pmem update 123456789 "Updated content"
        pmem update 123456789 "New content" --metadata '{"updated": true}'
    """
    try:
        # Parse metadata if provided
        meta_dict = None
        if metadata:
            try:
                meta_dict = json.loads(metadata)
            except json.JSONDecodeError as e:
                print_error(f"Invalid metadata JSON: {e}")
                sys.exit(1)
        
        result = ctx.memory.update(
            memory_id=memory_id,
            content=content,
            user_id=user_id,
            agent_id=agent_id,
            metadata=meta_dict,
        )
        
        if ctx.json_output:
            click.echo(format_output(result, "generic", json_output=True))
        else:
            print_success(f"Memory updated: ID={memory_id}")
            if ctx.verbose:
                click.echo(format_output(result, "memory"))
                
    except Exception as e:
        print_error(f"Failed to update memory: {e}")
        if ctx.verbose:
            import traceback
            traceback.print_exc()
        sys.exit(1)


@click.command(name="delete")
@click.argument("memory_id", required=True, type=int)
@click.option("--user-id", "-u", help="User ID for access control")
@click.option("--agent-id", "-a", help="Agent ID for access control")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation")
@pass_context
def delete_cmd(ctx: CLIContext, memory_id, user_id, agent_id, yes):
    """
    Delete a memory.
    
    \b
    Examples:
        pmem delete 123456789
        pmem delete 123456789 --yes
    """
    try:
        # Confirm deletion
        if not yes:
            if not click.confirm(f"Delete memory {memory_id}?"):
                print_info("Cancelled")
                return
        
        result = ctx.memory.delete(
            memory_id=memory_id,
            user_id=user_id,
            agent_id=agent_id,
        )
        
        if result:
            print_success(f"Memory deleted: ID={memory_id}")
        else:
            print_error(f"Failed to delete memory: {memory_id}")
            sys.exit(1)
            
    except Exception as e:
        print_error(f"Failed to delete memory: {e}")
        if ctx.verbose:
            import traceback
            traceback.print_exc()
        sys.exit(1)


@click.command(name="list")
@click.option("--user-id", "-u", help="Filter by user ID")
@click.option("--agent-id", "-a", help="Filter by agent ID")
@click.option("--run-id", "-r", help="Filter by run/session ID")
@click.option("--limit", "-l", default=50, type=int, help="Maximum results (default: 50)")
@click.option("--offset", "-o", default=0, type=int, help="Offset for pagination (default: 0)")
@click.option(
    "--sort-by", "-s",
    type=click.Choice(["created_at", "updated_at", "id"]),
    default="created_at",
    help="Sort field (default: created_at)"
)
@click.option(
    "--order",
    type=click.Choice(["asc", "desc"]),
    default="desc",
    help="Sort order (default: desc)"
)
@click.option(
    "--filters", "-f",
    help="Additional filters as JSON string"
)
@pass_context
def list_cmd(ctx: CLIContext, user_id, agent_id, run_id, limit, offset,
             sort_by, order, filters):
    """
    List all memories.
    
    \b
    Examples:
        pmem list --user-id user123
        pmem list --limit 20 --offset 0
        pmem list --sort-by created_at --order desc
    """
    try:
        # Parse filters if provided
        filter_dict = None
        if filters:
            try:
                filter_dict = json.loads(filters)
            except json.JSONDecodeError as e:
                print_error(f"Invalid filters JSON: {e}")
                sys.exit(1)
        
        result = ctx.memory.get_all(
            user_id=user_id,
            agent_id=agent_id,
            run_id=run_id,
            limit=limit,
            offset=offset,
            filters=filter_dict,
            sort_by=sort_by,
            order=order,
        )
        
        # get_all returns {"results": [...]}
        memories = result.get("results", [])
        
        # Format output
        output = format_output(
            memories,
            "memories",
            json_output=ctx.json_output
        )
        click.echo(output)
        
        if not ctx.json_output and memories:
            click.echo(f"\nShowing {len(memories)} memories (offset: {offset})")
        
    except Exception as e:
        print_error(f"Failed to list memories: {e}")
        if ctx.verbose:
            import traceback
            traceback.print_exc()
        sys.exit(1)


@click.command(name="delete-all")
@click.option("--user-id", "-u", help="Filter by user ID")
@click.option("--agent-id", "-a", help="Filter by agent ID")
@click.option("--run-id", "-r", help="Filter by run/session ID")
@click.option("--confirm", is_flag=True, help="Confirm deletion (required)")
@pass_context
def delete_all_cmd(ctx: CLIContext, user_id, agent_id, run_id, confirm):
    """
    Delete all memories matching the filters.
    
    WARNING: This operation is irreversible!
    
    \b
    Examples:
        pmem delete-all --user-id user123 --confirm
        pmem delete-all --run-id session1 --confirm
    """
    if not confirm:
        print_error("This will delete ALL matching memories!")
        print_error("Add --confirm flag to proceed.")
        sys.exit(1)
    
    # Build filter description
    filters = []
    if user_id:
        filters.append(f"user_id={user_id}")
    if agent_id:
        filters.append(f"agent_id={agent_id}")
    if run_id:
        filters.append(f"run_id={run_id}")
    
    filter_desc = ", ".join(filters) if filters else "ALL memories"
    
    # Double confirm for safety
    if not click.confirm(f"Delete {filter_desc}? This cannot be undone!"):
        print_info("Cancelled")
        return
    
    try:
        result = ctx.memory.delete_all(
            user_id=user_id,
            agent_id=agent_id,
            run_id=run_id,
        )
        
        if result:
            print_success(f"All matching memories deleted: {filter_desc}")
        else:
            print_warning("No memories were deleted (none matched filters)")
            
    except Exception as e:
        print_error(f"Failed to delete memories: {e}")
        if ctx.verbose:
            import traceback
            traceback.print_exc()
        sys.exit(1)


# Add commands to group
memory_group.add_command(add_cmd)
memory_group.add_command(search_cmd)
memory_group.add_command(get_cmd)
memory_group.add_command(update_cmd)
memory_group.add_command(delete_cmd)
memory_group.add_command(list_cmd)
memory_group.add_command(delete_all_cmd)
