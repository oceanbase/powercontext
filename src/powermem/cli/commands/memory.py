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

import logging
import os
import click
import json
import sys
from typing import Optional, Dict, Any

from ..main import pass_context, CLIContext, json_option
from ..utils.output import (
    format_output,
    print_success,
    print_error,
    print_warning,
    print_info,
)

logger = logging.getLogger(__name__)


@click.group(name="memory")
def memory_group():
    """Memory operations (add, search, get, update, delete, list)."""
    pass


@click.command(name="add")
@click.argument("content", required=False, default=None)
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
@click.option("--with-profile", is_flag=True, help="Also extract user profile (requires --user-id)")
@click.option(
    "--profile-type",
    type=click.Choice(["content", "topics"]),
    default="content",
    help='Profile extraction type: "content" (free-text) or "topics" (structured). Default: content',
)
@click.option("--native-language", help="ISO 639-1 language code for profile extraction (e.g., zh, en)")
@click.option(
    "--batch",
    "batch_file",
    type=click.Path(exists=True),
    help="JSON file with batch memories to add (mutually exclusive with CONTENT)",
)
@json_option
@pass_context
def add_cmd(ctx: CLIContext, content, user_id, agent_id, run_id, metadata,
            scope, memory_type, no_infer, with_profile, profile_type,
            native_language, batch_file, json_output):
    """
    Add a new memory, optionally with user profile extraction.

    \b
    Examples:
        pmem memory add "User prefers dark mode" --user-id user123
        pmem memory add "API key is stored in vault" --metadata '{"category": "security"}'
        pmem memory add "I like Python" --user-id u1 --with-profile
        pmem memory add --batch memories.json --user-id user123
    """
    ctx.json_output = ctx.json_output or json_output

    if batch_file and content:
        print_error("Cannot use both CONTENT argument and --batch option")
        sys.exit(1)

    if batch_file:
        _add_batch(ctx, batch_file, user_id, agent_id, run_id, no_infer)
        return

    if not content:
        print_error("Missing argument 'CONTENT'. Provide text or use --batch <file>.")
        sys.exit(1)

    try:
        meta_dict = None
        if metadata:
            try:
                meta_dict = json.loads(metadata)
            except json.JSONDecodeError as e:
                print_error(f"Invalid metadata JSON: {e}")
                sys.exit(1)

        if with_profile:
            if not user_id:
                print_error("--with-profile requires --user-id")
                sys.exit(1)
            result = ctx.user_memory.add(
                messages=content,
                user_id=user_id,
                agent_id=agent_id,
                run_id=run_id,
                metadata=meta_dict,
                scope=scope,
                memory_type=memory_type,
                infer=not no_infer,
                profile_type=profile_type,
                native_language=native_language,
            )
        else:
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

            if with_profile and result.get("profile_extracted"):
                print_success("User profile extracted")
                
    except Exception as e:
        print_error(f"Failed to add memory: {e}")
        if ctx.verbose:
            logger.exception("CLI command failed")
        sys.exit(1)


