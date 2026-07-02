#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
# shellcheck disable=SC1091
. "$SCRIPT_DIR/common.sh"

echo "PowerMem Claude Code plugin init"
echo "Data dir: $DATA_DIR"

# --- Remote mode short-circuit ---
# Triggered when the user provides a remote PowerMem server URL via
# POWERMEM_INIT_BASE_URL (AskUserQuestion) or POWERMEM_BASE_URL (env, non-localhost).
# Skips .env creation, uvx launch, PID management.
# Connection mode (POWERMEM_INIT_CONNECTION_MODE=hook|mcp|both, default both)
# decides which files get written:
#   hook → runtime.env with URL + key; remove powermem from user-level mcpServers
#   mcp  → user-level mcpServers only; runtime.env gets POWERMEM_HOOK_DISABLED=1
#          so the hook launcher exits early instead of falling back to a stale URL
#   both → both sources populated, hook enabled
# MCP config is written to the user-scope config (~/.claude.json top-level
# mcpServers) so it survives plugin reinstalls and applies to all projects.
remote_init_url="${POWERMEM_INIT_BASE_URL:-${POWERMEM_BASE_URL:-}}"
if [ -n "$remote_init_url" ] && is_remote_url "$remote_init_url"; then
  remote_api_key="${POWERMEM_INIT_API_KEY:-${POWERMEM_API_KEY:-}}"
  remote_mode="${POWERMEM_INIT_CONNECTION_MODE:-both}"
  case "$remote_mode" in
    hook|mcp|both) ;;
    *) echo "ERROR: POWERMEM_INIT_CONNECTION_MODE must be hook, mcp, or both (got: $remote_mode)" >&2; exit 1 ;;
  esac
  echo "Remote server mode: $remote_init_url (connection: $remote_mode)"
  mkdir -p "$DATA_DIR"

  echo "Verifying connectivity..."
  if ! is_healthy "$remote_init_url"; then
    echo "ERROR: remote server at $remote_init_url is not healthy." >&2
    echo "Check the URL and any required API key." >&2
    exit 1
  fi
  echo "Remote server healthy: $remote_init_url"

  case "$remote_mode" in
    hook|both)
      write_runtime_remote "$remote_init_url" "$remote_api_key"
      echo "Wrote $RUNTIME_FILE"
      ;;
  esac

  case "$remote_mode" in
    mcp|both)
      mcp_url=$(printf '%s' "$remote_init_url" | sed 's:/*$::')"/mcp"
      write_user_mcp_config "$mcp_url" "$remote_api_key"
      echo "Wrote powermem MCP server to user-scope config (url=$mcp_url)"
      ;;
  esac

  case "$remote_mode" in
    hook)
      # Remove powermem from user-level mcpServers so MCP is disabled in
      # hook-only mode. Other MCP servers are preserved.
      remove_user_mcp_config
      echo "Removed powermem from user-scope config (MCP disabled)"
      ;;
    mcp)
      # Write a marker runtime.env so run-hook.sh exits early instead of
      # falling back to a stale POWERMEM_BASE_URL. Without this, the hook
      # binary would run with whatever URL was last configured.
      write_runtime_hook_disabled
      echo "Wrote $RUNTIME_FILE (hook disabled; MCP-only mode)"
      ;;
  esac

  exit 0
fi

base_url=$(runtime_base_url)

ensure_bootstrap_python || exit 1
echo "Bootstrap Python: $BOOTSTRAP_PYTHON ($(python_version "$BOOTSTRAP_PYTHON"))"

