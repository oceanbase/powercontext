import builtins

import pytest


def test_load_mcp_asgi_app_skips_mcp_import_when_fastmcp_missing(monkeypatch):
    pytest.importorskip("fastapi", exc_type=ImportError)

    from server import main

    monkeypatch.setattr(
        main, "find_spec", lambda name: None if name == "fastmcp" else object()
    )

    real_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name == "powermem.mcp.server":
            raise AssertionError("MCP server should not be imported without fastmcp")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    assert main._load_mcp_asgi_app() is None
