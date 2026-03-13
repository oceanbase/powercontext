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
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from ..main import pass_context, CLIContext, json_option
from ..utils.output import (
    format_output,
    print_success,
    print_error,
    print_warning,
    print_info,
)
from ..utils.envfile import read_env_file, update_env_file


@click.group(name="config")
def config_group():
    """Configuration management commands."""
    pass


# Keys to mask in config show (explicit list only; add new entries here when needed)
# Use the key names as they appear in the nested config (e.g. api_key, password under each section),
# not env-style names (e.g. LLM_API_KEY only matches when config is flattened).
_SENSITIVE_KEYS = frozenset({
    "api_key",
    "password",
    "oceanbase_password",
    "postgres_password",
    "llm_api_key",
    "embedding_api_key",
    "reranker_api_key",
    "graph_store_password",
    "sparse_embedder_api_key",
    "powermem_server_api_keys",
})


def _is_sensitive_name(name: str) -> bool:
    """True if the key is in the explicit sensitive list (masked in config show)."""
    return name.lower() in _SENSITIVE_KEYS


def _mask_for_display(key: str, value: Any) -> str:
    if value is None:
        return "(not set)"
    if _is_sensitive_name(key):
        return "***" if str(value) else "(not set)"
    return str(value)


def _read_masked_input(prompt: str, mask_char: str = "*") -> str:
    """
    Read secret input from a TTY while echoing `mask_char` per character.
    Falls back to click.prompt(hide_input=True) when stdin is not a TTY.
    """
    # Fallback for non-interactive contexts (pipes, CI, etc.)
    if not sys.stdin.isatty():
        return click.prompt(prompt, default="", show_default=False, hide_input=True)

    import termios
    import tty

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    buf: list[str] = []

    sys.stdout.write(f"{prompt}: ")
    sys.stdout.flush()

    def _erase_one() -> None:
        sys.stdout.write("\b \b")
        sys.stdout.flush()

    try:
        tty.setraw(fd)
        while True:
            ch = sys.stdin.read(1)
            if ch in ("\r", "\n"):
                break
            if ch == "\x03":  # Ctrl+C
                raise KeyboardInterrupt
            if ch == "\x04":  # Ctrl+D
                break
            if ch in ("\x7f", "\b"):  # Backspace
                if buf:
                    buf.pop()
                    _erase_one()
                continue
            if ch == "\x15":  # Ctrl+U (clear line)
                while buf:
                    buf.pop()
                    _erase_one()
                continue
            if ch == "\x1b":
                # Escape sequence (arrows, etc.). Consume a couple chars best-effort.
                sys.stdin.read(1)
                sys.stdin.read(1)
                continue

            buf.append(ch)
            sys.stdout.write(mask_char)
            sys.stdout.flush()
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        sys.stdout.write("\n")
        sys.stdout.flush()

    return "".join(buf)


def _prompt_keep_or_set(
    label: str,
    current: Optional[str],
    hide_input: bool = False,
    confirm_sensitive: bool = True,
) -> Optional[str]:
    """
    Prompt for an optional value.
    - If current exists, user can keep it or set a new value.
    - Returns the chosen value (string), or None if user chose to keep/skip.
    """
    if current:
        if click.confirm(f"{label}: keep existing value?", default=True):
            return None
    # Never display existing secrets as defaults, even when input is hidden.
    if hide_input:
        click.echo(click.style("Note: secret input is masked with '*' and will not be shown.", fg="blue"))
        for attempt in range(3):
            first = _read_masked_input(label)
            if not confirm_sensitive:
                click.echo("Captured value (hidden).")
                return first
            second = _read_masked_input("Please confirm by re-entering the value")
            if first == second:
                click.echo("Captured value (hidden).")
                return first
            print_warning("Values do not match. Please try again.")
        raise click.ClickException("Too many failed confirmations for secret input.")

    default_value = current or ""
    show_default = bool(current)
    return click.prompt(label, default=default_value, show_default=show_default, hide_input=False)


def _prompt_optional_int(label: str, current: Optional[str], default: int) -> Optional[int]:
    if current is not None and current != "":
        if click.confirm(f"{label}: keep existing value ({current})?", default=True):
            return None
        try:
            default = int(current)
        except Exception:
            pass
    return click.prompt(label, type=int, default=default, show_default=True)


def _prompt_optional_float(label: str, current: Optional[str], default: float) -> Optional[float]:
    if current is not None and current != "":
        if click.confirm(f"{label}: keep existing value ({current})?", default=True):
            return None
        try:
            default = float(current)
        except Exception:
            pass
    return click.prompt(label, type=float, default=default, show_default=True)


@click.command(name="show")
@click.option(
    "--section", "-s",
    type=click.Choice(["llm", "embedder", "vector_store", "graph_store",
                       "intelligent_memory", "agent_memory", "reranker", "all"]),
    default="all",
    help="Configuration section to show (default: all)"
)
@click.option("--show-secrets", is_flag=True, help="Show API keys and passwords (USE WITH CAUTION)")
@json_option
@pass_context
def show_cmd(ctx: CLIContext, section, show_secrets, json_output):
    """
    Display current configuration.
    
    \b
    Examples:
        pmem config show
        pmem config show --section llm
        pmem config show --json
    """
    ctx.json_output = ctx.json_output or json_output
    try:
        # Show only what is in .env; only show the selected provider's vars per section
        env_path = ctx.env_file or _resolve_default_env_file()
        config = _config_from_env_file(env_path, section if section != "all" else None, mask_secrets=not show_secrets)
        
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