# Interactive configuration prompts.
create_env_file() {
  "$BOOTSTRAP_PYTHON" - "$ENV_FILE" "$DATA_DIR" <<'PY'
import json
import os
import sys
from pathlib import Path

env_path = Path(sys.argv[1])
data_dir = Path(sys.argv[2]).expanduser()

def read_claude_settings():
    path = Path.home() / ".claude" / "settings.json"
    if not path.is_file():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(
            f"Warning: failed to read Claude settings {path}: {exc}; "
            "ignoring settings fallback.",
            file=sys.stderr,
        )
        return {}
    return loaded if isinstance(loaded, dict) else {}

def env_first(*names):
    value, _ = env_first_with_source(*names)
    return value

def env_first_with_source(*names):
    for name in names:
        value = os.environ.get(name)
        if value is not None and value.strip():
            return value.strip(), f"env:{name}"
    return "", ""

def settings_first(settings_env, *names):
    value, _ = settings_first_with_source(settings_env, *names)
    return value

def settings_first_with_source(settings_env, *names):
    for name in names:
        value = settings_env.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip(), f"settings.env:{name}"
    return "", ""

def first_value(*items):
    for value, source in items:
        if value:
            return value, source
    return "", ""

def path_value(*parts):
    return str(data_dir.joinpath(*parts))

def provider_base_url_from_env(provider):
    value, _ = provider_base_url_from_env_source(provider)
    return value

def provider_base_url_from_env_source(provider):
    provider_base_envs = {
        "anthropic": ("ANTHROPIC_BASE_URL",),
        "openai": ("OPENAI_BASE_URL",),
        "qwen": ("QWEN_BASE_URL",),
        "siliconflow": ("SILICONFLOW_BASE_URL",),
        "deepseek": ("DEEPSEEK_BASE_URL",),
    }
    return env_first_with_source(*provider_base_envs.get(provider, ()))

def provider_base_url_from_settings(settings_env, provider):
    value, _ = provider_base_url_from_settings_source(settings_env, provider)
    return value

def provider_base_url_from_settings_source(settings_env, provider):
    provider_base_keys = {
        "anthropic": ("ANTHROPIC_BASE_URL",),
        "openai": ("OPENAI_BASE_URL",),
        "qwen": ("QWEN_BASE_URL",),
        "siliconflow": ("SILICONFLOW_BASE_URL",),
        "deepseek": ("DEEPSEEK_BASE_URL",),
    }
    return settings_first_with_source(settings_env, *(provider_base_keys.get(provider, ()) + ("LLM_BASE_URL",)))

settings = read_claude_settings()
settings_env = settings.get("env") if isinstance(settings.get("env"), dict) else {}
settings_model = settings.get("model") if isinstance(settings.get("model"), str) else ""
raw_model, model_source = first_value(
    env_first_with_source("POWERMEM_INIT_LLM_MODEL", "LLM_MODEL", "ANTHROPIC_MODEL"),
    settings_first_with_source(settings_env, "ANTHROPIC_MODEL", "LLM_MODEL"),
    (settings_model.strip(), "settings.model" if settings_model.strip() else ""),
)
raw_model = raw_model.strip()

explicit_base_url, explicit_base_url_source = env_first_with_source("POWERMEM_INIT_LLM_BASE_URL", "LLM_BASE_URL")
env_api_key, env_api_key_source = first_value(
    env_first_with_source("POWERMEM_INIT_LLM_API_KEY", "LLM_API_KEY"),
    env_first_with_source("ANTHROPIC_API_KEY"),
)
env_auth_token, env_auth_token_source = first_value(
    env_first_with_source("POWERMEM_INIT_LLM_AUTH_TOKEN", "LLM_AUTH_TOKEN"),
    env_first_with_source("ANTHROPIC_AUTH_TOKEN"),
)
env_anthropic_base_url, env_anthropic_base_url_source = env_first_with_source("ANTHROPIC_BASE_URL")
env_auth_base_url, env_auth_base_url_source = first_value(
    (explicit_base_url, explicit_base_url_source),
    (env_anthropic_base_url, env_anthropic_base_url_source),
)
settings_api_key, settings_api_key_source = settings_first_with_source(settings_env, "ANTHROPIC_API_KEY")
settings_auth_token, settings_auth_token_source = settings_first_with_source(settings_env, "ANTHROPIC_AUTH_TOKEN")
settings_auth_base_value, settings_auth_base_source = settings_first_with_source(
    settings_env,
    "ANTHROPIC_BASE_URL",
    "LLM_BASE_URL",
)
settings_auth_base_url, settings_auth_base_url_source = first_value(
    (explicit_base_url, explicit_base_url_source),
    (settings_auth_base_value, settings_auth_base_source),
)

api_key = ""
api_key_source = ""
auth_token = ""
auth_token_source = ""
base_url = ""
base_url_source = ""
credential_source_group = ""
if env_api_key:
    api_key = env_api_key
    api_key_source = env_api_key_source
    credential_source_group = "env"
elif env_auth_token and env_auth_base_url:
    auth_token = env_auth_token
    auth_token_source = env_auth_token_source
    base_url = env_auth_base_url
    base_url_source = env_auth_base_url_source
    credential_source_group = "env"
elif settings_api_key:
    api_key = settings_api_key
    api_key_source = settings_api_key_source
    credential_source_group = "settings"
elif settings_auth_token and settings_auth_base_url:
    auth_token = settings_auth_token
    auth_token_source = settings_auth_token_source
    base_url = settings_auth_base_url
    base_url_source = settings_auth_base_url_source
    credential_source_group = "settings"
elif env_auth_token:
    auth_token = env_auth_token
    auth_token_source = env_auth_token_source
    credential_source_group = "env"
elif settings_auth_token:
    auth_token = settings_auth_token
    auth_token_source = settings_auth_token_source
    credential_source_group = "settings"

key_provider = "anthropic" if auth_token or api_key else ""

model_prefix = raw_model.split("/", 1)[0].strip().lower() if "/" in raw_model else ""
explicit_provider, explicit_provider_source = env_first_with_source("POWERMEM_INIT_LLM_PROVIDER", "LLM_PROVIDER")
settings_provider, settings_provider_source = settings_first_with_source(settings_env, "LLM_PROVIDER")
provider_candidates = [
    (explicit_provider, explicit_provider_source),
]
if credential_source_group != "env":
    provider_candidates.append((settings_provider, settings_provider_source))
provider_candidates.extend(
    [
        (key_provider, "inferred:anthropic credential" if key_provider else ""),
        (model_prefix, "inferred:LLM model prefix" if model_prefix else ""),
    ]
)
provider, provider_source = first_value(*provider_candidates)
provider = provider.lower()

model = raw_model

if not base_url and not auth_token:
    base_url = explicit_base_url
    base_url_source = explicit_base_url_source
if not base_url and not auth_token:
    if credential_source_group == "env":
        base_url, base_url_source = provider_base_url_from_env_source(provider)
    elif credential_source_group == "settings":
        base_url, base_url_source = provider_base_url_from_settings_source(settings_env, provider)
    else:
        base_url, base_url_source = provider_base_url_from_env_source(provider)
        if not base_url:
            base_url, base_url_source = provider_base_url_from_settings_source(settings_env, provider)
base_url = base_url.strip()

missing = []
llm_credentials_missing = False
if not provider:
    missing.append("POWERMEM_INIT_LLM_PROVIDER")
    llm_credentials_missing = True
if not model:
    missing.append("POWERMEM_INIT_LLM_MODEL")
if provider not in {"ollama", "vllm"}:
    if provider == "anthropic":
        if not auth_token and not api_key:
            llm_credentials_missing = True
            missing.append(
                "ANTHROPIC_AUTH_TOKEN + ANTHROPIC_BASE_URL, "
                "ANTHROPIC_API_KEY, or POWERMEM_INIT_LLM_API_KEY"
            )
        if auth_token and not base_url:
            llm_credentials_missing = True
            missing.append("ANTHROPIC_BASE_URL or POWERMEM_INIT_LLM_BASE_URL")
    elif not api_key:
        llm_credentials_missing = True
        missing.append("POWERMEM_INIT_LLM_API_KEY or LLM_API_KEY")

if missing:
    if llm_credentials_missing:
        print("No complete LLM configuration found: " + ", ".join(missing))
        print(
            "PowerMem will run in no-LLM mode. Basic memory add/search/update/delete "
            "will work, while fact extraction, profile extraction, query rewrite, "
            "compression, and graph extraction will be skipped."
        )
        provider = "noop"
        provider_source = "fallback:no complete LLM config"
        model = "noop"
        model_source = "fallback:no complete LLM config"
        api_key = ""
        api_key_source = ""
        auth_token = ""
        auth_token_source = ""
        base_url = ""
        base_url_source = ""
    else:
        print("Missing configuration: " + ", ".join(missing), file=sys.stderr)
        print("Run init again with these environment variables set.", file=sys.stderr)
        sys.exit(2)

VALID_PROVIDERS = {"sqlite", "oceanbase"}
db_provider = (env_first("POWERMEM_INIT_DATABASE_PROVIDER") or "sqlite").lower()
if db_provider not in VALID_PROVIDERS:
    print(f"Warning: unknown DATABASE_PROVIDER '{db_provider}', falling back to sqlite",
          file=sys.stderr)
    db_provider = "sqlite"

# For the SQLite path, use huggingface (sentence-transformers, local, no API key)
# instead of `default` which requires the seekdb extra.  OceanBase keeps `default`.
_embedding_fallback = "huggingface" if db_provider == "sqlite" else "default"

embedding_provider = env_first("POWERMEM_INIT_EMBEDDING_PROVIDER", "EMBEDDING_PROVIDER") or _embedding_fallback
embedding_provider = embedding_provider.lower()

embedding_model_defaults = {
    "none": "none",
    "default": "all-MiniLM-L6-v2",
    "huggingface": "all-MiniLM-L6-v2",
    "qwen": "text-embedding-v4",
    "openai": "text-embedding-3-small",
    "siliconflow": "BAAI/bge-m3",
    "ollama": "nomic-embed-text",
    "lmstudio": "text-embedding-nomic-embed-text-v1.5",
}
embedding_dim_defaults = {
    "none": "0",
    "default": "384",
    "huggingface": "384",
    "qwen": "1536",
    "openai": "1536",
    "siliconflow": "1024",
    "ollama": "768",
    "lmstudio": "768",
}
embedding_model = (
    env_first("POWERMEM_INIT_EMBEDDING_MODEL", "EMBEDDING_MODEL")
    or embedding_model_defaults.get(embedding_provider, embedding_model_defaults["default"])
)
embedding_dims = (
    env_first("POWERMEM_INIT_EMBEDDING_DIMS", "EMBEDDING_DIMS")
    or embedding_dim_defaults.get(embedding_provider, embedding_dim_defaults["default"])
)
embedding_api_key = env_first("POWERMEM_INIT_EMBEDDING_API_KEY", "EMBEDDING_API_KEY")
if not embedding_api_key:
    if embedding_provider == "qwen":
        embedding_api_key = env_first("QWEN_API_KEY", "DASHSCOPE_API_KEY") or settings_first(settings_env, "QWEN_API_KEY", "DASHSCOPE_API_KEY")
    elif embedding_provider == "openai":
        embedding_api_key = env_first("OPENAI_API_KEY") or settings_first(settings_env, "OPENAI_API_KEY")
    elif embedding_provider == "siliconflow":
        embedding_api_key = env_first("SILICONFLOW_API_KEY") or settings_first(settings_env, "SILICONFLOW_API_KEY")

if embedding_provider not in {"none", "default", "huggingface", "ollama", "lmstudio"} and not embedding_api_key:
    print(
        "Missing configuration: POWERMEM_INIT_EMBEDDING_API_KEY "
        f"for EMBEDDING_PROVIDER={embedding_provider}",
        file=sys.stderr,
    )
    sys.exit(2)

server_port = env_first("POWERMEM_SERVER_PORT", "POWERMEM_INIT_PORT") or "8848"
server_host = env_first("POWERMEM_SERVER_HOST") or "127.0.0.1"
server_workers = env_first("POWERMEM_SERVER_WORKERS") or "1"
server_log_file = env_first("POWERMEM_SERVER_LOG_FILE") or path_value("powermem-server.log")
logging_level = env_first("LOGGING_LEVEL") or "INFO"

if db_provider == "sqlite":
    db_lines = [
        "# Database: SQLite (lightweight, no external service required)",
        "DATABASE_PROVIDER=sqlite",
        f"SQLITE_PATH={path_value('powermem.db')}",
        "SQLITE_COLLECTION=memories",
        "SQLITE_ENABLE_WAL=true",
        "SQLITE_TIMEOUT=30",
    ]
else:
    db_lines = [
        "# Database: embedded seekdb through the OceanBase provider",
        "DATABASE_PROVIDER=oceanbase",
        "OCEANBASE_HOST=",
        f"OCEANBASE_PATH={path_value('seekdb_data')}",
        "OCEANBASE_PORT=2881",
        "OCEANBASE_USER=root@sys",
        "OCEANBASE_PASSWORD=",
        "OCEANBASE_DATABASE=powermem",
        "OCEANBASE_COLLECTION=memories",
        "OCEANBASE_INDEX_TYPE=HNSW",
        "OCEANBASE_VECTOR_METRIC_TYPE=cosine",
        f"OCEANBASE_EMBEDDING_MODEL_DIMS={embedding_dims}",
        "OCEANBASE_TEXT_FIELD=document",
        "OCEANBASE_VECTOR_FIELD=embedding",
        "OCEANBASE_PRIMARY_FIELD=id",
        "OCEANBASE_METADATA_FIELD=metadata",
        "OCEANBASE_VIDX_NAME=memories_vidx",
        "OCEANBASE_INCLUDE_SPARSE=false",
        "OCEANBASE_ENABLE_NATIVE_HYBRID=false",
    ]

lines = [
    "# Generated by the PowerMem Claude Code plugin.",
    "",
    "# Core paths",
    f"POWERMEM_DATA_DIR={data_dir}",
    "",
    *db_lines,
    "",
    "# LLM",
    f"LLM_PROVIDER={provider}",
    f"LLM_MODEL={model}",
]
if api_key:
    lines.append(f"LLM_API_KEY={api_key}")
if auth_token:
    lines.append(f"LLM_AUTH_TOKEN={auth_token}")
if base_url:
    key = f"{provider.upper()}_LLM_BASE_URL"
    if provider == "qwen":
        key = "QWEN_LLM_BASE_URL"
    lines.append(f"{key}={base_url}")
else:
    key = ""

lines.extend(
    [
        "",
        "# Embedding",
        f"EMBEDDING_PROVIDER={embedding_provider}",
        f"EMBEDDING_MODEL={embedding_model}",
        f"EMBEDDING_DIMS={embedding_dims}",
    ]
)
if embedding_api_key:
    lines.append(f"EMBEDDING_API_KEY={embedding_api_key}")
# Note: do NOT write HF_HUB_OFFLINE=1 here. PowerMem's HuggingFaceEmbedding
# manages offline behaviour itself via SentenceTransformer(local_files_only=True)
# and runs an internal ModelScope/HF download when the cache is empty. Forcing
# HF_HUB_OFFLINE=1 globally would block that download for non-CN users.

embedding_base_override = env_first("POWERMEM_INIT_EMBEDDING_BASE_URL", "EMBEDDING_BASE_URL")
embedding_base_keys = {
    "qwen": "QWEN_EMBEDDING_BASE_URL",
    "openai": "OPENAI_EMBEDDING_BASE_URL",
    "siliconflow": "SILICONFLOW_EMBEDDING_BASE_URL",
    "ollama": "OLLAMA_EMBEDDING_BASE_URL",
    "lmstudio": "LMSTUDIO_EMBEDDING_BASE_URL",
}
if embedding_base_override and embedding_provider in embedding_base_keys:
    lines.append(f"{embedding_base_keys[embedding_provider]}={embedding_base_override}")

lines.extend(
    [
        "# Server",
        f"POWERMEM_SERVER_HOST={server_host}",
        f"POWERMEM_SERVER_PORT={server_port}",
        f"POWERMEM_SERVER_WORKERS={server_workers}",
        "POWERMEM_SERVER_RELOAD=false",
        "POWERMEM_SERVER_AUTH_ENABLED=false",
        "POWERMEM_SERVER_API_KEYS=",
        "POWERMEM_SERVER_RATE_LIMIT_ENABLED=true",
        "POWERMEM_SERVER_RATE_LIMIT_PER_MINUTE=100",
        f"POWERMEM_SERVER_LOG_FILE={server_log_file}",
        "POWERMEM_SERVER_LOG_LEVEL=INFO",
        "POWERMEM_SERVER_LOG_FORMAT=json",
        "POWERMEM_SERVER_API_TITLE=PowerMem API",
        "POWERMEM_SERVER_API_VERSION=v1",
        "POWERMEM_SERVER_CORS_ENABLED=true",
        "POWERMEM_SERVER_CORS_ORIGINS=*",
        "",
        "# Logging",
        f"LOGGING_LEVEL={logging_level}",
        "LOGGING_FORMAT=json",
        f"LOGGING_FILE={path_value('powermem.log')}",
        f"AUDIT_LOG_FILE={path_value('audit.log')}",
        "",
        "# Vector store tuning",
        "VECTOR_STORE_BATCH_SIZE=50",
        "VECTOR_STORE_CACHE_SIZE=500",
        "VECTOR_STORE_INDEX_REBUILD_INTERVAL=86400",
        "",
        "# Optional retrieval features",
        "SPARSE_VECTOR_ENABLE=false",
        "",
    ]
)

env_path.parent.mkdir(parents=True, exist_ok=True)
env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
print(
    f"Wrote {env_path} with db_provider={db_provider}, llm_provider={provider}, llm_model={model}, "
    f"embedding_provider={embedding_provider}, embedding_model={embedding_model}, "
    f"embedding_dims={embedding_dims}, server_port={server_port}"
)
print("LLM config sources:")
print(f"  LLM_PROVIDER: {provider_source or 'not set'}")
print(f"  LLM_MODEL: {model_source or 'not set'}")
if api_key:
    print(f"  LLM_API_KEY: {api_key_source or 'unknown'} (value hidden)")
elif auth_token:
    print(f"  LLM_AUTH_TOKEN: {auth_token_source or 'unknown'} (value hidden)")
else:
    print("  LLM_API_KEY/LLM_AUTH_TOKEN: not required")
if key and base_url:
    print(f"  {key}: {base_url_source or 'unknown'} (value hidden)")
else:
    print("  *_LLM_BASE_URL: not set")
PY
}

