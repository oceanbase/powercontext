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


@click.group()
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
    # Handle shell completion installation
    if install_completion:
        _install_shell_completion(install_completion)
        return
    
    ctx.ensure_object(CLIContext)
    ctx.obj.env_file = env_file
    ctx.obj.json_output = json_output
    ctx.obj.verbose = verbose


def _install_shell_completion(shell: str) -> None:
    """Install shell completion script for the specified shell."""
    import subprocess
    
    completion_scripts = {
        "bash": '''
# PowerMem CLI bash completion
# Add this to ~/.bashrc or ~/.bash_profile

_pmem_completion() {
    local IFS=$'\\n'
    local response

    response=$(env COMP_WORDS="${COMP_WORDS[*]}" COMP_CWORD=$COMP_CWORD _PMEM_COMPLETE=bash_complete $1)

    for completion in $response; do
        IFS=',' read type value <<< "$completion"
        COMPREPLY+=("$value")
    done
}

complete -o default -F _pmem_completion pmem
complete -o default -F _pmem_completion powermem-cli
''',
        "zsh": '''
# PowerMem CLI zsh completion
# Add this to ~/.zshrc

_pmem_completion() {
    local -a completions
    local -a completions_with_descriptions
    local -a response
    (( ! $+commands[pmem] )) && return 1

    response=("${(@f)$(env COMP_WORDS="${words[*]}" COMP_CWORD=$((CURRENT-1)) _PMEM_COMPLETE=zsh_complete pmem)}")

    for key descr in ${(kv)response}; do
        if [[ "$descr" == "_" ]]; then
            completions+=("$key")
        else
            completions_with_descriptions+=("$key":"$descr")
        fi
    done

    if [ -n "$completions_with_descriptions" ]; then
        _describe -V unsorted completions_with_descriptions -U
    fi

    if [ -n "$completions" ]; then
        compadd -U -V unsorted -a completions
    fi
}

compdef _pmem_completion pmem
compdef _pmem_completion powermem-cli
''',
        "fish": '''
# PowerMem CLI fish completion
# Save this to ~/.config/fish/completions/pmem.fish

function _pmem_completion
    set -l response (env _PMEM_COMPLETE=fish_complete COMP_WORDS=(commandline -cp) COMP_CWORD=(commandline -t) pmem)
    for completion in $response
        echo $completion
    end
end

complete -c pmem -f -a "(_pmem_completion)"
complete -c powermem-cli -f -a "(_pmem_completion)"
''',
        "powershell": '''
# PowerMem CLI PowerShell completion
# Add this to your PowerShell profile

Register-ArgumentCompleter -Native -CommandName pmem -ScriptBlock {
    param($wordToComplete, $commandAst, $cursorPosition)
    $env:COMP_WORDS = $commandAst.ToString()
    $env:COMP_CWORD = $cursorPosition
    $env:_PMEM_COMPLETE = "powershell_complete"
    pmem | ForEach-Object {
        [System.Management.Automation.CompletionResult]::new($_, $_, 'ParameterValue', $_)
    }
    Remove-Item Env:COMP_WORDS
    Remove-Item Env:COMP_CWORD
    Remove-Item Env:_PMEM_COMPLETE
}

Register-ArgumentCompleter -Native -CommandName powermem-cli -ScriptBlock {
    param($wordToComplete, $commandAst, $cursorPosition)
    $env:COMP_WORDS = $commandAst.ToString()
    $env:COMP_CWORD = $cursorPosition
    $env:_PMEM_COMPLETE = "powershell_complete"
    powermem-cli | ForEach-Object {
        [System.Management.Automation.CompletionResult]::new($_, $_, 'ParameterValue', $_)
    }
    Remove-Item Env:COMP_WORDS
    Remove-Item Env:COMP_CWORD  
    Remove-Item Env:_PMEM_COMPLETE
}
'''
    }
    
    script = completion_scripts.get(shell)
    if not script:
        click.echo(f"Unsupported shell: {shell}", err=True)
        sys.exit(1)
    
    # Determine installation path
    home = os.path.expanduser("~")
    install_paths = {
        "bash": os.path.join(home, ".bashrc"),
        "zsh": os.path.join(home, ".zshrc"),
        "fish": os.path.join(home, ".config", "fish", "completions", "pmem.fish"),
        "powershell": None,  # PowerShell profile path varies
    }
    
    click.echo(f"Shell completion script for {shell}:")
    click.echo("-" * 50)
    click.echo(script.strip())
    click.echo("-" * 50)
    
    if shell == "fish":
        # For fish, create the completions file directly
        fish_dir = os.path.join(home, ".config", "fish", "completions")
        fish_file = os.path.join(fish_dir, "pmem.fish")
        
        if click.confirm(f"Install to {fish_file}?"):
            os.makedirs(fish_dir, exist_ok=True)
            with open(fish_file, "w") as f:
                f.write(script.strip())
            click.echo(click.style(f"[SUCCESS] Installed to {fish_file}", fg="green"))
    elif shell == "powershell":
        click.echo("\nTo install, add the script above to your PowerShell profile.")
        click.echo("You can find your profile path by running: $PROFILE")
    else:
        # For bash/zsh, append to rc file
        rc_file = install_paths[shell]
        if rc_file and os.path.exists(os.path.dirname(rc_file) or "."):
            if click.confirm(f"Append to {rc_file}?"):
                with open(rc_file, "a") as f:
                    f.write("\n" + script.strip() + "\n")
                click.echo(click.style(f"[SUCCESS] Added to {rc_file}", fg="green"))
                click.echo(f"Run 'source {rc_file}' to activate completion")


# Import and register command groups
from .commands.memory import memory_group
from .commands.config import config_group
from .commands.stats import stats_cmd
from .commands.manage import manage_group
from .commands.interactive import interactive_cmd, shell_cmd

# Register memory commands at root level for convenience
cli.add_command(memory_group)

# Also add individual memory commands to root for direct access
from .commands.memory import add_cmd, search_cmd, get_cmd, update_cmd, delete_cmd, list_cmd, delete_all_cmd

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