def _is_valid_url(value: str) -> bool:
    """Return True if value looks like a valid HTTP/HTTPS URL."""
    if not value or not isinstance(value, str):
        return False
    value = value.strip()
    try:
        parsed = urlparse(value)
        return parsed.scheme in ("http", "https") and bool(parsed.netloc)
    except Exception:
        return False


def _validate_base_urls_in_config(
    inner_config: Dict[str, Any], section_label: str, errors: List[str]
) -> None:
    """Append errors for any base_url-like key that has an invalid URL value."""
    for key, value in inner_config.items():
        if "base_url" not in key.lower():
            continue
        if value is None or (isinstance(value, str) and not value.strip()):
            continue
        if not isinstance(value, str):
            errors.append(f"{section_label}: invalid {key} (must be a string)")
            continue
        if not _is_valid_url(value):
            errors.append(f"{section_label}: invalid URL in {key}: {value!r}")


def _validate_loaded_config(config: Dict[str, Any], strict: bool) -> Dict[str, Any]:
    errors = []
    warnings = []

    required_sections = ["llm", "embedder", "vector_store"]
    for section in required_sections:
        section_config = config.get(section, {})
        if not section_config:
            errors.append(f"Missing required section: {section}")
        elif not section_config.get("provider"):
            errors.append(f"Missing provider in section: {section}")

    llm_config = config.get("llm", {})
    if llm_config:
        provider = llm_config.get("provider", "")
        inner_config = llm_config.get("config", {})
        if provider not in ["mock", "ollama"]:
            api_key = inner_config.get("api_key")
            if not api_key:
                (errors if strict else warnings).append(
                    f"LLM API key not configured for provider: {provider}"
                )
        _validate_base_urls_in_config(inner_config, "LLM", errors)

    embedder_config = config.get("embedder", {})
    if embedder_config:
        provider = embedder_config.get("provider", "")
        inner_config = embedder_config.get("config", {})
        dims = inner_config.get("embedding_dims")
        if not dims and strict:
            warnings.append("Embedding dimensions not explicitly configured")

        if provider not in ["mock", "ollama", "huggingface"]:
            api_key = inner_config.get("api_key")
            if not api_key:
                (errors if strict else warnings).append(
                    f"Embedder API key not configured for provider: {provider}"
                )
        _validate_base_urls_in_config(inner_config, "Embedder", errors)

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
        elif provider in ("postgres", "pgvector"):
            required_fields = ["host", "port", "user", "dbname"]
            for field in required_fields:
                if not inner_config.get(field):
                    errors.append(f"PostgreSQL connection missing: {field}")

        # EMBEDDING_DIMS must match OCEANBASE_EMBEDDING_MODEL_DIMS / POSTGRES_EMBEDDING_MODEL_DIMS
        if provider in ("oceanbase", "postgres", "pgvector"):
            embedder_dims_raw = (config.get("embedder") or {}).get("config", {}).get("embedding_dims")
            vs_dims_raw = inner_config.get("embedding_model_dims")
            embedder_dims = _parse_dims(embedder_dims_raw)
            vs_dims = _parse_dims(vs_dims_raw)
            if embedder_dims is not None and vs_dims is not None and embedder_dims != vs_dims:
                dims_var = "OCEANBASE_EMBEDDING_MODEL_DIMS" if provider == "oceanbase" else "POSTGRES_EMBEDDING_MODEL_DIMS"
                errors.append(
                    f"EMBEDDING_DIMS ({embedder_dims}) must match {dims_var} ({vs_dims}) when using {provider}"
                )

    return {"valid": len(errors) == 0, "errors": errors, "warnings": warnings}


def _parse_dims(value: Any) -> Optional[int]:
    """Parse embedding dimension to int; return None if missing or invalid."""
    if value is None:
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _load_config_with_env_file(env_file: Optional[str]) -> Dict[str, Any]:
    from powermem import auto_config

    if env_file:
        os.environ["POWERMEM_ENV_FILE"] = env_file
    return auto_config()


def _run_connectivity_checks(config: Dict[str, Any]) -> List[str]:
    """
    Run LLM and embedder connectivity checks with the given config.
    Returns a list of error messages (empty if all checks pass).
    """
    errors: List[str] = []
    try:
        from powermem import create_memory

        memory = create_memory(config=config)
    except Exception as e:
        errors.append(f"Failed to create Memory with config: {e}")
        return errors

    llm_provider = (config.get("llm") or {}).get("provider", "")
    if llm_provider and llm_provider not in ("mock", "ollama"):
        try:
            if hasattr(memory, "llm") and memory.llm:
                messages = [{"role": "user", "content": "Say 'test' and nothing else."}]
                if hasattr(memory.llm, "generate_response"):
                    memory.llm.generate_response(messages=messages)
                else:
                    memory.llm.generate(messages=messages)
            else:
                errors.append("LLM is not configured or not available for connectivity check.")
        except Exception as e:
            errors.append(f"LLM connectivity failed (check BASE_URL, API_KEY, and MODEL): {e}")

    embedder_provider = (config.get("embedder") or {}).get("provider", "")
    if embedder_provider and embedder_provider not in ("mock", "ollama", "huggingface"):
        try:
            if hasattr(memory, "embedding") and memory.embedding:
                emb = memory.embedding.embed("test")
                if not emb:
                    errors.append("Embedder returned empty result.")
            else:
                errors.append("Embedder is not configured or not available for connectivity check.")
        except Exception as e:
            errors.append(f"Embedder connectivity failed (check BASE_URL and API_KEY): {e}")

    return errors