validate_llm_config() {
  "$BOOTSTRAP_PYTHON" - "$ENV_FILE" <<'PY'
import json, os, sys, urllib.request, urllib.error
from pathlib import Path

env = {}
for line in Path(sys.argv[1]).read_text().splitlines():
    line = line.strip()
    if '=' in line and not line.startswith('#'):
        k, v = line.split('=', 1)
        env[k.strip()] = v.strip()

def env_value_with_source(*names):
    for name in names:
        value = env.get(name, '')
        if value:
            return value, f".env:{name}"
    return '', ''

provider = env.get('LLM_PROVIDER', '')
model    = env.get('LLM_MODEL', '')
api_key, api_key_source = env_value_with_source('LLM_API_KEY')
auth_token, auth_token_source = env_value_with_source('LLM_AUTH_TOKEN', 'ANTHROPIC_AUTH_TOKEN')
base_url_name = f'{provider.upper()}_LLM_BASE_URL' if provider else ''
base_url, base_url_source = env_value_with_source(base_url_name) if base_url_name else ('', '')
if provider == 'anthropic':
    if not base_url:
        base_url, base_url_source = env_value_with_source('ANTHROPIC_BASE_URL')
    if api_key:
        auth_token = ''
        auth_token_source = ''
    elif auth_token and not base_url:
        print(
            'LLM validation failed: ANTHROPIC_AUTH_TOKEN/LLM_AUTH_TOKEN requires '
            'ANTHROPIC_LLM_BASE_URL or ANTHROPIC_BASE_URL.',
            file=sys.stderr,
        )
        sys.exit(1)

if provider == 'noop':
    print("LLM validation skipped: no-LLM mode is enabled.")
    sys.exit(0)

if provider in {'ollama', 'vllm'} or not (api_key or auth_token):
    sys.exit(0)

print("LLM validation sources:")
print("  LLM_PROVIDER: .env:LLM_PROVIDER")
print("  LLM_MODEL: .env:LLM_MODEL")
if api_key:
    print(f"  LLM_API_KEY: {api_key_source} (value hidden)")
elif auth_token:
    print(f"  {auth_token_source.rsplit(':', 1)[-1]}: {auth_token_source} (value hidden)")
if base_url:
    print(f"  {base_url_source.rsplit(':', 1)[-1]}: {base_url_source} (value hidden)")
else:
    print("  *_LLM_BASE_URL: not set")

def anthropic_headers(api_key, auth_token):
    headers = {
        'anthropic-version': '2023-06-01',
        'content-type': 'application/json',
    }
    if auth_token:
        headers['authorization'] = f'Bearer {auth_token}'
    else:
        headers['x-api-key'] = api_key
    return headers

CONFIGS = {
    'anthropic': {
        'url':     lambda b: f"{b or 'https://api.anthropic.com'}/v1/messages",
        'headers': lambda k, t: anthropic_headers(k, t),
        'body':    lambda m: {'model': m, 'max_tokens': 1,
                              'messages': [{'role': 'user', 'content': 'hi'}]},
    },
    'openai': {
        'url':     lambda b: f"{b or 'https://api.openai.com'}/v1/chat/completions",
        'headers': lambda k, t: {'authorization': f'Bearer {k}',
                                 'content-type': 'application/json'},
        'body':    lambda m: {'model': m, 'max_tokens': 1,
                              'messages': [{'role': 'user', 'content': 'hi'}]},
    },
    'deepseek': {
        'url':     lambda b: f"{b or 'https://api.deepseek.com'}/v1/chat/completions",
        'headers': lambda k, t: {'authorization': f'Bearer {k}',
                                 'content-type': 'application/json'},
        'body':    lambda m: {'model': m, 'max_tokens': 1,
                              'messages': [{'role': 'user', 'content': 'hi'}]},
    },
    'qwen': {
        'url':     lambda b: f"{b or 'https://dashscope.aliyuncs.com/compatible-mode'}/v1/chat/completions",
        'headers': lambda k, t: {'authorization': f'Bearer {k}',
                                 'content-type': 'application/json'},
        'body':    lambda m: {'model': m, 'max_tokens': 1,
                              'messages': [{'role': 'user', 'content': 'hi'}]},
    },
}

cfg = CONFIGS.get(provider)
if not cfg:
    print(f"No test template for provider={provider!r}, skipping LLM validation")
    sys.exit(0)

url     = cfg['url'](base_url)
headers = cfg['headers'](api_key, auth_token)
body    = json.dumps(cfg['body'](model)).encode()

try:
    req = urllib.request.Request(url, data=body, headers=headers, method='POST')
    with urllib.request.urlopen(req, timeout=15):
        print(f"LLM test OK (provider={provider}, model={model})")
        sys.exit(0)
except urllib.error.HTTPError as e:
    err = e.read().decode(errors='replace')
    print(f"LLM test failed (HTTP {e.code}): {err}", file=sys.stderr)
    sys.exit(1)
except Exception as e:
    print(f"LLM test error: {e}", file=sys.stderr)
    sys.exit(1)
PY
}

