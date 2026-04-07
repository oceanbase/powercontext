"""
PowerMem CLI Interactive Mode

This module provides an interactive REPL (Read-Eval-Print Loop) for PowerMem.
Tab completion and Up/Down history are enabled when the readline module is
available (typical on Unix; on Windows install pyreadline for similar behavior).
"""

import atexit
import click
import os
import shlex
import sys
from typing import Optional, List

try:
    import readline
except ImportError:
    readline = None

from ..main import pass_context, CLIContext
from ..utils.output import (
    format_output,
    print_success,
    print_error,
    print_warning,
    print_info,
)


class InteractiveSession:
    """Interactive session manager."""
    
    PROMPT = "powermem> "
    
    # Commands for Tab completion (first word only)
    COMMANDS = [
        "add", "search", "get", "update", "delete", "list", "stats",
        "set", "show", "clear", "help", "exit", "quit", "q", "memory",
    ]
    
    HELP_TEXT = """
PowerMem Interactive Mode
=========================

Available commands (you can also use "memory <cmd> ..." e.g. memory add "..."):
  add <content> [--user-id <id>] [--agent-id <id>]
      Add a new memory (same as CLI: pmem memory add ...)
      
  search <query> [--user-id <id>] [--limit <n>] [--threshold <t>]
      Search for memories (--threshold: min similarity, e.g. 0.3)
      
  get <memory_id> [--user-id <id>]
      Get a specific memory
      
  update <memory_id> <content> [--user-id <id>]
      Update a memory
      
  delete <memory_id> [--user-id <id>]
      Delete a memory
      
  list [--user-id <id>] [--limit <n>] [-j|--json]
      List memories (-j/--json for JSON output)
      
  stats [--user-id <id>]
      Show statistics
      
  set user <user_id>
      Set default user ID for this session
      
  set agent <agent_id>
      Set default agent ID for this session
      
  set json on|off
      Enable/disable JSON output
      
  show settings
      Show current session settings
      
  clear
      Clear the screen
      
  help
      Show this help message
      
  exit, quit, q
      Exit interactive mode

Examples:
  powermem> add "User prefers dark mode"
  powermem> search "preferences"
  powermem> set user user123
  powermem> list --limit 10
"""
    
    def __init__(self, ctx: CLIContext):
        self.ctx = ctx
        self.default_user_id: Optional[str] = None
        self.default_agent_id: Optional[str] = None
        self.json_output: bool = ctx.json_output
        self.running: bool = True
        self._completion_matches: List[str] = []
    
    def _completer(self, text: str, state: int):
        """Readline completer: Tab completion for command names."""
        if state == 0:
            t = text.lower()
            self._completion_matches = [c for c in self.COMMANDS if c.startswith(t)]
        try:
            return self._completion_matches[state] + " "
        except IndexError:
            return None
    
    def _setup_readline(self):
        """Enable Tab completion and Up/Down history when readline is available."""
        if readline is None:
            return
        readline.set_completer(self._completer)
        readline.parse_and_bind("tab: complete")
        histfile = os.path.join(os.path.expanduser("~"), ".powermem_history")
        try:
            readline.read_history_file(histfile)
        except OSError:
            pass
        def _save_history(path=histfile):
            try:
                readline.write_history_file(path)
            except OSError:
                pass
        atexit.register(_save_history)
    
    def run(self):
        """Run the interactive session."""
        self._setup_readline()
        self._print_welcome()
        
        while self.running:
            try:
                # Read input
                line = input(self.PROMPT).strip()
                
                if not line:
                    continue
                
                # Process command
                self._process_command(line)
                
            except KeyboardInterrupt:
                click.echo()  # New line after ^C
                print_info("Use 'exit' or 'quit' to exit")
            except EOFError:
                click.echo()
                self.running = False
    
    def _print_welcome(self):
        """Print welcome message."""
        click.echo()
        click.echo("=" * 50)
        click.echo("  PowerMem Interactive Mode")
        click.echo("=" * 50)
        click.echo("Type 'help' for available commands, 'exit' to quit")
        if readline:
            click.echo("Tab: complete commands; Up/Down: command history")
        click.echo()
    
    def _process_command(self, line: str):
        """Process a command line."""
        try:
            # Parse the command line
            parts = shlex.split(line)
        except ValueError as e:
            print_error(f"Invalid command: {e}")
            return
        
        if not parts:
            return
        
        cmd = parts[0].lower()
        args = parts[1:]
        
        # Support "memory add ..." / "memory search ..." etc. so interactive mode
        # is consistent with CLI command-line usage (pmem memory add ...).
        if cmd == "memory" and args:
            cmd = args[0].lower()
            args = args[1:]
        
        # Route to command handler
        handlers = {
            "add": self._cmd_add,
            "search": self._cmd_search,
            "get": self._cmd_get,
            "update": self._cmd_update,
            "delete": self._cmd_delete,
            "list": self._cmd_list,
            "stats": self._cmd_stats,
            "set": self._cmd_set,
            "show": self._cmd_show,
            "clear": self._cmd_clear,
            "help": self._cmd_help,
            "exit": self._cmd_exit,
            "quit": self._cmd_exit,
            "q": self._cmd_exit,
        }
        
        handler = handlers.get(cmd)
        if handler:
            handler(args)
        else:
            print_error(f"Unknown command: {cmd}")
            print_info("Type 'help' for available commands")
    
    def _parse_options(self, args: List[str]) -> tuple:
        """Parse command arguments and options."""
        positional = []
        options = {}
        
        i = 0
        while i < len(args):
            arg = args[i]
            if arg.startswith("--"):
                key = arg[2:].replace("-", "_")
                if i + 1 < len(args) and not args[i + 1].startswith("--"):
                    options[key] = args[i + 1]
                    i += 2
                else:
                    options[key] = True
                    i += 1
            elif arg.startswith("-"):
                # Short options
                key = arg[1:]
                short_map = {"u": "user_id", "a": "agent_id", "l": "limit", "r": "run_id", "j": "json"}
                key = short_map.get(key, key)
                if i + 1 < len(args) and not args[i + 1].startswith("-"):
                    options[key] = args[i + 1]
                    i += 2
                else:
                    options[key] = True
                    i += 1
            else:
                positional.append(arg)
                i += 1
        
        return positional, options
    
    def _get_user_id(self, options: dict) -> Optional[str]:
        """Get user ID from options or defaults."""
        return options.get("user_id") or self.default_user_id
    
    def _get_agent_id(self, options: dict) -> Optional[str]:
        """Get agent ID from options or defaults."""
        return options.get("agent_id") or self.default_agent_id
    
    def _cmd_add(self, args: List[str]):
        """Handle add command."""
        positional, options = self._parse_options(args)
        
        if not positional:
            print_error("Usage: add <content> [--user-id <id>] [--agent-id <id>]")
            return
        
        content = " ".join(positional)
        
        try:
            result = self.ctx.memory.add(
                messages=content,
                user_id=self._get_user_id(options),
                agent_id=self._get_agent_id(options),
                infer=not options.get("no_infer", False),
            )
            
            results = result.get("results", [])
            if results:
                for r in results:
                    event = r.get("event", "ADD")
                    memory_id = r.get("id", "N/A")
                    print_success(f"Memory {event}: ID={memory_id}")
            else:
                print_warning("No memory was added")
                
        except Exception as e:
            print_error(f"Failed: {e}")
    
    def _cmd_search(self, args: List[str]):
        """Handle search command."""
        positional, options = self._parse_options(args)
        
        if not positional:
            print_error("Usage: search <query> [--user-id <id>] [--limit <n>] [--threshold <t>]")
            return
        
        query = " ".join(positional)
        limit = int(options.get("limit", 10))
        threshold = options.get("threshold")
        if threshold is not None:
            try:
                threshold = float(threshold)
            except (TypeError, ValueError):
                threshold = None
        
        try:
            result = self.ctx.memory.search(
                query=query,
                user_id=self._get_user_id(options),
                agent_id=self._get_agent_id(options),
                limit=limit,
                threshold=threshold,
            )
            
            memories = result.get("results", [])
            if not memories:
                print_info("No results found")
                return
            
            click.echo(f"\nFound {len(memories)} results:")
            click.echo("-" * 60)
            
            for i, mem in enumerate(memories, 1):
                memory_id = mem.get("id") or mem.get("memory_id", "N/A")
                content = mem.get("memory") or mem.get("content", "N/A")
                score = mem.get("score", 0)
                
                # Truncate content
                if len(content) > 60:
                    content = content[:57] + "..."
                
                click.echo(f"{i}. [{memory_id}] (score: {score:.4f})")
                click.echo(f"   {content}")
                
        except Exception as e:
            print_error(f"Search failed: {e}")
    
    def _cmd_get(self, args: List[str]):
        """Handle get command."""
        positional, options = self._parse_options(args)
        
        if not positional:
            print_error("Usage: get <memory_id> [--user-id <id>]")
            return
        
        try:
            memory_id = int(positional[0])
        except ValueError:
            print_error("Invalid memory ID (must be a number)")
            return
        
        try:
            result = self.ctx.memory.get(
                memory_id=memory_id,
                user_id=self._get_user_id(options),
                agent_id=self._get_agent_id(options),
            )
            
            if result is None:
                print_error(f"Memory not found: {memory_id}")
                return
            
            click.echo()
            click.echo(f"ID: {result.get('id') or result.get('memory_id')}")
            click.echo(f"Content: {result.get('memory') or result.get('content')}")
            click.echo(f"User ID: {result.get('user_id', 'N/A')}")
            click.echo(f"Agent ID: {result.get('agent_id', 'N/A')}")
            click.echo(f"Created: {result.get('created_at', 'N/A')}")
            
        except Exception as e:
            print_error(f"Failed: {e}")
    
    def _cmd_update(self, args: List[str]):
        """Handle update command."""
        positional, options = self._parse_options(args)
        
        if len(positional) < 2:
            print_error("Usage: update <memory_id> <content> [--user-id <id>]")
            return
        
        try:
            memory_id = int(positional[0])
        except ValueError:
            print_error("Invalid memory ID (must be a number)")
            return
        
        content = " ".join(positional[1:])
        
        try:
            result = self.ctx.memory.update(
                memory_id=memory_id,
                content=content,
                user_id=self._get_user_id(options),
                agent_id=self._get_agent_id(options),
            )
            if result is None or not isinstance(result, dict) or not result:
                print_error(f"Memory not found or access denied: {memory_id}")
            else:
                print_success(f"Memory updated: ID={memory_id}")
            
        except Exception as e:
            print_error(f"Failed: {e}")
    
    def _cmd_delete(self, args: List[str]):
        """Handle delete command."""
        positional, options = self._parse_options(args)
        
        if not positional:
            print_error("Usage: delete <memory_id> [--user-id <id>]")
            return
        
        try:
            memory_id = int(positional[0])
        except ValueError:
            print_error("Invalid memory ID (must be a number)")
            return
        
        # Confirm
        if not click.confirm(f"Delete memory {memory_id}?"):
            print_info("Cancelled")
            return
        
        try:
            result = self.ctx.memory.delete(
                memory_id=memory_id,
                user_id=self._get_user_id(options),
                agent_id=self._get_agent_id(options),
            )
            
            if result:
                print_success(f"Memory deleted: ID={memory_id}")
            else:
                # Consistent with update: same prompt for not found or access denied (issue #299)
                print_error(f"Memory not found or access denied: {memory_id}")
                
        except Exception as e:
            print_error(f"Failed: {e}")
    
    def _cmd_list(self, args: List[str]):
        """Handle list command."""
        _, options = self._parse_options(args)
        
        limit = int(options.get("limit", 20))
        offset = int(options.get("offset", 0))
        use_json = options.get("json") or self.json_output
        
        try:
            result = self.ctx.memory.get_all(
                user_id=self._get_user_id(options),
                agent_id=self._get_agent_id(options),
                limit=limit,
                offset=offset,
            )
            
            memories = result.get("results", [])
            if not memories:
                if use_json:
                    click.echo(format_output([], "memories", json_output=True))
                else:
                    print_info("No memories found")
                return
            
            if use_json:
                click.echo(format_output(memories, "memories", json_output=True))
                return
            
            click.echo(f"\nFound {len(memories)} memories:")
            click.echo("-" * 70)
            click.echo(f"{'ID':<20} {'User ID':<22} {'Content':<35}")
            click.echo("-" * 70)

            def _truncate_with_ellipsis(value: str, max_len: int) -> str:
                """Truncate long display values with visible ellipsis."""
                if len(value) <= max_len:
                    return value
                if max_len <= 3:
                    return value[:max_len]
                return value[: max_len - 3] + "..."
            
            for mem in memories:
                memory_id = _truncate_with_ellipsis(
                    str(mem.get("id") or mem.get("memory_id", "N/A")),
                    18,
                )
                user_id = _truncate_with_ellipsis(str(mem.get("user_id", "N/A")), 20)
                content = _truncate_with_ellipsis(
                    str(mem.get("memory") or mem.get("content", "N/A")),
                    33,
                )
                
                click.echo(f"{memory_id:<20} {user_id:<22} {content:<35}")
            
            click.echo("-" * 70)
            
        except Exception as e:
            print_error(f"Failed: {e}")
    
    def _cmd_stats(self, args: List[str]):
        """Handle stats command."""
        _, options = self._parse_options(args)
        
        try:
            stats = self.ctx.memory.get_statistics(
                user_id=self._get_user_id(options),
                agent_id=self._get_agent_id(options),
                time_range="all",
            )
            
            click.echo()
            click.echo("=" * 40)
            click.echo("Statistics")
            click.echo("=" * 40)
            
            total = stats.get("total_memories", stats.get("total", 0))
            click.echo(f"Total memories: {total}")
            
            by_type = stats.get("by_type", {})
            if by_type:
                click.echo("\nBy type:")
                for t, count in by_type.items():
                    click.echo(f"  {t}: {count}")
            
            age_dist = stats.get("age_distribution", {})
            if age_dist:
                click.echo("\nAge distribution:")
                for age, count in age_dist.items():
                    click.echo(f"  {age}: {count}")
            
            click.echo("=" * 40)
            
        except Exception as e:
            print_error(f"Failed: {e}")
    
    def _cmd_set(self, args: List[str]):
        """Handle set command."""
        if len(args) < 2:
            print_error("Usage: set <setting> <value>")
            print_info("Settings: user, agent, json")
            return
        
        setting = args[0].lower()
        value = args[1]
        
        if setting == "user":
            self.default_user_id = value if value.lower() != "none" else None
            print_success(f"Default user ID set to: {self.default_user_id or '(none)'}")
        elif setting == "agent":
            self.default_agent_id = value if value.lower() != "none" else None
            print_success(f"Default agent ID set to: {self.default_agent_id or '(none)'}")
        elif setting == "json":
            self.json_output = value.lower() in ["on", "true", "yes", "1"]
            self.ctx.json_output = self.json_output
            print_success(f"JSON output: {'enabled' if self.json_output else 'disabled'}")
        else:
            print_error(f"Unknown setting: {setting}")
    
    def _cmd_show(self, args: List[str]):
        """Handle show command."""
        if not args or args[0].lower() == "settings":
            click.echo()
            click.echo("Current Settings:")
            click.echo(f"  Default user ID: {self.default_user_id or '(none)'}")
            click.echo(f"  Default agent ID: {self.default_agent_id or '(none)'}")
            click.echo(f"  JSON output: {'enabled' if self.json_output else 'disabled'}")
        else:
            print_error(f"Unknown: {args[0]}")
    
    def _cmd_clear(self, args: List[str]):
        """Handle clear command."""
        click.clear()
    
    def _cmd_help(self, args: List[str]):
        """Handle help command."""
        click.echo(self.HELP_TEXT)
    
    def _cmd_exit(self, args: List[str]):
        """Handle exit command."""
        print_info("Goodbye!")
        self.running = False


@click.command(name="shell")
@pass_context
def shell_cmd(ctx: CLIContext):
    """
    Start interactive mode (REPL).
    
    Provides a shell-like interface for PowerMem operations.
    
    \b
    Examples:
        pmem shell
    """
    try:
        session = InteractiveSession(ctx)
        session.run()
    except Exception as e:
        print_error(f"Interactive mode error: {e}")
        sys.exit(1)