@click.command(name="validate")
@click.option(
    "--env-file", "-f",
    type=click.Path(exists=True),
    help="Path to .env file to validate"
)
@click.option("--strict", is_flag=True, help="Enable strict validation mode")
@click.option(
    "--check-connectivity",
    is_flag=True,
    help="Also verify LLM and embedder connectivity (API_KEY, BASE_URL, model)"
)
@json_option
@pass_context
def validate_cmd(ctx: CLIContext, env_file, strict, json_output, check_connectivity):
    """
    Validate configuration file.
    
    Use --check-connectivity to verify that LLM and embedder API_KEY, BASE_URL,
    and model settings are valid by making a test request.
    
    \b
    Examples:
        pmem config validate
        pmem config validate --env-file .env.production
        pmem config validate --strict
        pmem config validate --check-connectivity
    """
    ctx.json_output = ctx.json_output or json_output
    try:
        print_info("Validating configuration...")
        config = _load_config_with_env_file(env_file or ctx.env_file)
        result = _validate_loaded_config(config, strict=strict)

        if result["valid"] and check_connectivity:
            print_info("Running connectivity checks (LLM, embedder)...")
            connectivity_errors = _run_connectivity_checks(config)
            result["errors"].extend(connectivity_errors)
            result["valid"] = len(result["errors"]) == 0

        if ctx.json_output:
            click.echo(format_output(result, "generic", json_output=True))
        else:
            if result["errors"]:
                print_error("Configuration validation FAILED")
                for error in result["errors"]:
                    click.echo(f"  - {error}")
            
            if result["warnings"]:
                print_warning("Warnings:")
                for warning in result["warnings"]:
                    click.echo(f"  - {warning}")
            
            if not result["errors"] and not result["warnings"]:
                print_success("Configuration is valid!")
            elif not result["errors"]:
                print_success("Configuration is valid (with warnings)")
        
        if result["errors"]:
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
@json_option
@pass_context
def test_cmd(ctx: CLIContext, component, json_output):
    """
    Test configuration connectivity.
    
    \b
    Examples:
        pmem config test
        pmem config test --component database
        pmem config test --component llm
    """
    ctx.json_output = ctx.json_output or json_output
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
                messages = [{"role": "user", "content": "Say 'test' and nothing else."}]
                llm = memory.llm
                if hasattr(llm, "generate_response"):
                    response = llm.generate_response(messages=messages)
                else:
                    # Backward-compatibility for any legacy LLM wrappers
                    response = llm.generate(messages=messages)
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
            if hasattr(memory, 'embedding') and memory.embedding:
                # Try to generate an embedding
                embedding = memory.embedding.embed("test")
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

    for key, value in config.items():
        if isinstance(value, dict):
            masked[key] = _mask_secrets(value, parent_key=key)
        elif _is_sensitive_name(key):
            # Mask sensitive values
            if value:
                # Fully mask secrets (do not leak suffix/prefix)
                masked[key] = "***"
            else:
                masked[key] = "(not set)"
        else:
            masked[key] = value
    
    return masked


def _strip_vector_store_connection_args(config: dict) -> dict:
    """Remove connection_args from vector_store section so they are not shown in config display."""
    if not isinstance(config, dict):
        return config
    result = dict(config)
    vs = result.get("vector_store")
    if isinstance(vs, dict):
        inner = vs.get("config")
        if isinstance(inner, dict) and "connection_args" in inner:
            vs = dict(vs)
            vs["config"] = {k: v for k, v in inner.items() if k != "connection_args"}
            result["vector_store"] = vs
    return result


# Env var -> section for "config show" (only show what is in .env). Order: first match wins.
_ENV_SECTION_PREFIXES: List[Tuple[str, List[str]]] = [
    ("timezone", ["TIMEZONE"]),
    ("vector_store", ["DATABASE_", "SQLITE_", "OCEANBASE_", "POSTGRES_"]),
    ("llm", ["LLM_", "QWEN_LLM_", "OPENAI_LLM_", "SILICONFLOW_LLM_", "OLLAMA_LLM_", "VLLM_LLM_", "ANTHROPIC_LLM_", "DEEPSEEK_LLM_"]),
    ("embedder", ["EMBEDDING_", "QWEN_EMBEDDING_", "OPENAI_EMBEDDING_", "SILICONFLOW_EMBEDDING_", "HUGGINFACE_EMBEDDING_", "LMSTUDIO_EMBEDDING_", "OLLAMA_EMBEDDING_"]),
    ("reranker", ["RERANKER_"]),
    ("agent_memory", ["AGENT_"]),
    ("intelligent_memory", ["INTELLIGENT_MEMORY_"]),
    ("memory_decay", ["MEMORY_DECAY_"]),
    ("performance", ["MEMORY_BATCH_SIZE", "MEMORY_CACHE_SIZE", "MEMORY_CACHE_TTL", "MEMORY_SEARCH_", "VECTOR_STORE_BATCH_SIZE", "VECTOR_STORE_CACHE_SIZE", "VECTOR_STORE_INDEX_REBUILD"]),
    ("security", ["ENCRYPTION_", "ACCESS_CONTROL_"]),
    ("telemetry", ["TELEMETRY_"]),
    ("audit", ["AUDIT_"]),
    ("logging", ["LOGGING_"]),
    ("graph_store", ["GRAPH_STORE_"]),
    ("sparse_embedder", ["SPARSE_VECTOR_", "SPARSE_EMBEDDER_"]),
    ("query_rewrite", ["QUERY_REWRITE_"]),
    ("server", ["POWERMEM_SERVER_"]),
]