stop_unhealthy_managed_server() {
  pid=$(managed_pid 2>/dev/null || true)
  if [ -n "$pid" ]; then
    echo "Stopping unhealthy managed powermem-server PID $pid."
    kill "$pid" 2>/dev/null || true
    sleep 2
    if kill -0 "$pid" 2>/dev/null; then
      echo "Managed server PID $pid is still running; sending SIGKILL."
      kill -9 "$pid" 2>/dev/null || true
    fi
  fi
  remove_managed_pid_files
}

announce_dashboard_url() {
  case "$1" in
    */) dashboard_url="${1}dashboard/" ;;
    *) dashboard_url="${1}/dashboard/" ;;
  esac
  echo "Memory dashboard: $dashboard_url"
}

if [ ! -f "$ENV_FILE" ]; then
  echo "Creating plugin .env from environment variables or Claude settings fallback."
  create_env_file
else
  echo "Using existing plugin .env: $ENV_FILE"
  echo "Checking managed powermem-server recorded for this env: $(managed_pid_file)"
  if pid_alive; then
    echo "Managed powermem-server is recorded and matches $ENV_FILE: $(managed_pid)"
    managed_url=$(managed_base_url || true)
    if [ -n "$managed_url" ]; then
      base_url=$managed_url
      echo "Managed powermem-server URL: $base_url"
    fi
    if is_healthy "$base_url"; then
      write_runtime_base_url "$base_url"
      echo "Managed PowerMem backend is healthy: $base_url"
      announce_dashboard_url "$base_url"
      echo "Hook will use this backend through $RUNTIME_FILE."
      exit 0
    fi
    echo "Managed powermem-server exists, but health check failed at $base_url; init will stop it and continue."
    stop_unhealthy_managed_server
  else
    echo "No matching managed powermem-server is recorded for $ENV_FILE; init will start one if no healthy backend is found."
    remove_managed_pid_files
  fi
