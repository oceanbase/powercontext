import powermem.config_loader as config_loader
import powermem.settings as settings


def _reset_env(monkeypatch, keys):
    for key in keys:
        monkeypatch.delenv(key, raising=False)


def _disable_env_file(monkeypatch):
    monkeypatch.setattr(config_loader, "_DEFAULT_ENV_FILE", None, raising=False)
    monkeypatch.setattr(settings, "_DEFAULT_ENV_FILE", None, raising=False)


def test_load_config_from_env_builds_core_config(monkeypatch):
    _reset_env(
        monkeypatch,
        [
            "DATABASE_PROVIDER",
            "OCEANBASE_HOST",
            "OCEANBASE_PORT",
            "OCEANBASE_USER",
            "OCEANBASE_PASSWORD",
            "OCEANBASE_DATABASE",
            "OCEANBASE_COLLECTION",
            "LLM_PROVIDER",
            "LLM_API_KEY",
            "LLM_MODEL",
            "QWEN_LLM_BASE_URL",
            "EMBEDDING_PROVIDER",
            "EMBEDDING_API_KEY",
            "EMBEDDING_MODEL",
            "OPENAI_EMBEDDING_BASE_URL",
            "AGENT_MEMORY_MODE",
        ],
    )
    _disable_env_file(monkeypatch)
    monkeypatch.setenv("DATABASE_PROVIDER", "oceanbase")
    monkeypatch.setenv("OCEANBASE_HOST", "10.0.0.1")
    monkeypatch.setenv("OCEANBASE_PORT", "2881")
    monkeypatch.setenv("OCEANBASE_USER", "root@sys")
    monkeypatch.setenv("OCEANBASE_PASSWORD", "secret")
    monkeypatch.setenv("OCEANBASE_DATABASE", "powermem")
    monkeypatch.setenv("OCEANBASE_COLLECTION", "memories")
    monkeypatch.setenv("LLM_PROVIDER", "qwen")
    monkeypatch.setenv("LLM_API_KEY", "llm-key")
    monkeypatch.setenv("LLM_MODEL", "qwen-plus")
    monkeypatch.setenv("QWEN_LLM_BASE_URL", "https://qwen.example.com/v1")
    monkeypatch.setenv("EMBEDDING_PROVIDER", "openai")
    monkeypatch.setenv("EMBEDDING_API_KEY", "embed-key")
    monkeypatch.setenv("EMBEDDING_MODEL", "text-embedding-3-small")
    monkeypatch.setenv("OPENAI_EMBEDDING_BASE_URL", "https://emb.example.com/v1")
    monkeypatch.setenv("AGENT_MEMORY_MODE", "auto")

    config = config_loader.load_config_from_env()

    assert config["vector_store"]["provider"] == "oceanbase"
    assert config["vector_store"]["config"]["connection_args"]["host"] == "10.0.0.1"
    assert config["llm"]["provider"] == "qwen"
    assert config["llm"]["config"]["dashscope_base_url"] == "https://qwen.example.com/v1"
    assert config["embedder"]["provider"] == "openai"
    assert (
        config["embedder"]["config"]["openai_base_url"]
        == "https://emb.example.com/v1"
    )
    assert config["agent_memory"]["mode"] == "auto"


def test_load_config_from_env_graph_store_fallback(monkeypatch):
    _reset_env(
        monkeypatch,
        [
            "GRAPH_STORE_ENABLED",
            "GRAPH_STORE_HOST",
            "GRAPH_STORE_MAX_HOPS",
            "OCEANBASE_HOST",
            "OCEANBASE_PORT",
        ],
    )
    _disable_env_file(monkeypatch)
    monkeypatch.setenv("GRAPH_STORE_ENABLED", "true")
    monkeypatch.setenv("OCEANBASE_HOST", "127.0.0.2")
    monkeypatch.setenv("OCEANBASE_PORT", "2881")

    config = config_loader.load_config_from_env()

    graph_store = config["graph_store"]
    assert graph_store["enabled"] is True
    assert graph_store["config"]["host"] == "127.0.0.2"
    assert graph_store["config"]["max_hops"] == 3


def test_load_config_from_env_does_not_expose_internal_settings(monkeypatch):
    _reset_env(
        monkeypatch,
        [
            "MEMORY_BATCH_SIZE",
            "ENCRYPTION_ENABLED",
            "ACCESS_CONTROL_ENABLED",
            "MEMORY_DECAY_ENABLED",
            "DATABASE_SSLMODE",
        ],
    )
    _disable_env_file(monkeypatch)
    monkeypatch.setenv("MEMORY_BATCH_SIZE", "200")
    monkeypatch.setenv("ENCRYPTION_ENABLED", "true")
    monkeypatch.setenv("ACCESS_CONTROL_ENABLED", "false")
    monkeypatch.setenv("MEMORY_DECAY_ENABLED", "false")
    monkeypatch.setenv("DATABASE_SSLMODE", "require")

    config = config_loader.load_config_from_env()

    assert "performance" not in config
    assert "security" not in config
    assert "memory_decay" not in config


def test_load_config_from_env_telemetry_aliases(monkeypatch):
    _reset_env(
        monkeypatch,
        [
            "TELEMETRY_ENABLED",
            "TELEMETRY_BATCH_SIZE",
            "TELEMETRY_FLUSH_INTERVAL",
        ],
    )
    _disable_env_file(monkeypatch)
    monkeypatch.setenv("TELEMETRY_ENABLED", "true")
    monkeypatch.setenv("TELEMETRY_BATCH_SIZE", "42")
    monkeypatch.setenv("TELEMETRY_FLUSH_INTERVAL", "15")

    config = config_loader.load_config_from_env()

    telemetry = config["telemetry"]
    assert telemetry["enable_telemetry"] is True
    assert telemetry["telemetry_batch_size"] == 42
    assert telemetry["telemetry_flush_interval"] == 15
    assert telemetry["batch_size"] == 42
    assert telemetry["flush_interval"] == 15


def test_load_config_from_env_embedding_provider_values(monkeypatch):
    _reset_env(
        monkeypatch,
        [
            "EMBEDDING_PROVIDER",
            "EMBEDDING_API_KEY",
            "AZURE_OPENAI_API_KEY",
        ],
    )
    _disable_env_file(monkeypatch)
    monkeypatch.setenv("EMBEDDING_PROVIDER", "azure_openai")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "azure-key")

    config = config_loader.load_config_from_env()

    assert config["embedder"]["provider"] == "azure_openai"
    assert config["embedder"]["config"]["api_key"] == "azure-key"


def test_load_config_from_env_embedding_common_override(monkeypatch):
    _reset_env(
        monkeypatch,
        [
            "EMBEDDING_PROVIDER",
            "EMBEDDING_API_KEY",
            "AZURE_OPENAI_API_KEY",
        ],
    )
    _disable_env_file(monkeypatch)
    monkeypatch.setenv("EMBEDDING_PROVIDER", "azure_openai")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "azure-key")
    monkeypatch.setenv("EMBEDDING_API_KEY", "common-key")

    config = config_loader.load_config_from_env()

    assert config["embedder"]["provider"] == "azure_openai"
    assert config["embedder"]["config"]["api_key"] == "common-key"