# Per-section: env key that holds provider choice -> allowed prefix(s) for that provider (only show these when selected).
_ENV_PROVIDER_FILTER: Dict[str, Tuple[str, Dict[str, List[str]]]] = {
    "vector_store": ("DATABASE_PROVIDER", {
        "oceanbase": ["DATABASE_PROVIDER", "OCEANBASE_"],
        "sqlite": ["DATABASE_PROVIDER", "SQLITE_"],
        "postgres": ["DATABASE_PROVIDER", "POSTGRES_"],
    }),
    "llm": ("LLM_PROVIDER", {
        "qwen": ["LLM_", "QWEN_LLM_"],
        "openai": ["LLM_", "OPENAI_LLM_"],
        "siliconflow": ["LLM_", "SILICONFLOW_LLM_"],
        "ollama": ["LLM_", "OLLAMA_LLM_"],
        "vllm": ["LLM_", "VLLM_LLM_"],
        "anthropic": ["LLM_", "ANTHROPIC_LLM_"],
        "deepseek": ["LLM_", "DEEPSEEK_LLM_"],
    }),
    "embedder": ("EMBEDDING_PROVIDER", {
        "qwen": ["EMBEDDING_", "QWEN_EMBEDDING_"],
        "openai": ["EMBEDDING_", "OPENAI_EMBEDDING_"],
        "siliconflow": ["EMBEDDING_", "SILICONFLOW_EMBEDDING_"],
        "huggingface": ["EMBEDDING_", "HUGGINFACE_EMBEDDING_"],
        "lmstudio": ["EMBEDDING_", "LMSTUDIO_EMBEDDING_"],
        "ollama": ["EMBEDDING_", "OLLAMA_EMBEDDING_"],
    }),
    "reranker": ("RERANKER_PROVIDER", {
        "qwen": ["RERANKER_"],
        "jina": ["RERANKER_"],
        "zhipu": ["RERANKER_"],
    }),
    "sparse_embedder": ("SPARSE_EMBEDDER_PROVIDER", {
        "qwen": ["SPARSE_EMBEDDER_", "SPARSE_VECTOR_"],
        "openai": ["SPARSE_EMBEDDER_", "SPARSE_VECTOR_"],
    }),
}


def _assign_env_key_to_section(key: str) -> Optional[str]:
    """Return section_key for this env var, or None if not in any known section."""
    for section_key, prefixes in _ENV_SECTION_PREFIXES:
        for p in prefixes:
            if key == p or key.startswith(p):
                return section_key
    return None


def _env_key_matches_provider_filter(key: str, section_key: str, env_dict: Dict[str, str]) -> bool:
    """True if key should be shown for this section given the selected provider (only show selected provider's vars)."""
    spec = _ENV_PROVIDER_FILTER.get(section_key)
    if not spec:
        return True
    provider_key, provider_prefixes = spec
    provider = (env_dict.get(provider_key) or "").strip().lower()
    allowed = provider_prefixes.get(provider)
    if not allowed:
        return True  # unknown provider, show all keys that were assigned to this section
    for prefix in allowed:
        if key == prefix or key.startswith(prefix):
            return True
    return False


def _config_from_env_file(
    env_path: str,
    section_filter: Optional[str],
    mask_secrets: bool = True,
) -> Dict[str, Any]:
    """
    Build config from .env only: show keys present in .env; per section show only the selected provider's vars.
    """
    from ..utils.envfile import read_env_file

    _, env_dict = read_env_file(env_path)
    if not env_dict:
        return {} if not section_filter else {section_filter: {}}

    sections: Dict[str, Dict[str, str]] = {sk: {} for sk, _ in _ENV_SECTION_PREFIXES}
    for env_key, value in env_dict.items():
        section_key = _assign_env_key_to_section(env_key)
        if section_key is None:
            continue
        if not _env_key_matches_provider_filter(env_key, section_key, env_dict):
            continue
        if mask_secrets and _is_sensitive_name(env_key):
            value = "***" if (value or "").strip() else "(not set)"
        sections[section_key][env_key] = value

    result = {}
    for section_key, _ in _ENV_SECTION_PREFIXES:
        if sections[section_key]:
            result[section_key] = sections[section_key]
    if section_filter is not None:
        result = {section_filter: result.get(section_filter, {})}
    return result


def _resolve_default_env_file() -> str:
    try:
        from powermem.settings import _DEFAULT_ENV_FILE  # type: ignore

        if _DEFAULT_ENV_FILE:
            return str(_DEFAULT_ENV_FILE)
    except Exception:
        pass
    return str(Path.cwd() / ".env")


_ENV_PLACEHOLDER_VALUES = {
    # Keep this list aligned with `.env.example`.
    "your_password",
    "your_api_key_here",
}


def _normalize_existing_env_values(existing: Dict[str, str]) -> Dict[str, str]:
    """
    Treat template placeholder values as "not set".

    Rationale: `.env.example` contains placeholder tokens (e.g. `your_password`)
    which should not be treated as real configured values in the wizard.
    """
    normalized: Dict[str, str] = {}
    for k, v in existing.items():
        vv = (v or "").strip()
        if vv.lower() in _ENV_PLACEHOLDER_VALUES:
            normalized[k] = ""
        else:
            normalized[k] = v
    return normalized


def _discover_env_example(start_dir: Path, max_parent_levels: int = 8) -> Optional[Path]:
    """
    Try to find `.env.example` by walking up parent directories.
    This keeps `pmem config init` convenient when launched from a subdirectory.
    """
    start_dir = start_dir.resolve()
    candidates = [start_dir, *list(start_dir.parents)[:max_parent_levels]]
    for d in candidates:
        p = d / ".env.example"
        if p.exists() and p.is_file():
            return p
    return None


