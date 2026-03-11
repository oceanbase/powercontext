"""
PowerMem CLI Management Commands

This module provides CLI commands for system management:
- backup: Export memories to a JSON file
- restore: Import memories from a JSON file
- cleanup: Clean up forgotten/stale memories
- migrate: Migrate data between stores
"""

import click
import json
import sys
import os
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any

from ..main import pass_context, CLIContext, json_option
from ..utils.output import (
    format_output,
    print_success,
    print_error,
    print_warning,
    print_info,
)


@click.group(name="manage")
def manage_group():
    """System management commands (backup, restore, cleanup, migrate)."""
    pass


@click.command(name="backup")
@click.option(
    "--output", "-o",
    type=click.Path(),
    default=None,
    help="Output file path (default: powermem_backup_<timestamp>.json)"
)
@click.option("--user-id", "-u", help="Filter by user ID")
@click.option("--agent-id", "-a", help="Filter by agent ID")
@click.option("--run-id", "-r", help="Filter by run/session ID")
@click.option("--limit", "-l", type=int, default=10000, help="Maximum memories to export (default: 10000)")
@click.option("--include-metadata", is_flag=True, default=True, help="Include metadata in backup")
@json_option
@pass_context
def backup_cmd(ctx: CLIContext, output, user_id, agent_id, run_id, limit, include_metadata, json_output):
    """
    Export memories to a JSON file.
    
    \b
    Examples:
        pmem manage backup --output backup.json
        pmem manage backup --user-id user123 --output user_backup.json
        pmem manage backup --limit 1000
    """
    ctx.json_output = ctx.json_output or json_output
    try:
        print_info("Starting backup...")
        
        # Generate default filename if not provided
        if not output:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output = f"powermem_backup_{timestamp}.json"
        
        # Get all memories
        result = ctx.memory.get_all(
            user_id=user_id,
            agent_id=agent_id,
            run_id=run_id,
            limit=limit,
        )
        
        memories = result.get("results", [])
        
        if not memories:
            print_warning("No memories found to backup")
            return
        
        # Prepare backup data
        backup_data = {
            "version": "1.0",
            "created_at": datetime.now().isoformat(),
            "source": "powermem-cli",
            "filters": {
                "user_id": user_id,
                "agent_id": agent_id,
                "run_id": run_id,
            },
            "count": len(memories),
            "memories": memories,
        }
        
        # Create parent directory if needed
        out_path = Path(output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        # Write to file
        with open(output, "w", encoding="utf-8") as f:
            json.dump(backup_data, f, indent=2, default=str, ensure_ascii=False)
        
        file_size = os.path.getsize(output)
        print_success(f"Backup completed: {len(memories)} memories exported to {output}")
        print_info(f"File size: {file_size / 1024:.2f} KB")
        
        if ctx.json_output:
            click.echo(format_output({
                "status": "success",
                "file": output,
                "count": len(memories),
                "size_bytes": file_size,
            }, "generic", json_output=True))
            
    except Exception as e:
        print_error(f"Backup failed: {e}")
        if ctx.verbose:
            import traceback
            traceback.print_exc()
        sys.exit(1)


@click.command(name="restore")
@click.option(
    "--input", "-i", "input_file",
    type=click.Path(exists=True),
    required=True,
    help="Input backup file path"
)
@click.option("--user-id", "-u", help="Override user ID for all restored memories")
@click.option("--agent-id", "-a", help="Override agent ID for all restored memories")
@click.option("--dry-run", is_flag=True, help="Preview restore without making changes")
@click.option("--skip-duplicates", is_flag=True, default=True, help="Skip memories that already exist")
@json_option
@pass_context
def restore_cmd(ctx: CLIContext, input_file, user_id, agent_id, dry_run, skip_duplicates, json_output):
    """
    Import memories from a JSON backup file.
    
    \b
    Examples:
        pmem manage restore --input backup.json
        pmem manage restore --input backup.json --dry-run
        pmem manage restore --input backup.json --user-id new_user
    """
    ctx.json_output = ctx.json_output or json_output
    try:
        print_info(f"Reading backup file: {input_file}")
        
        # Read backup file
        with open(input_file, "r", encoding="utf-8") as f:
            backup_data = json.load(f)
        
        # Validate backup format
        if "memories" not in backup_data:
            print_error("Invalid backup file format: 'memories' field not found")
            sys.exit(1)
        
        memories = backup_data["memories"]
        print_info(f"Found {len(memories)} memories in backup")
        
        if backup_data.get("created_at"):
            print_info(f"Backup created at: {backup_data['created_at']}")
        
        if dry_run:
            print_warning("DRY RUN MODE - No changes will be made")
            click.echo(f"\nWould restore {len(memories)} memories")
            if skip_duplicates:
                click.echo("(With --skip-duplicates, memories that already exist will be skipped.)")
            
            # Show sample
            if memories:
                click.echo("\nSample memories:")
                for i, mem in enumerate(memories[:3]):
                    content = mem.get("memory") or mem.get("content", "N/A")
                    content = content[:50] + "..." if len(content) > 50 else content
                    click.echo(f"  {i+1}. {content}")
            
            if ctx.json_output:
                click.echo(format_output({
                    "dry_run": True,
                    "would_restore": len(memories),
                }, "generic", json_output=True))
            return
        
        # Confirm restore
        if not click.confirm(f"Restore {len(memories)} memories?"):
            print_info("Cancelled")
            return
        
        # Restore memories
        success_count = 0
        skip_count = 0
        error_count = 0
        
        for i, mem in enumerate(memories):
            try:
                # Extract content
                content = mem.get("memory") or mem.get("content")
                if not content:
                    skip_count += 1
                    continue
                
                # Use override IDs if provided
                mem_user_id = user_id or mem.get("user_id")
                mem_agent_id = agent_id or mem.get("agent_id")
                mem_run_id = mem.get("run_id")
                mem_metadata = mem.get("metadata")
                
                # When skip_duplicates, check if this memory already exists (same content + user_id + agent_id)
                is_duplicate = False
                if skip_duplicates:
                    try:
                        existing = ctx.memory.search(
                            query=content,
                            user_id=mem_user_id,
                            agent_id=mem_agent_id,
                            limit=10,
                            threshold=0.99,
                        )
                        for r in existing.get("results") or []:
                            if (r.get("memory") or "").strip() == content.strip():
                                is_duplicate = True
                                break
                    except Exception as e:
                        if ctx.verbose:
                            print_warning(f"Duplicate check failed for memory {i}, will attempt add: {e}")
                        # On error, proceed to add
                
                if is_duplicate:
                    skip_count += 1
                    continue
                
                # Add memory
                result = ctx.memory.add(
                    messages=content,
                    user_id=mem_user_id,
                    agent_id=mem_agent_id,
                    run_id=mem_run_id,
                    metadata=mem_metadata,
                    infer=False,  # Don't run inference on restored data
                )
                
                results = result.get("results", [])
                if results:
                    success_count += 1
                else:
                    skip_count += 1
                
                # Progress indicator
                if (i + 1) % 100 == 0:
                    print_info(f"Progress: {i + 1}/{len(memories)}")
                    
            except Exception as e:
                error_count += 1
                if ctx.verbose:
                    print_warning(f"Failed to restore memory {i}: {e}")
        
        # Report results
        print_success(f"Restore completed:")
        click.echo(f"  - Successfully restored: {success_count}")
        click.echo(f"  - Skipped (duplicates/empty): {skip_count}")
        click.echo(f"  - Errors: {error_count}")
        
        if ctx.json_output:
            click.echo(format_output({
                "status": "success",
                "restored": success_count,
                "skipped": skip_count,
                "errors": error_count,
            }, "generic", json_output=True))
            
    except json.JSONDecodeError as e:
        print_error(f"Invalid JSON in backup file: {e}")
        sys.exit(1)
    except Exception as e:
        print_error(f"Restore failed: {e}")
        if ctx.verbose:
            import traceback
            traceback.print_exc()
        sys.exit(1)


@click.command(name="cleanup")
@click.option("--threshold", "-t", type=float, default=0.1,
              help="Retention score threshold for deletion, in (0, 1] (default: 0.1)")
@click.option("--archive-threshold", type=float, default=0.3,
              help="Retention score threshold for archiving, in (0, 1]; must be > threshold (default: 0.3)")
@click.option("--user-id", "-u", help="Filter by user ID")
@click.option("--agent-id", "-a", help="Filter by agent ID")
@click.option("--dry-run", is_flag=True, help="Preview cleanup without making changes")
@click.option("--force", "-f", is_flag=True, help="Skip confirmation")
@json_option
@pass_context
def cleanup_cmd(ctx: CLIContext, threshold, archive_threshold, user_id, agent_id, dry_run, force, json_output):
    """
    Clean up forgotten or stale memories.
    
    Removes memories with low retention scores (based on Ebbinghaus algorithm).
    
    \b
    Examples:
        pmem manage cleanup --dry-run
        pmem manage cleanup --threshold 0.2
        pmem manage cleanup --user-id user123 --force
    """
    ctx.json_output = ctx.json_output or json_output
    try:
        # Validate threshold: retention score is in [0, 1], threshold must be in (0, 1]
        if threshold <= 0:
            print_error(
                f"Invalid --threshold {threshold}: must be greater than 0. "
                "Retention score is in [0, 1]; threshold 0 or negative would be unreasonable."
            )
            sys.exit(1)
        if threshold > 1:
            print_error(
                f"Invalid --threshold {threshold}: retention score is in [0, 1], "
                "so threshold must not exceed 1. Use a value between 0 and 1 (e.g. 0.1, 0.3)."
            )
            sys.exit(1)
        if archive_threshold <= 0 or archive_threshold > 1:
            print_error(
                f"Invalid --archive-threshold {archive_threshold}: must be in (0, 1]."
            )
            sys.exit(1)
        if threshold >= archive_threshold:
            print_error(
                f"Invalid: --threshold ({threshold}) must be less than --archive-threshold ({archive_threshold}). "
                "Memories with score < threshold are deleted; those between threshold and archive-threshold are archived."
            )
            sys.exit(1)

        print_info("Analyzing memories for cleanup...")
        
        # Get all memories to analyze
        result = ctx.memory.get_all(
            user_id=user_id,
            agent_id=agent_id,
            limit=10000,
        )
        
        memories = result.get("results", [])
        
        if not memories:
            print_info("No memories found")
            return
        
        # Analyze memories based on metadata
        to_delete = []
        to_archive = []
        
        for mem in memories:
            metadata = mem.get("metadata", {})
            retention_score = metadata.get("retention_score")
            
            # If no retention score, check access count and age
            if retention_score is None:
                access_count = metadata.get("access_count", 0)
                # Simple heuristic: memories with 0 access are candidates
                if access_count == 0:
                    created_at = mem.get("created_at")
                    if created_at:
                        # Skip very recent memories
                        try:
                            from datetime import datetime
                            if isinstance(created_at, str):
                                created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                            else:
                                created = created_at
                            age_days = (datetime.now(created.tzinfo) - created).days if created.tzinfo else 0
                            if age_days > 30:  # Older than 30 days with no access
                                to_archive.append(mem)
                        except Exception:
                            pass
                continue
            
            memory_id = mem.get("id") or mem.get("memory_id")
            if retention_score < threshold:
                to_delete.append(mem)
            elif retention_score < archive_threshold:
                to_archive.append(mem)
        
        # Report findings
        click.echo(f"\nAnalysis Results:")
        click.echo(f"  Total memories scanned: {len(memories)}")
        click.echo(f"  Memories to delete (score < {threshold}): {len(to_delete)}")
        click.echo(f"  Memories to archive (score < {archive_threshold}): {len(to_archive)}")
        
        if dry_run:
            print_warning("DRY RUN MODE - No changes will be made")
            
            if to_delete:
                click.echo("\nMemories that would be deleted:")
                for mem in to_delete[:5]:
                    content = mem.get("memory") or mem.get("content", "N/A")
                    content = content[:40] + "..." if len(content) > 40 else content
                    score = mem.get("metadata", {}).get("retention_score", "N/A")
                    click.echo(f"  - {content} (score: {score})")
                if len(to_delete) > 5:
                    click.echo(f"  ... and {len(to_delete) - 5} more")
            
            if ctx.json_output:
                click.echo(format_output({
                    "dry_run": True,
                    "would_delete": len(to_delete),
                    "would_archive": len(to_archive),
                    "total_scanned": len(memories),
                }, "generic", json_output=True))
            return
        
        if not to_delete and not to_archive:
            print_success("No memories need cleanup")
            return
        
        # Confirm cleanup
        if not force:
            if not click.confirm(f"Delete {len(to_delete)} memories?"):
                print_info("Cancelled")
                return
        
        # Perform cleanup
        deleted_count = 0
        error_count = 0
        
        for mem in to_delete:
            try:
                memory_id = mem.get("id") or mem.get("memory_id")
                if memory_id:
                    ctx.memory.delete(
                        memory_id=memory_id,
                        user_id=user_id,
                        agent_id=agent_id,
                    )
                    deleted_count += 1
            except Exception as e:
                error_count += 1
                if ctx.verbose:
                    print_warning(f"Failed to delete memory: {e}")
        
        print_success(f"Cleanup completed:")
        click.echo(f"  - Deleted: {deleted_count}")
        click.echo(f"  - Errors: {error_count}")
        
        if ctx.json_output:
            click.echo(format_output({
                "status": "success",
                "deleted": deleted_count,
                "errors": error_count,
            }, "generic", json_output=True))
            
    except Exception as e:
        print_error(f"Cleanup failed: {e}")
        if ctx.verbose:
            import traceback
            traceback.print_exc()
        sys.exit(1)


@click.command(name="migrate")
@click.option("--target-store", "-t", type=int, required=True,
              help="Target sub-store index")
@click.option("--source-store", "-s", type=int, default=None,
              help="Source sub-store index (default: main store)")
@click.option("--delete-source", is_flag=True, default=False,
              help="Delete from source after migration")
@click.option("--dry-run", is_flag=True, help="Preview migration without making changes")
@json_option
@pass_context
def migrate_cmd(ctx: CLIContext, target_store, source_store, delete_source, dry_run, json_output):
    """
    Migrate data between stores.
    
    Used to move memories between main store and sub-stores. Sub-stores must be
    defined in your config under 'sub_stores' before running migrate .
    There is no CLI command to create sub-stores; 
    they are configured in config only.
    
    \b
    Examples:
        pmem manage migrate --target-store 0 --dry-run
        pmem manage migrate --target-store 1 --delete-source
    """
    ctx.json_output = ctx.json_output or json_output
    try:
        # Migrate (sub-store) is only supported with OceanBase; intercept for SQLite etc.
        storage_type = (getattr(ctx.memory, "storage_type", None) or "").lower()
        if storage_type != "oceanbase":
            print_error(
                f"Migration is only supported with OceanBase storage. "
                f"Current storage: {storage_type or 'unknown'}. "
                "Use OceanBase as DATABASE_PROVIDER to use sub-stores and migrate."
            )
            sys.exit(1)

        print_info(f"Preparing migration to sub-store {target_store}...")
        
        if dry_run:
            print_warning("DRY RUN MODE - No changes will be made")
            
            # Get count from source
            stats = ctx.memory.get_statistics()
            total = stats.get("total_memories", 0)
            
            source_display = "Main store" if source_store is None else f"Sub-store {source_store}"
            target_display = f"Sub-store {target_store}"
            
            click.echo(f"\nMigration Preview:")
            click.echo(f"  Source: {source_display}")
            click.echo(f"  Target: {target_display}")
            click.echo(f"  Memories to migrate: ~{total}")
            click.echo(f"  Delete source: {delete_source}")
            
            if ctx.json_output:
                click.echo(format_output({
                    "dry_run": True,
                    "source": source_display,
                    "target": target_display,
                    "source_store_index": source_store,
                    "target_store_index": target_store,
                    "estimated_count": total,
                    "delete_source": delete_source,
                }, "generic", json_output=True))
            return
        
        # Confirm migration
        if not click.confirm(f"Migrate data to sub-store {target_store}?"):
            print_info("Cancelled")
            return
        
        # Check if migrate method exists
        if not hasattr(ctx.memory, 'migrate_to_sub_store'):
            print_error("Migration not supported by current storage backend")
            sys.exit(1)
        
        # Perform migration
        print_info("Starting migration...")
        try:
            result = ctx.memory.migrate_to_sub_store(
                sub_store_index=target_store,
                delete_source=delete_source,
            )
        except ValueError as e:
            if "No sub stores configured" in str(e):
                print_error(
                    "No sub stores configured. Sub-stores must be defined in your config "
                    "(e.g. config file or environment) under 'sub_stores' before running migrate. "
                )
                print_info(
                    "Tip: There is no CLI command to create sub-stores; add them in config and restart."
                )
                sys.exit(1)
            raise
        
        print_success("Migration completed")
        
        if ctx.json_output:
            target_display = f"Sub-store {target_store}"
            click.echo(format_output({
                "status": "success",
                "target": target_display,
                "target_store_index": target_store,
                "delete_source": delete_source,
                "result": result,
            }, "generic", json_output=True))
            
    except AttributeError:
        print_error("Migration not supported by current storage backend")
        sys.exit(1)
    except Exception as e:
        print_error(f"Migration failed: {e}")
        if ctx.verbose:
            import traceback
            traceback.print_exc()
        sys.exit(1)


# Add commands to group
manage_group.add_command(backup_cmd)
manage_group.add_command(restore_cmd)
manage_group.add_command(cleanup_cmd)
manage_group.add_command(migrate_cmd)
