"""
PowerMem CLI Configuration Commands

This module provides CLI commands for configuration management:
- show: Display current configuration
- validate: Validate configuration file
- test: Test configuration connectivity
"""

import click
import sys
import os
from typing import Optional

from ..main import pass_context, CLIContext
from ..utils.output import (
    format_output,
    print_success,
    print_error,
    print_warning,
    print_info,
)


@click.group(name="config")
def config_group():
    """Configuration management commands."""
    pass


@click.command(name="show")
@click.option(
    "--section", "-s",
    type=click.Choice(["llm", "embedder", "vector_store", "graph_store",
                       "intelligent_memory", "agent_memory", "reranker", "all"]),
    default="all",
    help="Configuration section to show (default: all)"
)
@click.option("--show-secrets", is_flag=True, help="Show API keys and passwords (USE WITH CAUTION)")
@pass_context
def show_cmd(ctx: CLIContext, section, show_secrets):
    """
    Display current configuration.
    
    \b
    Examples:
        pmem config show
        pmem config show --section llm
        pmem config show --json
    """
    try:
        config = ctx.config
        
        # Filter by section if specified
        if section != "all":
            config = {section: config.get(section, {})}
        
        # Mask secrets unless explicitly requested
        if not show_secrets:
            config = _mask_secrets(config)
        
        # Format output
        output = format_output(
            config,
            "config",
            json_output=ctx.json_output
        )
        click.echo(output)
        
    except Exception as e:
        print_error(f"Failed to show configuration: {e}")
        if ctx.verbose:
            import traceback
            traceback.print_exc()
        sys.exit(1)


@click.command(name="validate")
@click.option(
    "--env-file", "-f",
    type=click.Path(exists=True),
    help="Path to .env file to validate"
)
@click.option("--strict", is_flag=True, help="Enable strict validation mode")
@pass_context
def validate_cmd(ctx: CLIContext, env_file, strict):
    """
    Validate configuration file.
    
    \b
    Examples:
        pmem config validate
        pmem config validate --env-file .env.production
        pmem config validate --strict
    """
    try:
        from powermem import validate_config, auto_config
        
        # Set env file if specified
        if env_file:
            os.environ["POWERMEM_ENV_FILE"] = env_file
        
        print_info("Validating configuration...")
        
        # Load and validate config
        config = auto_config()
        
        errors = []
        warnings = []
        
        # Check required sections
        required_sections = ["llm", "embedder", "vector_store"]
        for section in required_sections:
            section_config = config.get(section, {})
            if not section_config:
                errors.append(f"Missing required section: {section}")
            elif not section_config.get("provider"):
                errors.append(f"Missing provider in section: {section}")
        
        # Check LLM configuration
        llm_config = config.get("llm", {})
        if llm_config:
            provider = llm_config.get("provider", "")
            inner_config = llm_config.get("config", {})
            
            # Check API key for non-mock providers
            if provider not in ["mock", "ollama"]:
                api_key = inner_config.get("api_key")
                if not api_key:
                    if strict:
                        errors.append(f"LLM API key not configured for provider: {provider}")
                    else:
                        warnings.append(f"LLM API key not configured for provider: {provider}")
        
        # Check embedder configuration
        embedder_config = config.get("embedder", {})
        if embedder_config:
            provider = embedder_config.get("provider", "")
            inner_config = embedder_config.get("config", {})
            
            # Check embedding dimensions
            dims = inner_config.get("embedding_dims")
            if not dims and strict:
                warnings.append("Embedding dimensions not explicitly configured")
            
            # Check API key for non-mock providers
            if provider not in ["mock", "ollama", "huggingface"]:
                api_key = inner_config.get("api_key")
                if not api_key:
                    if strict:
                        errors.append(f"Embedder API key not configured for provider: {provider}")
                    else:
                        warnings.append(f"Embedder API key not configured for provider: {provider}")
        
        # Check vector store configuration
        vector_store_config = config.get("vector_store", {})
        if vector_store_config:
            provider = vector_store_config.get("provider", "")
            inner_config = vector_store_config.get("config", {})
            
            if provider == "oceanbase":
                required_fields = ["host", "port", "user", "db_name"]
                conn_args = inner_config.get("connection_args", {})
                for field in required_fields:
                    if not conn_args.get(field):
                        errors.append(f"OceanBase connection missing: {field}")
            
            elif provider == "postgres" or provider == "pgvector":
                required_fields = ["host", "port", "user", "dbname"]
                for field in required_fields:
                    if not inner_config.get(field):
                        errors.append(f"PostgreSQL connection missing: {field}")
        
        # Output results
        if ctx.json_output:
            result = {
                "valid": len(errors) == 0,
                "errors": errors,
                "warnings": warnings,
            }
            click.echo(format_output(result, "generic", json_output=True))
        else:
            if errors:
                print_error("Configuration validation FAILED")
                for error in errors:
                    click.echo(f"  - {error}")
            
            if warnings:
                print_warning("Warnings:")
                for warning in warnings:
                    click.echo(f"  - {warning}")
            
            if not errors and not warnings:
                print_success("Configuration is valid!")
            elif not errors:
                print_success("Configuration is valid (with warnings)")
        
        if errors:
            sys.exit(1)
            
    except Exception as e:
        print_error(f"Validation failed: {e}")
        if ctx.verbose:
            import traceback
            traceback.print_exc()
        sys.exit(1)