def _wizard_database(existing: Dict[str, str]) -> Dict[str, str]:
    updates: Dict[str, str] = {}
    provider_default = existing.get("DATABASE_PROVIDER") or "sqlite"
    provider = click.prompt(
        "Database provider",
        type=click.Choice(["oceanbase", "postgres", "sqlite"], case_sensitive=False),
        default=provider_default,
        show_default=True,
    ).lower()
    updates["DATABASE_PROVIDER"] = provider

    if provider == "sqlite":
        updates["SQLITE_PATH"] = click.prompt(
            "SQLite path",
            default=existing.get("SQLITE_PATH") or "./data/powermem_dev.db",
            show_default=True,
        )
        updates["SQLITE_ENABLE_WAL"] = str(
            click.confirm(
                "Enable WAL (Write-Ahead Logging)",
                default=(existing.get("SQLITE_ENABLE_WAL", "true").lower() != "false"),
            )
        ).lower()
        updates["SQLITE_TIMEOUT"] = str(
            click.prompt(
                "SQLite timeout (seconds)",
                type=int,
                default=int(existing.get("SQLITE_TIMEOUT") or 30),
                show_default=True,
            )
        )
        if click.confirm("Set SQLite collection name?", default=False):
            updates["SQLITE_COLLECTION"] = click.prompt(
                "SQLite collection name",
                default=existing.get("SQLITE_COLLECTION") or "memories",
                show_default=True,
            )
        return updates

    if provider == "oceanbase":
        updates["OCEANBASE_HOST"] = click.prompt(
            "OceanBase host",
            default=existing.get("OCEANBASE_HOST") or "127.0.0.1",
            show_default=True,
        )
        updates["OCEANBASE_PORT"] = click.prompt(
            "OceanBase port",
            default=existing.get("OCEANBASE_PORT") or "2881",
            show_default=True,
        )
        updates["OCEANBASE_USER"] = click.prompt(
            "OceanBase user",
            default=existing.get("OCEANBASE_USER") or "root",
            show_default=True,
        )
        pw = _prompt_keep_or_set("OceanBase password (OCEANBASE_PASSWORD)", existing.get("OCEANBASE_PASSWORD"), hide_input=True)
        if pw is not None and (pw != "" or existing.get("OCEANBASE_PASSWORD")):
            updates["OCEANBASE_PASSWORD"] = pw
        updates["OCEANBASE_DATABASE"] = click.prompt(
            "OceanBase database name",
            default=existing.get("OCEANBASE_DATABASE") or "powermem",
            show_default=True,
        )
        updates["OCEANBASE_COLLECTION"] = click.prompt(
            "OceanBase collection name",
            default=existing.get("OCEANBASE_COLLECTION") or "memories",
            show_default=True,
        )
        return updates

    # postgres (pgvector)
    updates["POSTGRES_HOST"] = click.prompt(
        "PostgreSQL host",
        default=existing.get("POSTGRES_HOST") or "127.0.0.1",
        show_default=True,
    )
    updates["POSTGRES_PORT"] = str(
        click.prompt(
            "PostgreSQL port",
            type=int,
            default=int(existing.get("POSTGRES_PORT") or 5432),
            show_default=True,
        )
    )
    updates["POSTGRES_USER"] = click.prompt(
        "PostgreSQL user",
        default=existing.get("POSTGRES_USER") or "postgres",
        show_default=True,
    )
    pw = _prompt_keep_or_set("PostgreSQL password (POSTGRES_PASSWORD)", existing.get("POSTGRES_PASSWORD"), hide_input=True)
    if pw is not None and (pw != "" or existing.get("POSTGRES_PASSWORD")):
        updates["POSTGRES_PASSWORD"] = pw
    updates["POSTGRES_DATABASE"] = click.prompt(
        "PostgreSQL database name",
        default=existing.get("POSTGRES_DATABASE") or "postgres",
        show_default=True,
    )
    updates["POSTGRES_COLLECTION"] = click.prompt(
        "PostgreSQL collection name",
        default=existing.get("POSTGRES_COLLECTION") or "memories",
        show_default=True,
    )
    return updates


def _wizard_database_quickstart(existing: Dict[str, str]) -> Dict[str, str]:
    """
    Quickstart database wizard: prompt only the minimum required fields.
    This intentionally avoids optional knobs (WAL/timeout/collection/etc.).
    """
    updates: Dict[str, str] = {}
    provider_default = existing.get("DATABASE_PROVIDER") or "sqlite"
    provider = click.prompt(
        "Database provider",
        type=click.Choice(["sqlite", "oceanbase", "postgres"], case_sensitive=False),
        default=provider_default,
        show_default=True,
    ).lower()
    updates["DATABASE_PROVIDER"] = provider

    if provider == "sqlite":
        updates["SQLITE_PATH"] = click.prompt(
            "SQLite path",
            default=existing.get("SQLITE_PATH") or "./data/powermem_dev.db",
            show_default=True,
        )
        return updates

    if provider == "oceanbase":
        updates["OCEANBASE_HOST"] = click.prompt(
            "OceanBase host",
            default=existing.get("OCEANBASE_HOST") or "127.0.0.1",
            show_default=True,
        )
        updates["OCEANBASE_PORT"] = click.prompt(
            "OceanBase port",
            default=existing.get("OCEANBASE_PORT") or "2881",
            show_default=True,
        )
        updates["OCEANBASE_USER"] = click.prompt(
            "OceanBase user",
            default=existing.get("OCEANBASE_USER") or "root",
            show_default=True,
        )
        pw = _prompt_keep_or_set(
            "OceanBase password (OCEANBASE_PASSWORD)",
            existing.get("OCEANBASE_PASSWORD"),
            hide_input=True,
        )
        if pw is not None and (pw != "" or existing.get("OCEANBASE_PASSWORD")):
            updates["OCEANBASE_PASSWORD"] = pw
        updates["OCEANBASE_DATABASE"] = click.prompt(
            "OceanBase database name",
            default=existing.get("OCEANBASE_DATABASE") or "powermem",
            show_default=True,
        )
        return updates

    # postgres (pgvector)
    updates["POSTGRES_HOST"] = click.prompt(
        "PostgreSQL host",
        default=existing.get("POSTGRES_HOST") or "127.0.0.1",
        show_default=True,
    )
    updates["POSTGRES_PORT"] = str(
        click.prompt(
            "PostgreSQL port",
            type=int,
            default=int(existing.get("POSTGRES_PORT") or 5432),
            show_default=True,
        )
    )
    updates["POSTGRES_USER"] = click.prompt(
        "PostgreSQL user",
        default=existing.get("POSTGRES_USER") or "postgres",
        show_default=True,
    )
    pw = _prompt_keep_or_set(
        "PostgreSQL password (POSTGRES_PASSWORD)",
        existing.get("POSTGRES_PASSWORD"),
        hide_input=True,
    )
    if pw is not None and (pw != "" or existing.get("POSTGRES_PASSWORD")):
        updates["POSTGRES_PASSWORD"] = pw
    updates["POSTGRES_DATABASE"] = click.prompt(
        "PostgreSQL database name",
        default=existing.get("POSTGRES_DATABASE") or "postgres",
        show_default=True,
    )
    return updates