fi

echo "Validating LLM config..."
if ! validate_llm_config; then
  echo "LLM validation failed. Check the provider, model, API key, and base URL." >&2
  echo "Re-run with POWERMEM_INIT_LLM_MODEL=<model> (and optionally POWERMEM_INIT_LLM_PROVIDER=<provider>) to override." >&2
  exit 1
fi

db_provider=$(grep '^DATABASE_PROVIDER=' "$ENV_FILE" 2>/dev/null \
  | cut -d= -f2 | tr -d '[:space:]')
db_provider="${db_provider:-sqlite}"
case "$db_provider" in
  oceanbase) PACKAGE="${POWERMEM_INIT_PACKAGE:-powermem[server,seekdb]}" ;;
  *)         PACKAGE="${POWERMEM_INIT_PACKAGE:-powermem[server,extras]}" ;;
esac

# For SQLite backend: probe FTS5/JSON1 support via the bootstrap Python and,
# if the bundled SQLite is too old (< 3.9.0), inject pysqlite3-binary into
# the uvx invocation so PowerMem gets a modern SQLite at runtime.
UVX_WITH_ARGS=""
if [ "$db_provider" != "oceanbase" ]; then
  _sqlite_ok=$("$BOOTSTRAP_PYTHON" -c \
    "import sqlite3; print(sqlite3.sqlite_version_info >= (3,9,0))" 2>/dev/null || echo False)
  if [ "$_sqlite_ok" != "True" ]; then
    _sys_ver=$("$BOOTSTRAP_PYTHON" -c 'import sqlite3; print(sqlite3.sqlite_version)' 2>/dev/null || echo unknown)
    echo "System SQLite $_sys_ver < 3.9.0; injecting pysqlite3-binary into uvx invocation."
    UVX_WITH_ARGS="--with pysqlite3-binary"
  fi
