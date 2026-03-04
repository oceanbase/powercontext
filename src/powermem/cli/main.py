"""
PowerMem CLI - Main command group

This module defines the main CLI entry point and global options.
"""

import click
import sys
import os
from typing import Optional

# Version from package
try:
    from powermem import __version__
except ImportError:
    __version__ = "unknown"


class CLIContext:
    """Context object passed to all commands."""
    
    def __init__(self):
        self.env_file: Optional[str] = None
        self.json_output: bool = False
        self.verbose: bool = False
        self._memory = None
        self._config = None
    
    @property
    def memory(self):
        """Lazy-load Memory instance."""
        if self._memory is None:
            from powermem import create_memory
            
            # Load config with custom env file if specified
            if self.env_file:
                os.environ["POWERMEM_ENV_FILE"] = self.env_file
            
            try:
                self._memory = create_memory()
            except Exception as e:
                click.echo(f"Error: Failed to initialize PowerMem: {e}", err=True)
                sys.exit(1)
        return self._memory
    
    @property
    def config(self):
        """Lazy-load configuration."""
        if self._config is None:
            from powermem import auto_config
            
            if self.env_file:
                os.environ["POWERMEM_ENV_FILE"] = self.env_file
            
            try:
                self._config = auto_config()
            except Exception as e:
                click.echo(f"Error: Failed to load configuration: {e}", err=True)
                sys.exit(1)
        return self._config


pass_context = click.make_pass_decorator(CLIContext, ensure=True)

# Static command tree for fast shell completion (no Python process on TAB).
# Keep in sync with cli.add_command / group.add_command below.
_COMPLETION_COMMANDS = [
    "add", "config", "delete", "delete-all", "get", "interactive", "list",
    "manage", "memory", "search", "shell", "stats", "update",
]
_COMPLETION_SUBCOMMANDS = {
    "config": ["init", "show", "test", "validate"],
    "manage": ["backup", "cleanup", "migrate", "restore"],
    "memory": ["add", "delete", "delete-all", "get", "list", "search", "update"],
}


@click.group(invoke_without_command=True)
@click.option(
    "--env-file", "-e",
    type=click.Path(exists=True),
    help="Path to .env configuration file"
)
@click.option(
    "--json", "-j", "json_output",
    is_flag=True,
    help="Output results in JSON format"
)
@click.option(
    "--verbose", "-v",
    is_flag=True,
    help="Enable verbose output"
)
@click.option(
    "--install-completion",
    type=click.Choice(["bash", "zsh", "fish", "powershell"]),
    help="Install shell completion script"
)
@click.version_option(version=__version__, prog_name="pmem")
@click.pass_context
def cli(ctx, env_file, json_output, verbose, install_completion):
    """
    PowerMem CLI - Command Line Interface for PowerMem
    
    A powerful tool for managing AI memory operations from the command line.
    
    \b
    Examples:
        pmem add "User prefers dark mode" --user-id user123
        pmem search "preferences" --user-id user123
        pmem stats --json
        pmem config show
    
    \b
    Shell Completion:
        pmem --install-completion bash   # Install bash completion
        pmem --install-completion zsh    # Install zsh completion
        pmem --install-completion fish   # Install fish completion
    """
    # Handle shell completion installation (no subcommand required)
    if install_completion:
        _install_shell_completion(install_completion)
        return

    if ctx.invoked_subcommand is None:
        click.echo("Missing command.", err=True)
        click.echo(ctx.get_help(), err=True)
        ctx.exit(1)
        return

    ctx.ensure_object(CLIContext)
    ctx.obj.env_file = env_file
    ctx.obj.json_output = json_output
    ctx.obj.verbose = verbose


def _static_bash_completion_script() -> str:
    """Generate bash completion script (pure shell, no Python on TAB)."""
    top = " ".join(_COMPLETION_COMMANDS)
    lines = [
        "_pmem_completion() {",
        "  local cur=${COMP_WORDS[COMP_CWORD]}",
        "  local prev=${COMP_WORDS[COMP_CWORD-1]}",
        "  if [ $COMP_CWORD -eq 1 ]; then",
        f"    COMPREPLY=($(compgen -W \"{top}\" -- \"$cur\"))",
        "  elif [ $COMP_CWORD -eq 2 ]; then",
        "    case \"$prev\" in",
    ]
    for group, subcmds in _COMPLETION_SUBCOMMANDS.items():
        sub = " ".join(subcmds)
        lines.append(f"      {group}) COMPREPLY=($(compgen -W \"{sub}\" -- \"$cur\")) ;;")
    lines.extend([
        "      *) COMPREPLY=() ;;",
        "    esac",
        "  else",
        "    COMPREPLY=()",
        "  fi",
        "}",
        "complete -o default -F _pmem_completion pmem",
        "complete -o default -F _pmem_completion powermem-cli",
    ])
    return "\n".join(lines)


def _static_zsh_completion_script() -> str:
    """Generate zsh completion script (pure shell, no Python on TAB)."""
    cmds = " ".join(_COMPLETION_COMMANDS)
    return f"""#compdef pmem powermem-cli
_pmem_completion() {{
  if [ $CURRENT -eq 2 ]; then
    compadd ${{(s: :)'{cmds}'}}
  elif [ $CURRENT -eq 3 ]; then
    case ${{words[2]}} in
      config) compadd init show test validate ;;
      manage) compadd backup cleanup migrate restore ;;
      memory) compadd add delete delete-all get list search update ;;
      *) ;;
    esac
  fi
}}
_pmem_completion"""