def _wizard_llm(existing: Dict[str, str]) -> Dict[str, str]:
    updates: Dict[str, str] = {}
    provider_default = existing.get("LLM_PROVIDER") or "qwen"
    provider = click.prompt(
        "LLM provider",
        default=provider_default,
        show_default=True,
    ).lower()
    updates["LLM_PROVIDER"] = provider

    api_key = _prompt_keep_or_set("LLM API key (LLM_API_KEY)", existing.get("LLM_API_KEY"), hide_input=True)
    if api_key is not None:
        updates["LLM_API_KEY"] = api_key

    model_default = existing.get("LLM_MODEL")
    if not model_default:
        model_default = "qwen-plus" if provider == "qwen" else "gpt-4o-mini"
    updates["LLM_MODEL"] = click.prompt("LLM model", default=model_default, show_default=True)

    temperature = _prompt_optional_float("LLM temperature (LLM_TEMPERATURE)", existing.get("LLM_TEMPERATURE"), default=0.7)
    if temperature is not None:
        updates["LLM_TEMPERATURE"] = str(temperature)

    max_tokens = _prompt_optional_int("LLM max tokens (LLM_MAX_TOKENS)", existing.get("LLM_MAX_TOKENS"), default=1000)
    if max_tokens is not None:
        updates["LLM_MAX_TOKENS"] = str(max_tokens)

    top_p = _prompt_optional_float("LLM top_p (LLM_TOP_P)", existing.get("LLM_TOP_P"), default=0.8)
    if top_p is not None:
        updates["LLM_TOP_P"] = str(top_p)

    top_k = _prompt_optional_int("LLM top_k (LLM_TOP_K)", existing.get("LLM_TOP_K"), default=50)
    if top_k is not None:
        updates["LLM_TOP_K"] = str(top_k)

    return updates


def _wizard_llm_quickstart(existing: Dict[str, str]) -> Dict[str, str]:
    """
    Quickstart LLM wizard: provider + key + model only (skip tuning knobs).
    """
    updates: Dict[str, str] = {}
    provider_default = existing.get("LLM_PROVIDER") or "qwen"
    provider = click.prompt(
        "LLM provider",
        default=provider_default,
        show_default=True,
    ).lower()
    updates["LLM_PROVIDER"] = provider

    api_key = _prompt_keep_or_set(
        "LLM API key (LLM_API_KEY)",
        existing.get("LLM_API_KEY"),
        hide_input=True,
    )
    if api_key is not None:
        updates["LLM_API_KEY"] = api_key

    model_default = existing.get("LLM_MODEL")
    if not model_default:
        model_default = "qwen-plus" if provider == "qwen" else "gpt-4o-mini"
    updates["LLM_MODEL"] = click.prompt("LLM model", default=model_default, show_default=True)
    return updates


def _wizard_embedder(existing: Dict[str, str], llm_updates: Dict[str, str]) -> Dict[str, str]:
    updates: Dict[str, str] = {}
    provider_default = existing.get("EMBEDDING_PROVIDER") or "qwen"
    provider = click.prompt(
        "Embedding provider",
        default=provider_default,
        show_default=True,
    ).lower()
    updates["EMBEDDING_PROVIDER"] = provider

    current_key = existing.get("EMBEDDING_API_KEY")
    if not current_key and (llm_updates.get("LLM_API_KEY") or existing.get("LLM_API_KEY")):
        if click.confirm("Reuse LLM_API_KEY for embeddings?", default=True):
            updates["EMBEDDING_API_KEY"] = llm_updates.get("LLM_API_KEY") or existing.get("LLM_API_KEY", "")
        else:
            api_key = _prompt_keep_or_set("Embedding API key (EMBEDDING_API_KEY)", current_key, hide_input=True)
            if api_key is not None:
                updates["EMBEDDING_API_KEY"] = api_key
    else:
        api_key = _prompt_keep_or_set("Embedding API key (EMBEDDING_API_KEY)", current_key, hide_input=True)
        if api_key is not None:
            updates["EMBEDDING_API_KEY"] = api_key

    model_default = existing.get("EMBEDDING_MODEL") or "text-embedding-v4"
    updates["EMBEDDING_MODEL"] = click.prompt("Embedding model", default=model_default, show_default=True)

    dims_current = existing.get("EMBEDDING_DIMS") or existing.get("DIMS")
    dims = click.prompt(
        "Embedding dimensions (EMBEDDING_DIMS)",
        type=int,
        default=int(dims_current) if dims_current and dims_current.isdigit() else 1536,
        show_default=True,
    )
    updates["EMBEDDING_DIMS"] = str(dims)
    return updates


