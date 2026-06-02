"""Tests for the zero-config storage default.

The OceanBase provider covers both deployment shapes: with no ``OCEANBASE_HOST``
configured it boots embedded seekdb on disk; with ``OCEANBASE_HOST`` set it
talks to a remote OceanBase cluster. There is intentionally no separate
``seekdb`` database provider — seekdb is just how the OceanBase backend
behaves in embedded mode.
"""

from __future__ import annotations


def test_memory_config_default_storage_is_oceanbase_in_embedded_mode(monkeypatch):
    """`MemoryConfig()` with no env vars defaults to the OceanBase provider
    with an empty host — i.e. embedded seekdb on disk.
    """
    monkeypatch.delenv("DATABASE_PROVIDER", raising=False)
    monkeypatch.delenv("OCEANBASE_HOST", raising=False)

    from powermem.configs import MemoryConfig
    from powermem.storage.config.oceanbase import OceanBaseConfig

    cfg = MemoryConfig()

    assert isinstance(cfg.vector_store, OceanBaseConfig)
    assert cfg.vector_store.host == ""  # → embedded seekdb mode
    assert cfg.vector_store.ob_path == "./seekdb_data"


def test_database_settings_default_provider_is_oceanbase(monkeypatch):
    monkeypatch.delenv("DATABASE_PROVIDER", raising=False)

    from powermem.config_loader import DatabaseSettings

    assert DatabaseSettings().provider == "oceanbase"


def test_oceanbase_provider_picks_up_remote_host(monkeypatch):
    """Setting OCEANBASE_HOST flips the same provider into remote mode."""
    monkeypatch.setenv("OCEANBASE_HOST", "ob.example.com")

    from powermem.storage.config.oceanbase import OceanBaseConfig

    cfg = OceanBaseConfig()
    assert cfg.host == "ob.example.com"