fi

if pid_alive; then
  echo "Managed PowerMem server process is running: $(managed_pid)"
  managed_url=$(managed_base_url || true)
  if [ -n "$managed_url" ]; then
    base_url=$managed_url
  fi
  if is_healthy "$base_url"; then
    write_runtime_base_url "$base_url"
    echo "Managed PowerMem backend is healthy: $base_url"
    announce_dashboard_url "$base_url"
    exit 0
  fi
  echo "Managed server PID exists but health check failed; stopping it before continuing."
  stop_unhealthy_managed_server
fi

if is_healthy "$base_url"; then
  write_runtime_base_url "$base_url"
  echo "External PowerMem backend is healthy: $base_url"
  announce_dashboard_url "$base_url"
  echo "Plugin config is ready. Not starting a managed server."
  exit 0
fi

ensure_uv
configure_uv_index
export_env_file_vars "$ENV_FILE"
echo "Backend package: $PACKAGE"
echo "Backend launcher: uvx --from '$PACKAGE' powermem-server"

if [ -n "${POWERMEM_INIT_PRELOAD_MODEL:-}" ]; then
  echo "POWERMEM_INIT_PRELOAD_MODEL is deprecated; the embedding model is now downloaded automatically by PowerMem at startup."
fi

if [ -n "${POWERMEM_INIT_PORT:-}" ]; then
  port=$POWERMEM_INIT_PORT
  if ! port_free "$port"; then
    describe_port "$port" >&2
    echo "Requested POWERMEM_INIT_PORT=$port is not available. Stop that process or choose another port." >&2
    exit 1
  fi
