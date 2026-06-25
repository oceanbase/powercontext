"""
PowerMem CLI Profile Commands

This module provides CLI commands for user profile operations:
- get: Get a user profile by user ID
- list: List user profiles with optional filtering
- delete: Delete a user profile
"""

import logging
import click
import sys

from ..main import pass_context, CLIContext, json_option
from ..utils.output import (
    format_output,
    print_success,
    print_error,
    print_warning,
    print_info,
)

logger = logging.getLogger(__name__)


@click.group(name="profile")
def profile_group():
    """User profile operations (get, list, delete)."""
    pass


@click.command(name="get")
@click.argument("user_id", required=True)
@json_option
@pass_context
def profile_get_cmd(ctx: CLIContext, user_id, json_output):
    """
    Get a user profile by user ID.

    \b
    Examples:
        pmem profile get user123
        pmem profile get user123 --json
    """
    ctx.json_output = ctx.json_output or json_output
    try:
        result = ctx.user_memory.profile(user_id=user_id)

        if not result:
            print_error(f"Profile not found for user: {user_id}")
            sys.exit(1)

        output = format_output(
            result,
            "profile",
            json_output=ctx.json_output,
        )
        click.echo(output)

    except Exception as e:
        print_error(f"Failed to get profile: {e}")
        if ctx.verbose:
            logger.exception("CLI command failed")
        sys.exit(1)


@click.command(name="list")
@click.option("--user-id", "-u", help="Filter by user ID")
@click.option(
    "--main-topic", "-m",
    multiple=True,
    help="Filter by main topic name (can be repeated)",
)
@click.option(
    "--sub-topic", "-s",
    multiple=True,
    help='Filter by sub topic path "main.sub" (can be repeated)',
)
@click.option(
    "--topic-value",
    multiple=True,
    help="Filter by exact topic value (can be repeated)",
)
@click.option("--limit", "-l", default=100, type=int, help="Maximum results (default: 100)")
@click.option("--offset", "-o", default=0, type=int, help="Offset for pagination (default: 0)")
@json_option
@pass_context
def profile_list_cmd(ctx: CLIContext, user_id, main_topic, sub_topic, topic_value,
                     limit, offset, json_output):
    """
    List user profiles with optional filtering.

    \b
    Examples:
        pmem profile list
        pmem profile list --user-id user123
        pmem profile list --main-topic interests --sub-topic interests.sport
        pmem profile list --json
    """
    ctx.json_output = ctx.json_output or json_output
    try:
        result = ctx.user_memory.profile_list(
            user_id=user_id,
            main_topic=list(main_topic) if main_topic else None,
            sub_topic=list(sub_topic) if sub_topic else None,
            topic_value=list(topic_value) if topic_value else None,
            limit=limit,
            offset=offset,
        )

        if ctx.json_output:
            click.echo(format_output(
                {"profiles": result, "count": len(result)},
                "generic",
                json_output=True,
            ))
        else:
            output = format_output(result, "profiles")
            click.echo(output)
            if result:
                click.echo(f"\nShowing {len(result)} profiles (offset: {offset})")

    except Exception as e:
        print_error(f"Failed to list profiles: {e}")
        if ctx.verbose:
            logger.exception("CLI command failed")
        sys.exit(1)


@click.command(name="delete")
@click.argument("user_id", required=True)
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation")
@pass_context
def profile_delete_cmd(ctx: CLIContext, user_id, yes):
    """
    Delete a user profile (does not delete their memories).

    \b
    Examples:
        pmem profile delete user123
        pmem profile delete user123 --yes
    """
    try:
        if not yes:
            if not click.confirm(f"Delete profile for user {user_id}?"):
                print_info("Cancelled")
                return

        result = ctx.user_memory.delete_profile(user_id=user_id)

        if result:
            print_success(f"Profile deleted for user: {user_id}")
        else:
            print_error(f"Profile not found for user: {user_id}")
            sys.exit(1)

    except Exception as e:
        print_error(f"Failed to delete profile: {e}")
        if ctx.verbose:
            logger.exception("CLI command failed")
        sys.exit(1)


# Add commands to group
profile_group.add_command(profile_get_cmd)
profile_group.add_command(profile_list_cmd)
profile_group.add_command(profile_delete_cmd)