def _add_batch(ctx: CLIContext, batch_file, user_id, agent_id, run_id, no_infer):
    """Handle batch add from a JSON file."""
    try:
        with open(batch_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        print_error(f"Invalid JSON in batch file: {e}")
        sys.exit(1)
    except OSError as e:
        print_error(f"Cannot read batch file: {e}")
        sys.exit(1)

    if not isinstance(data, list):
        print_error("Batch file must contain a JSON array of memory objects")
        sys.exit(1)

    if not data:
        if ctx.json_output:
            _print_batch_result(success=0, failed=0, total=0)
        else:
            print_warning("Batch file is empty, nothing to add")
        return

    success = 0
    failed = 0
    for i, item in enumerate(data):
        try:
            if isinstance(item, str):
                messages = item
                meta = None
            elif isinstance(item, dict):
                messages = item.get("content") or item.get("messages") or item.get("memory", "")
                meta = item.get("metadata")
            else:
                _print_batch_warning(ctx, f"Skipping item {i}: unsupported type {type(item).__name__}")
                failed += 1
                continue

            if not messages:
                _print_batch_warning(ctx, f"Skipping item {i}: empty content")
                failed += 1
                continue

            mem_user_id = (item.get("user_id") if isinstance(item, dict) else None) or user_id
            mem_agent_id = (item.get("agent_id") if isinstance(item, dict) else None) or agent_id
            mem_run_id = (item.get("run_id") if isinstance(item, dict) else None) or run_id

            ctx.memory.add(
                messages=messages,
                user_id=mem_user_id,
                agent_id=mem_agent_id,
                run_id=mem_run_id,
                metadata=meta,
                infer=not no_infer,
            )
            success += 1
        except Exception as e:
            _print_batch_warning(ctx, f"Item {i} failed: {e}")
            failed += 1

    if ctx.json_output:
        _print_batch_result(success=success, failed=failed, total=len(data))
    else:
        print_success(f"Batch add complete: {success} succeeded, {failed} failed (total: {len(data)})")


def _print_batch_warning(ctx: CLIContext, message: str) -> None:
    """Keep stdout parseable when callers request JSON output."""
    if not ctx.json_output:
        print_warning(message)


def _print_batch_result(success: int, failed: int, total: int) -> None:
    click.echo(format_output(
        {"success": success, "failed": failed, "total": total},
        "generic",
        json_output=True,
    ))


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
@click.option("--add-profile", is_flag=True, help="Include user profile in results (requires --user-id)")
@json_option
@pass_context
def search_cmd(ctx: CLIContext, query, user_id, agent_id, run_id, limit,
               threshold, filters, add_profile, json_output):
    """
    Search for memories. Use --threshold / -t to only return results with
    similarity score >= threshold (e.g. 0.3 for score > 0.3).
    
    \b
    Examples:
        pmem memory search "user preferences" --user-id user123
        pmem memory search "dark mode" --limit 5 --json
        pmem memory search "123" -t 0.3
        pmem memory search "prefs" --user-id u1 --add-profile
    """
    ctx.json_output = ctx.json_output or json_output
    try:
        filter_dict = None
        if filters:
            try:
                filter_dict = json.loads(filters)
            except json.JSONDecodeError as e:
                print_error(f"Invalid filters JSON: {e}")
                sys.exit(1)

        if add_profile:
            if not user_id:
                print_error("--add-profile requires --user-id")
                sys.exit(1)
            result = ctx.user_memory.search(
                query=query,
                user_id=user_id,
                agent_id=agent_id,
                run_id=run_id,
                filters=filter_dict,
                limit=limit,
                threshold=threshold,
                add_profile=True,
            )
        else:
            result = ctx.memory.search(
                query=query,
                user_id=user_id,
                agent_id=agent_id,
                run_id=run_id,
                filters=filter_dict,
                limit=limit,
                threshold=threshold,
            )
        
        output = format_output(
            result, 
            "search_results",
            json_output=ctx.json_output
        )
        click.echo(output)

        if add_profile and not ctx.json_output:
            profile_content = result.get("profile_content")
            topics = result.get("topics")
            if profile_content or topics:
                click.echo("\n--- User Profile ---")
                if profile_content:
                    click.echo(f"Profile: {profile_content}")
                if topics:
                    click.echo(f"Topics: {json.dumps(topics, ensure_ascii=False, indent=2)}")
        
    except Exception as e:
        print_error(f"Search failed: {e}")
        if ctx.verbose:
            logger.exception("CLI command failed")
        sys.exit(1)


@click.command(name="get")
@click.argument("memory_id", required=True, type=int)
@click.option("--user-id", "-u", help="User ID for access control")
@click.option("--agent-id", "-a", help="Agent ID for access control")
@json_option
@pass_context
def get_cmd(ctx: CLIContext, memory_id, user_id, agent_id, json_output):
    """
    Get a specific memory by ID.
    
    MEMORY_ID is globally unique. If --user-id or --agent-id is provided, the
    memory is returned only when it belongs to that user/agent (access control).
    
    \b
    Examples:
        pmem memory get 123456789
        pmem memory get 123456789 --user-id user123
    """
    ctx.json_output = ctx.json_output or json_output
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
            logger.exception("CLI command failed")
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
@json_option
@pass_context
def update_cmd(ctx: CLIContext, memory_id, content, user_id, agent_id, metadata, json_output):
    """
    Update an existing memory.
    
    \b
    Examples:
        pmem memory update 123456789 "Updated content"
        pmem memory update 123456789 "New content" --metadata '{"updated": true}'
    """
    ctx.json_output = ctx.json_output or json_output
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
        
        # None or invalid result means not found or permission denied (see issue #298)
        if result is None or not isinstance(result, dict) or not result:
            print_error(f"Memory not found or access denied: {memory_id}")
            sys.exit(1)
        
        if ctx.json_output:
            click.echo(format_output(result, "generic", json_output=True))
        else:
            print_success(f"Memory updated: ID={memory_id}")
            if ctx.verbose:
                click.echo(format_output(result, "memory"))
                
    except Exception as e:
        print_error(f"Failed to update memory: {e}")
        if ctx.verbose:
            logger.exception("CLI command failed")
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
        pmem memory delete 123456789
        pmem memory delete 123456789 --yes
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
            # Consistent with update: same prompt for not found or access denied (issue #299)
            print_error(f"Memory not found or access denied: {memory_id}")
            sys.exit(1)
            
    except Exception as e:
        print_error(f"Failed to delete memory: {e}")
        if ctx.verbose:
            logger.exception("CLI command failed")
        sys.exit(1)


@click.command(name="list")
@click.option("--user-id", "-u", help="Filter by user ID")
@click.option("--agent-id", "-a", help="Filter by agent ID")
@click.option("--run-id", "-r", help="Filter by run/session ID")
@click.option("--limit", "-l", default=50, type=int, help="Maximum results (default: 50). Use a negative value (e.g. -1) for no limit.")
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
@json_option
@pass_context
def list_cmd(ctx: CLIContext, user_id, agent_id, run_id, limit, offset,
             sort_by, order, filters, json_output):
    """
    List all memories.
    
    \b
    Examples:
        pmem memory list --user-id user123
        pmem memory list --limit 20 --offset 0
        pmem memory list --sort-by created_at --order desc
    """
    ctx.json_output = ctx.json_output or json_output
    try:
        # Parse filters if provided
        filter_dict = None
        if filters:
            try:
                filter_dict = json.loads(filters)
            except json.JSONDecodeError as e:
                print_error(f"Invalid filters JSON: {e}")
                sys.exit(1)

        # Negative limit (e.g. -1, -2) means no limit; pass None so backend does not add LIMIT (MySQL/OceanBase reject negative LIMIT)
        effective_limit = None if limit < 0 else limit

        result = ctx.memory.get_all(
            user_id=user_id,
            agent_id=agent_id,
            run_id=run_id,
            limit=effective_limit,
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
            logger.exception("CLI command failed")
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
        pmem memory delete-all --user-id user123 --confirm
        pmem memory delete-all --run-id session1 --confirm
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
            logger.exception("CLI command failed")
        sys.exit(1)


@click.command(name="export")
@click.option(
    "--format", "fmt",
    type=click.Choice(["json", "csv"]),
    default="json",
    help="Export format (default: json)",
)
@click.option("--user-id", "-u", help="Filter by user ID")
@click.option("--agent-id", "-a", help="Filter by agent ID")
@click.option("--run-id", "-r", help="Filter by run/session ID")
@click.option("--limit", "-l", default=1000, type=int, help="Maximum memories to export (default: 1000)")
@click.option(
    "--output", "-o", "output_file",
    type=click.Path(),
    help="Output file path (default: stdout)",
)
@pass_context
def export_cmd(ctx: CLIContext, fmt, user_id, agent_id, run_id, limit, output_file):
    """
    Export memories to JSON or CSV.

    \b
    Examples:
        pmem memory export --user-id user123 --output memories.json
        pmem memory export --format csv --output memories.csv
        pmem memory export --format json   # prints to stdout
    """
    try:
        content = ctx.memory.export_memories(
            format=fmt,
            user_id=user_id,
            agent_id=agent_id,
            run_id=run_id,
            limit=limit,
        )

        if output_file:
            out_dir = os.path.dirname(output_file)
            if out_dir:
                os.makedirs(out_dir, exist_ok=True)
            with open(output_file, "w", encoding="utf-8") as f:
                f.write(content)
            print_success(f"Exported to {output_file}")
        else:
            click.echo(content)

    except Exception as e:
        print_error(f"Export failed: {e}")
        if ctx.verbose:
            logger.exception("CLI command failed")
        sys.exit(1)


@click.command(name="import")
@click.argument("input_file", required=True, type=click.Path(exists=True))
@click.option(
    "--format", "fmt",
    type=click.Choice(["json", "csv"]),
    default="json",
    help="Import format (default: json)",
)
@click.option("--user-id", "-u", help="Override user ID for all imported memories")
@click.option("--agent-id", "-a", help="Override agent ID for all imported memories")
@json_option
@pass_context
def import_cmd(ctx: CLIContext, input_file, fmt, user_id, agent_id, json_output):
    """
    Import memories from a JSON or CSV file.

    \b
    Examples:
        pmem memory import memories.json
        pmem memory import data.csv --format csv
        pmem memory import memories.json --user-id new_user
    """
    ctx.json_output = ctx.json_output or json_output
    try:
        with open(input_file, "r", encoding="utf-8") as f:
            source = f.read()

        if not source.strip():
            print_error("Import file is empty")
            sys.exit(1)

        result = ctx.memory.import_memories(
            source=source,
            format=fmt,
            user_id=user_id,
            agent_id=agent_id,
        )

        success = result.get("success", 0)
        failed = result.get("failed", 0)
        all_rows_failed = success == 0 and failed > 0

        if ctx.json_output:
            click.echo(format_output(result, "generic", json_output=True))
        elif all_rows_failed:
            print_error(f"Import failed: {success} succeeded, {failed} failed")
        else:
            print_success(
                f"Import complete: {success} succeeded, {failed} failed"
            )

        if all_rows_failed:
            sys.exit(1)

    except Exception as e:
        print_error(f"Import failed: {e}")
        if ctx.verbose:
            logger.exception("CLI command failed")
        sys.exit(1)


@click.command(name="quality")
@click.option("--user-id", "-u", help="Filter by user ID")
@click.option("--agent-id", "-a", help="Filter by agent ID")
@json_option
@pass_context
def quality_cmd(ctx: CLIContext, user_id, agent_id, json_output):
    """
    Analyze memory quality and show metrics.

    \b
    Examples:
        pmem memory quality
        pmem memory quality --user-id user123 --json
    """
    ctx.json_output = ctx.json_output or json_output
    try:
        all_result = ctx.memory.get_all(user_id=user_id, agent_id=agent_id, limit=1000)
        memories = all_result.get("results", [])

        total = len(memories)
        empty_count = sum(1 for m in memories if not (m.get("memory") or m.get("content", "")).strip())
        short_count = sum(1 for m in memories if len((m.get("memory") or m.get("content", "")).strip()) < 10)
        no_metadata = sum(1 for m in memories if not m.get("metadata"))

        quality_data = {
            "total_memories": total,
            "empty_content": empty_count,
            "short_content_lt10": short_count,
            "no_metadata": no_metadata,
            "quality_score": round(1.0 - (empty_count + short_count) / max(total, 1), 4),
        }

        if ctx.json_output:
            click.echo(format_output(quality_data, "generic", json_output=True))
        else:
            click.echo("=" * 50)
            click.echo("Memory Quality Metrics")
            click.echo("=" * 50)
            click.echo(f"{'Total Memories:':<25} {total}")
            click.echo(f"{'Empty Content:':<25} {empty_count}")
            click.echo(f"{'Short Content (<10):':<25} {short_count}")
            click.echo(f"{'No Metadata:':<25} {no_metadata}")
            click.echo(f"{'Quality Score:':<25} {quality_data['quality_score']}")
            click.echo("=" * 50)

    except Exception as e:
        print_error(f"Quality analysis failed: {e}")
        if ctx.verbose:
            logger.exception("CLI command failed")
        sys.exit(1)


@click.command(name="optimize")
@click.option(
    "--strategy",
    type=click.Choice(["deduplicate", "compress"]),
    default="deduplicate",
    help="Optimization strategy (default: deduplicate)",
)
@click.option("--user-id", "-u", help="Filter by user ID")
@click.option("--threshold", "-t", default=0.95, type=float, help="Similarity threshold (default: 0.95)")
@json_option
@pass_context
def optimize_cmd(ctx: CLIContext, strategy, user_id, threshold, json_output):
    """
    Optimize memory storage (deduplicate or compress).

    \b
    Examples:
        pmem memory optimize
        pmem memory optimize --strategy compress --threshold 0.85
        pmem memory optimize --user-id user123 --json
    """
    ctx.json_output = ctx.json_output or json_output
    try:
        result = ctx.memory.optimize(
            strategy=strategy,
            user_id=user_id,
            threshold=threshold,
        )

        if ctx.json_output:
            click.echo(format_output(result, "generic", json_output=True))
        else:
            print_success(f"Optimization complete ({strategy})")
            if isinstance(result, dict):
                for k, v in result.items():
                    click.echo(f"  {k}: {v}")

    except Exception as e:
        print_error(f"Optimization failed: {e}")
        if ctx.verbose:
            logger.exception("CLI command failed")
        sys.exit(1)


# Add commands to group
memory_group.add_command(add_cmd)
memory_group.add_command(search_cmd)
memory_group.add_command(get_cmd)
memory_group.add_command(update_cmd)
memory_group.add_command(delete_cmd)
memory_group.add_command(list_cmd)
memory_group.add_command(delete_all_cmd)
memory_group.add_command(export_cmd)
memory_group.add_command(import_cmd)
memory_group.add_command(quality_cmd)
memory_group.add_command(optimize_cmd)