elif port_free 8848; then
  port=8848
else
  describe_port 8848 >&2
  echo "Port 8848 is occupied and not a healthy PowerMem backend; looking for a free port in 8849-8899." >&2
  port=$(find_free_port 8849 8899) || {
    echo "No free port found in 8849-8899." >&2
    describe_port 8848 >&2
    exit 1
  }
fi

echo "Starting PowerMem server on port $port with uvx"
if [ -n "${POWERMEM_UV_INDEX_URL:-}" ]; then
  POWERMEM_ENV_FILE="$ENV_FILE" nohup "$UV_BIN" tool run \
    --python "$BOOTSTRAP_PYTHON" \
    --default-index "$POWERMEM_UV_INDEX_URL" \
    --from "$PACKAGE" \
    $UVX_WITH_ARGS \
    powermem-server --host "${POWERMEM_SERVER_HOST:-127.0.0.1}" --port "$port" >> "$LOG_FILE" 2>&1 &
else
  POWERMEM_ENV_FILE="$ENV_FILE" nohup "$UV_BIN" tool run \
    --python "$BOOTSTRAP_PYTHON" \
    --from "$PACKAGE" \
    $UVX_WITH_ARGS \
    powermem-server --host "${POWERMEM_SERVER_HOST:-127.0.0.1}" --port "$port" >> "$LOG_FILE" 2>&1 &
