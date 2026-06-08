from __future__ import annotations

import powermem.platform_defaults as platform_defaults


def _mock_find_spec(monkeypatch, available: set[str]):
    def fake_find_spec(name):
        return object() if name in available else None

    monkeypatch.setattr(platform_defaults.importlib.util, "find_spec", fake_find_spec)


def test_windows_zero_config_defaults_to_sqlite(monkeypatch):
    monkeypatch.delenv("DATABASE_PROVIDER", raising=False)
    monkeypatch.delenv("OCEANBASE_HOST", raising=False)
    monkeypatch.setattr(platform_defaults.sys, "platform", "win32")
    _mock_find_spec(monkeypatch, {"pyobvector", "pyseekdb"})

    decision = platform_defaults.choose_default_database_provider()

    assert decision.provider == "sqlite"
    assert decision.defaulted is True
    assert decision.embedded_seekdb_available is False


def test_linux_with_seekdb_defaults_to_oceanbase(monkeypatch):
    monkeypatch.delenv("DATABASE_PROVIDER", raising=False)
    monkeypatch.delenv("OCEANBASE_HOST", raising=False)
    monkeypatch.setattr(platform_defaults.sys, "platform", "linux")
    _mock_find_spec(monkeypatch, {"pyobvector", "pyseekdb", "pylibseekdb"})

    decision = platform_defaults.choose_default_database_provider()

    assert decision.provider == "oceanbase"
    assert decision.defaulted is True
    assert decision.embedded_seekdb_available is True


def test_linux_without_seekdb_defaults_to_sqlite(monkeypatch):
    monkeypatch.delenv("DATABASE_PROVIDER", raising=False)
    monkeypatch.delenv("OCEANBASE_HOST", raising=False)
    monkeypatch.setattr(platform_defaults.sys, "platform", "linux")
    _mock_find_spec(monkeypatch, {"pyobvector", "pyseekdb"})

    assert platform_defaults.default_database_provider() == "sqlite"


def test_modules_with_missing_spec_are_treated_as_unavailable(monkeypatch):
    monkeypatch.delenv("DATABASE_PROVIDER", raising=False)
    monkeypatch.delenv("OCEANBASE_HOST", raising=False)
    monkeypatch.setattr(platform_defaults.sys, "platform", "linux")

    def bad_find_spec(name):
        if name == "pyobvector":
            raise ValueError("pyobvector.__spec__ is not set")
        return object()

    monkeypatch.setattr(platform_defaults.importlib.util, "find_spec", bad_find_spec)

    assert platform_defaults.embedded_seekdb_available() is False
    assert platform_defaults.default_database_provider() == "sqlite"
    assert "pyobvector=False" in platform_defaults.embedded_seekdb_unavailable_message()


def test_explicit_database_provider_wins(monkeypatch):
    monkeypatch.setenv("DATABASE_PROVIDER", "postgres")
    monkeypatch.setenv("OCEANBASE_HOST", "ob.example.com")
    monkeypatch.setattr(platform_defaults.sys, "platform", "linux")
    _mock_find_spec(monkeypatch, {"pyobvector", "pyseekdb", "pylibseekdb"})

    decision = platform_defaults.choose_default_database_provider()

    assert decision.provider == "postgres"
    assert decision.defaulted is False


def test_oceanbase_host_selects_remote_oceanbase(monkeypatch):
    monkeypatch.delenv("DATABASE_PROVIDER", raising=False)
    monkeypatch.setenv("OCEANBASE_HOST", "ob.example.com")
    monkeypatch.setattr(platform_defaults.sys, "platform", "win32")
    _mock_find_spec(monkeypatch, set())

    decision = platform_defaults.choose_default_database_provider()

    assert decision.provider == "oceanbase"
    assert decision.defaulted is True


def test_sqlite_capabilities_include_full_stack_warning():
    capabilities = platform_defaults.storage_capabilities("sqlite", defaulted=True)

    assert capabilities["full_stack_available"] is False
    assert "Graph Store is not available" in capabilities["limitations"]
    assert "OceanBase" in capabilities["recommendation"]