@click.command(name="test")
@click.option("--component", "-c",
              type=click.Choice(["database", "llm", "embedder", "all"]),
              default="all",
              help="Component to test (default: all)")
@pass_context
def test_cmd(ctx: CLIContext, component):
    """
    Test configuration connectivity.
    
    \b
    Examples:
        pmem config test
        pmem config test --component database
        pmem config test --component llm
    """
    results = {
        "database": None,
        "llm": None,
        "embedder": None,
    }
    
    print_info("Testing configuration connectivity...")
    
    # Test database connection
    if component in ["database", "all"]:
        try:
            print_info("Testing database connection...")
            # Access memory to trigger database initialization
            memory = ctx.memory
            # Try to get statistics as a simple connectivity test
            stats = memory.get_statistics()
            results["database"] = {
                "status": "success",
                "message": "Database connection successful",
                "details": {"total_memories": stats.get("total_memories", 0)}
            }
            print_success("Database: Connected")
            if ctx.verbose:
                click.echo(f"  Total memories: {stats.get('total_memories', 0)}")
        except Exception as e:
            results["database"] = {
                "status": "failed",
                "message": str(e)
            }
            print_error(f"Database: Failed - {e}")
    
    # Test LLM connection
    if component in ["llm", "all"]:
        try:
            print_info("Testing LLM connection...")
            # Access the LLM through memory
            memory = ctx.memory
            if hasattr(memory, 'llm') and memory.llm:
                # Try a simple generation
                response = memory.llm.generate(
                    messages=[{"role": "user", "content": "Say 'test' and nothing else."}]
                )
                results["llm"] = {
                    "status": "success",
                    "message": "LLM connection successful",
                    "details": {"response_length": len(str(response)) if response else 0}
                }
                print_success("LLM: Connected")
            else:
                results["llm"] = {
                    "status": "skipped",
                    "message": "LLM not configured or using mock provider"
                }
                print_warning("LLM: Skipped (not configured)")
        except Exception as e:
            results["llm"] = {
                "status": "failed",
                "message": str(e)
            }
            print_error(f"LLM: Failed - {e}")
    
    # Test embedder connection
    if component in ["embedder", "all"]:
        try:
            print_info("Testing embedder connection...")
            memory = ctx.memory
            if hasattr(memory, 'embedder') and memory.embedder:
                # Try to generate an embedding
                embedding = memory.embedder.embed("test")
                if embedding:
                    dims = len(embedding) if isinstance(embedding, list) else "N/A"
                    results["embedder"] = {
                        "status": "success",
                        "message": "Embedder connection successful",
                        "details": {"dimensions": dims}
                    }
                    print_success(f"Embedder: Connected (dims={dims})")
                else:
                    results["embedder"] = {
                        "status": "warning",
                        "message": "Embedder returned empty result"
                    }
                    print_warning("Embedder: Connected but returned empty result")
            else:
                results["embedder"] = {
                    "status": "skipped",
                    "message": "Embedder not configured or using mock provider"
                }
                print_warning("Embedder: Skipped (not configured)")
        except Exception as e:
            results["embedder"] = {
                "status": "failed",
                "message": str(e)
            }
            print_error(f"Embedder: Failed - {e}")
    
    # Output final results
    if ctx.json_output:
        click.echo(format_output(results, "generic", json_output=True))
    else:
        click.echo()
        # Summary
        success_count = sum(1 for r in results.values() if r and r.get("status") == "success")
        failed_count = sum(1 for r in results.values() if r and r.get("status") == "failed")
        skipped_count = sum(1 for r in results.values() if r and r.get("status") == "skipped")
        
        click.echo(f"Results: {success_count} passed, {failed_count} failed, {skipped_count} skipped")
        
        if failed_count > 0:
            sys.exit(1)


def _mask_secrets(config: dict, parent_key: str = "") -> dict:
    """Mask sensitive values in configuration."""
    if not isinstance(config, dict):
        return config
    
    masked = {}
    sensitive_keys = ["key", "password", "secret", "token", "credential"]
    
    for key, value in config.items():
        full_key = f"{parent_key}.{key}" if parent_key else key
        
        if isinstance(value, dict):
            masked[key] = _mask_secrets(value, full_key)
        elif any(s in key.lower() for s in sensitive_keys):
            # Mask sensitive values
            if value:
                masked[key] = "***" + str(value)[-4:] if len(str(value)) > 4 else "***"
            else:
                masked[key] = "(not set)"
        else:
            masked[key] = value
    
    return masked


# Add commands to group
config_group.add_command(show_cmd)
config_group.add_command(validate_cmd)
config_group.add_command(test_cmd)