fi
pid=$!
write_managed_pid "$pid"
echo "Recorded managed server PID $pid in $PID_FILE"

base_url="http://localhost:$port"

echo "Waiting for backend health: $base_url"
health_timeout=${POWERMEM_INIT_HEALTH_TIMEOUT_SECONDS:-300}
case "$health_timeout" in
  ""|*[!0-9]*) health_timeout=300 ;;
esac
max_checks=$((health_timeout / 2))
if [ "$max_checks" -lt 1 ]; then
  max_checks=1
fi
i=0
while [ "$i" -lt "$max_checks" ]; do
  if is_healthy "$base_url"; then
    write_runtime_base_url "$base_url"
    echo "PowerMem backend is healthy: $base_url"
    announce_dashboard_url "$base_url"
    echo "Hook will use this backend through $RUNTIME_FILE."
    echo "Log: $LOG_FILE"
    exit 0
  fi
  if ! kill -0 "$pid" 2>/dev/null; then
    echo "PowerMem server process exited before it became healthy." >&2
    echo "Check log: $LOG_FILE" >&2
    tail -n 80 "$LOG_FILE" >&2 2>/dev/null || true
    remove_managed_pid_files
    describe_port "$port" >&2
    exit 1
  fi
  i=$((i + 1))
  sleep 2
done

echo "PowerMem server did not become healthy within $((max_checks * 2)) seconds." >&2
echo "Check log: $LOG_FILE" >&2
stop_unhealthy_managed_server
describe_port "$port" >&2
exit 1