def _wizard_embedder_quickstart(existing: Dict[str, str], llm_updates: Dict[str, str]) -> Dict[str, str]:
    """
    Quickstart embedder wizard: provider + key + model + dims.
    """
    updates: Dict[str, str] = {}
    provider_default = existing.get("EMBEDDING_PROVIDER") or "qwen"
    provider = click.prompt(
        "Embedding provider",
        default=provider_default,
        show_default=True,
    ).lower()
    updates["EMBEDDING_PROVIDER"] = provider

    current_key = existing.get("EMBEDDING_API_KEY")
    if not current_key and (llm_updates.get("LLM_API_KEY") or existing.get("LLM_API_KEY")):
        if click.confirm("Reuse LLM_API_KEY for embeddings?", default=True):
            updates["EMBEDDING_API_KEY"] = llm_updates.get("LLM_API_KEY") or existing.get("LLM_API_KEY", "")
        else:
            api_key = _prompt_keep_or_set(
                "Embedding API key (EMBEDDING_API_KEY)",
                current_key,
                hide_input=True,
            )
            if api_key is not None:
                updates["EMBEDDING_API_KEY"] = api_key
    else:
        api_key = _prompt_keep_or_set(
            "Embedding API key (EMBEDDING_API_KEY)",
            current_key,
            hide_input=True,
        )
        if api_key is not None:
            updates["EMBEDDING_API_KEY"] = api_key

    model_default = existing.get("EMBEDDING_MODEL") or "text-embedding-v4"
    updates["EMBEDDING_MODEL"] = click.prompt("Embedding model", default=model_default, show_default=True)

    dims_current = existing.get("EMBEDDING_DIMS") or existing.get("DIMS")
    dims = click.prompt(
        "Embedding dimensions (EMBEDDING_DIMS)",
        type=int,
        default=int(dims_current) if dims_current and dims_current.isdigit() else 1536,
        show_default=True,
    )
    updates["EMBEDDING_DIMS"] = str(dims)
    return updates


def _sync_vector_dims_quickstart(db_provider: str, dims: str) -> Dict[str, str]:
    """
    Quickstart sync: automatically set required vector dims keys without prompting.
    """
    updates: Dict[str, str] = {}
    if db_provider == "oceanbase":
        updates["OCEANBASE_EMBEDDING_MODEL_DIMS"] = dims
    elif db_provider == "postgres":
        updates["POSTGRES_EMBEDDING_MODEL_DIMS"] = dims
    return updates


def _maybe_sync_vector_dims(db_provider: str, dims: str) -> Dict[str, str]:
    updates: Dict[str, str] = {}
    if db_provider == "oceanbase":
        if click.confirm("Sync OceanBase embedding dims with EMBEDDING_DIMS?", default=True):
            updates["OCEANBASE_EMBEDDING_MODEL_DIMS"] = dims
    if db_provider == "postgres":
        if click.confirm("Sync Postgres embedding dims with EMBEDDING_DIMS?", default=True):
            updates["POSTGRES_EMBEDDING_MODEL_DIMS"] = dims
    return updates


def _print_planned_updates(updates: Dict[str, str]) -> None:
    click.echo()
    click.echo("Planned updates:")
    for key in sorted(updates.keys()):
        click.echo(f"  - {key}={_mask_for_display(key, updates[key])}")
    click.echo()


