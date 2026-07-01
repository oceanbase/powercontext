"""Tests for the zero-config storage default.

The OceanBase provider covers both deployment shapes: with no ``OCEANBASE_HOST``
configured it boots embedded seekdb on disk; with ``OCEANBASE_HOST`` set it
talks to a remote OceanBase cluster. There is intentionally no separate
``seekdb`` database provider — seekdb is just how the OceanBase backend
behaves in embedded mode.
"""

from __future__ import annotations


def test_memory_config_default_storage_is_oceanbase_when_embedded_seekdb_available(monkeypatch):
    """Linux with embedded SeekDB capability keeps the OceanBase embedded default."""
    monkeypatch.delenv("DATABASE_PROVIDER", raising=False)
    monkeypatch.delenv("OCEANBASE_HOST", raising=False)

    import powermem.platform_defaults as platform_defaults

    monkeypatch.setattr(platform_defaults, "_configured_env_files", lambda: [])
    monkeypatch.setattr(platform_defaults.sys, "platform", "linux")
    monkeypatch.setattr(
        platform_defaults.importlib.util,
        "find_spec",
        lambda name: object()
        if name in {"pyobvector", "pyseekdb", "pylibseekdb"}
        else None,
    )

    from powermem.configs import MemoryConfig
    from powermem.storage.config.oceanbase import OceanBaseConfig

    cfg = MemoryConfig()

    assert isinstance(cfg.vector_store, OceanBaseConfig)
    assert cfg.vector_store.host == ""  # → embedded seekdb mode
    assert cfg.vector_store.ob_path == "./seekdb_data"


def test_database_settings_default_provider_matches_platform_helper(monkeypatch):
    monkeypatch.delenv("DATABASE_PROVIDER", raising=False)
    monkeypatch.delenv("OCEANBASE_HOST", raising=False)

    import powermem.platform_defaults as platform_defaults
    from powermem.config_loader import DatabaseSettings

    monkeypatch.setattr(platform_defaults, "_configured_env_files", lambda: [])
    model_config = dict(DatabaseSettings.model_config)
    model_config["env_file"] = None
    monkeypatch.setattr(DatabaseSettings, "model_config", model_config)

    assert DatabaseSettings().provider == platform_defaults.default_database_provider()


def test_oceanbase_provider_picks_up_remote_host(monkeypatch):
    """Setting OCEANBASE_HOST flips the same provider into remote mode."""
    monkeypatch.setenv("OCEANBASE_HOST", "ob.example.com")

    from powermem.storage.config.oceanbase import OceanBaseConfig

    cfg = OceanBaseConfig()
    assert cfg.host == "ob.example.com"