def _static_fish_completion_script() -> str:
    """Generate fish completion script (pure shell, no Python on TAB)."""
    top = " ".join(_COMPLETION_COMMANDS)
    lines = [
        "function _pmem_completion",
        "  set -l cmd (commandline -op)",
        "  set -l cur (commandline -t)",
        "  if [ (count $cmd) -eq 1 ]; then",
        f"    echo {top} | tr ' ' '\\n' | grep -E \"^$cur\"",
        "  else if [ (count $cmd) -eq 2 ]; then",
        "    switch $cmd[2]",
    ]
    for group, subcmds in _COMPLETION_SUBCOMMANDS.items():
        sub = " ".join(subcmds)
        lines.append(f"      {group})")
        lines.append(f"        echo {sub} | tr ' ' '\\n' | grep -E \"^$cur\"")
        lines.append("        ;;")
    lines.extend([
        "      *) ;;",
        "    end",
        "  end",
        "  end",
        "end",
        "complete -c pmem -f -a '(_pmem_completion)'",
        "complete -c powermem-cli -f -a '(_pmem_completion)'",
    ])
    return "\n".join(lines)


def _install_shell_completion(shell: str) -> None:
    """Install static shell completion script (instant TAB, no Python process)."""
    if shell == "bash":
        script = _static_bash_completion_script()
    elif shell == "zsh":
        script = _static_zsh_completion_script()
    elif shell == "fish":
        script = _static_fish_completion_script()
    elif shell == "powershell":
        # Click does not ship PowerShell completion; show manual instruction
        script = """
# PowerShell: register completion by running once (add to $PROFILE to persist)
# Completion will suggest: pmem, config, get, add, list, delete, memory, manage, etc.
$scriptBlock = {
    param($wordToComplete, $commandAst, $cursorPosition)
    $line = $commandAst.ToString()
    $env:COMP_WORDS = $line
    $env:COMP_CWORD = [math]::Max(0, ($line -split '\\s+').Count - 1)
    $env:_PMEM_COMPLETE = "powershell_complete"
    & pmem 2>$null
}
Register-ArgumentCompleter -Native -CommandName pmem,powermem-cli -ScriptBlock $scriptBlock
""".strip()
    else:
        click.echo(f"Unsupported shell: {shell}", err=True)
        sys.exit(1)

    home = os.path.expanduser("~")

    if shell == "fish":
        fish_dir = os.path.join(home, ".config", "fish", "completions")
        fish_file = os.path.join(fish_dir, "pmem.fish")
        click.echo(f"Shell completion script for {shell}:")
        click.echo("-" * 50)
        click.echo(script.strip())
        click.echo("-" * 50)
        if click.confirm(f"Install to {fish_file}?"):
            os.makedirs(fish_dir, exist_ok=True)
            with open(fish_file, "w") as f:
                f.write(script.strip())
            click.echo(click.style(f"[SUCCESS] Installed to {fish_file}", fg="green"))
    elif shell == "powershell":
        click.echo("Shell completion script for powershell:")
        click.echo("-" * 50)
        click.echo(script.strip())
        click.echo("-" * 50)
        click.echo("\nTo install, add the script above to your PowerShell profile: $PROFILE")
    else:
        # bash / zsh: write script to a dedicated file, add one source line to rc (idempotent)
        config_dir = os.path.join(home, ".config", "powermem")
        script_file = os.path.join(config_dir, f"{shell}_completion")
        rc_file = os.path.join(home, ".bashrc" if shell == "bash" else ".zshrc")
        source_line = f'\n# PowerMem CLI completion\n[ -f "{script_file}" ] && . "{script_file}"\n'

        os.makedirs(config_dir, exist_ok=True)
        with open(script_file, "w") as f:
            f.write(script.strip())
        click.echo(click.style(f"[SUCCESS] Wrote completion script to {script_file}", fg="green"))

        if os.path.exists(rc_file):
            with open(rc_file) as f:
                rc_content = f.read()
            if script_file in rc_content:
                click.echo(f"Already sourced in {rc_file}; no change.")
            else:
                if click.confirm(f"Add source line to {rc_file}?"):
                    with open(rc_file, "a") as f:
                        f.write(source_line)
                    click.echo(click.style(f"[SUCCESS] Added source to {rc_file}", fg="green"))
                else:
                    click.echo(f"To enable, add to {rc_file}:")
                    click.echo(f'  [ -f "{script_file}" ] && . "{script_file}"')
        else:
            click.echo(f"To enable, create {rc_file} and add:")
            click.echo(f'  [ -f "{script_file}" ] && . "{script_file}"')
        click.echo("Run 'source " + rc_file + "' or open a new terminal to activate.")


# Import and register command groups
from .commands.memory import add_cmd, search_cmd, get_cmd, update_cmd, delete_cmd, list_cmd, delete_all_cmd
from .commands.config import config_group
from .commands.stats import stats_cmd
from .commands.manage import manage_group
from .commands.interactive import interactive_cmd, shell_cmd

# Memory commands at root: add, search, get, update, delete, list, delete-all

cli.add_command(add_cmd)
cli.add_command(search_cmd)
cli.add_command(get_cmd)
cli.add_command(update_cmd)
cli.add_command(delete_cmd)
cli.add_command(list_cmd)
cli.add_command(delete_all_cmd)

# Register other command groups
cli.add_command(config_group)
cli.add_command(stats_cmd)
cli.add_command(manage_group)
cli.add_command(interactive_cmd)
cli.add_command(shell_cmd)


if __name__ == "__main__":
    cli()