@click.command(name="init")
@click.option(
    "--env-file",
    "-f",
    type=click.Path(dir_okay=False, path_type=str),
    default=None,
    help="Target .env file to write (default: auto-detected or ./.env)",
)
@click.option("--dry-run", is_flag=True, help="Show changes without writing the file")
@click.option(
    "--test/--no-test",
    default=False,
    help="Run validation and connectivity tests after writing",
)
@click.option(
    "--component",
    "-c",
    type=click.Choice(["database", "llm", "embedder", "all"]),
    default="all",
    help="Component to test when --test is enabled",
)
@click.option("--advanced", is_flag=True, help="Include optional modules (future extension)")
@pass_context
def init_cmd(ctx: CLIContext, env_file: Optional[str], dry_run: bool, test: bool, component: str, advanced: bool):
    """
    Interactive configuration wizard that writes to a .env file.

    Examples:
        pmem config init
        pmem config init -f .env.production
        pmem config init --dry-run
        pmem config init --test --component database
    """
    del advanced  # reserved for future modules

    target = env_file or ctx.env_file or _resolve_default_env_file()
    target_path = str(Path(target))

    click.echo("=" * 60)
    click.echo("PowerMem Interactive Configuration")
    click.echo("=" * 60)
    click.echo(f"Target env file: {target_path}")

    use_example_as_template = False
    if Path(target_path).exists():
        if not click.confirm("Use this file?", default=True):
            use_example_as_template = True
            target_path = click.prompt("Enter target .env file path", default=target_path, show_default=True)
    else:
        if not click.confirm("File does not exist. Create it?", default=True):
            print_info("Cancelled.")
            sys.exit(0)

    # If the target file doesn't exist, use `.env.example` as the source of defaults.
    # When the user answered "n" to "Use this file?", also use `.env.example` as the
    # template and merge with existing target values so .env values are not lost.
    if use_example_as_template:
        template_path = _discover_env_example(Path.cwd())
        if template_path:
            _, existing = read_env_file(str(template_path))
            if Path(target_path).exists():
                _, from_target = read_env_file(target_path)
                for k, v in from_target.items():
                    if (v or "").strip() and (v or "").strip().lower() not in _ENV_PLACEHOLDER_VALUES:
                        existing[k] = v
        else:
            template_path = None
            if Path(target_path).exists():
                _, existing = read_env_file(target_path)
            else:
                existing = {}
    elif Path(target_path).exists():
        _, existing = read_env_file(target_path)
        template_path = None
    else:
        template_path = _discover_env_example(Path.cwd())
        if template_path:
            _, existing = read_env_file(str(template_path))
        else:
            existing = {}
    existing = _normalize_existing_env_values(existing)

    mode = click.prompt(
        "Wizard mode",
        type=click.Choice(["quickstart", "custom"], case_sensitive=False),
        default="quickstart",
        show_default=True,
    ).lower()

    updates: Dict[str, str] = {}
    llm_updates: Dict[str, str] = {}

    if mode in ("quickstart", "custom"):
        if mode == "quickstart":
            db_updates = _wizard_database_quickstart(existing)
            updates.update(db_updates)
            llm_updates = _wizard_llm_quickstart(existing)
            updates.update(llm_updates)
            emb_updates = _wizard_embedder_quickstart(existing, llm_updates=llm_updates)
            updates.update(emb_updates)

            db_provider = updates.get("DATABASE_PROVIDER") or existing.get("DATABASE_PROVIDER") or "sqlite"
            dims = emb_updates.get("EMBEDDING_DIMS")
            if dims:
                updates.update(_sync_vector_dims_quickstart(db_provider=db_provider, dims=dims))
        else:
            if click.confirm("Configure database (vector store)?", default=True):
                db_updates = _wizard_database(existing)
                updates.update(db_updates)
            if click.confirm("Configure LLM?", default=True):
                llm_updates = _wizard_llm(existing)
                updates.update(llm_updates)
            if click.confirm("Configure embedder?", default=True):
                emb_updates = _wizard_embedder(existing, llm_updates=llm_updates)
                updates.update(emb_updates)

                db_provider = updates.get("DATABASE_PROVIDER") or existing.get("DATABASE_PROVIDER") or "sqlite"
                dims = emb_updates.get("EMBEDDING_DIMS")
                if dims:
                    updates.update(_maybe_sync_vector_dims(db_provider=db_provider, dims=dims))

    if not updates:
        print_warning("No updates selected. Nothing to do.")
        return

    _print_planned_updates(updates)

    if dry_run:
        print_info("Dry-run mode: no files were written.")
        return

    if not click.confirm("Write these changes to the env file?", default=True):
        print_warning("Aborted. No changes were written.")
        return

    result = update_env_file(target_path, updates)
    print_success(f"Wrote configuration to {result.path}")
    if result.backup_path:
        print_info(f"Backup created at {result.backup_path}")

    if test:
        print_info("Reloading configuration for validation/testing...")
        try:
            os.environ["POWERMEM_ENV_FILE"] = target_path
            config = _load_config_with_env_file(target_path)
            validation = _validate_loaded_config(config, strict=False)
            if validation["errors"]:
                print_error("Validation failed after writing env file.")
                for e in validation["errors"]:
                    click.echo(f"  - {e}")
            else:
                print_success("Validation passed.")
                if validation["warnings"]:
                    print_warning("Warnings:")
                    for w in validation["warnings"]:
                        click.echo(f"  - {w}")

            # Run connectivity tests using a fresh CLIContext to avoid cache reuse
            test_ctx = CLIContext()
            test_ctx.env_file = target_path
            test_ctx.json_output = False
            test_ctx.verbose = ctx.verbose

            # Reuse the existing logic by invoking the underlying actions inline
            # (kept consistent with `pmem config test`)
            if component in ("database", "all"):
                print_info("Testing database connection...")
                try:
                    memory = test_ctx.memory
                    stats = memory.get_statistics()
                    print_success("Database: Connected")
                    if ctx.verbose:
                        click.echo(f"  Total memories: {stats.get('total_memories', 0)}")
                except Exception as e:
                    print_error(f"Database: Failed - {e}")

            if component in ("llm", "all"):
                print_info("Testing LLM connection...")
                try:
                    memory = test_ctx.memory
                    if hasattr(memory, "llm") and memory.llm:
                        messages = [{"role": "user", "content": "Say 'test' and nothing else."}]
                        llm = memory.llm
                        if hasattr(llm, "generate_response"):
                            llm.generate_response(messages=messages)
                        else:
                            llm.generate(messages=messages)
                        print_success("LLM: Connected")
                    else:
                        print_warning("LLM: Skipped (not configured)")
                except Exception as e:
                    print_error(f"LLM: Failed - {e}")

            if component in ("embedder", "all"):
                print_info("Testing embedder connection...")
                try:
                    memory = test_ctx.memory
                    if hasattr(memory, "embedding") and memory.embedding:
                        embedding = memory.embedding.embed("test")
                        if embedding:
                            dims = len(embedding) if isinstance(embedding, list) else "N/A"
                            print_success(f"Embedder: Connected (dims={dims})")
                        else:
                            print_warning("Embedder: Connected but returned empty result")
                    else:
                        print_warning("Embedder: Skipped (not configured)")
                except Exception as e:
                    print_error(f"Embedder: Failed - {e}")
        except Exception as e:
            print_error(f"Post-write validation/testing failed: {e}")



# Add commands to group
config_group.add_command(show_cmd)
config_group.add_command(validate_cmd)
config_group.add_command(test_cmd)
config_group.add_command(init_cmd)