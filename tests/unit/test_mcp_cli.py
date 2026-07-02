import builtins
import importlib
import sys

import pytest


def _restore_module(module_name, saved_module):
    if saved_module is None:
        sys.modules.pop(module_name, None)
    else:
        sys.modules[module_name] = saved_module


def test_powermem_mcp_cli_help_does_not_import_server(monkeypatch, capsys):
    cli_module = "powermem.mcp.cli"
    server_module = "powermem.mcp.server"
    saved_cli = sys.modules.pop(cli_module, None)
    saved_server = sys.modules.pop(server_module, None)

    real_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name == server_module or name.startswith(server_module + "."):
            raise AssertionError("help should not import the MCP server module")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    try:
        cli = importlib.import_module(cli_module)
        with pytest.raises(SystemExit) as exc_info:
            cli.main(["--help"])
    finally:
        _restore_module(cli_module, saved_cli)
        _restore_module(server_module, saved_server)

    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    assert "usage:" in captured.out
    assert "streamable-http" in captured.out


def test_powermem_mcp_help_exits_without_starting_server(monkeypatch, capsys):
    pytest.importorskip("fastmcp", exc_type=ImportError)

    from powermem.mcp import server

    def fail_run(*args, **kwargs):
        raise AssertionError("help should not start the MCP server")

    monkeypatch.setattr(server.mcp, "run", fail_run)

    with pytest.raises(SystemExit) as exc_info:
        server.main(["--help"])

    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    assert "usage:" in captured.out
    assert "streamable-http" in captured.out
    assert "Starting PowerMem MCP Server" not in captured.err


def test_powermem_mcp_invalid_port_falls_back_to_default(monkeypatch, capsys):
    pytest.importorskip("fastmcp", exc_type=ImportError)

    from powermem.mcp import server

    calls = []

    def record_run(*args, **kwargs):
        calls.append((args, kwargs))

    monkeypatch.setattr(server.mcp, "run", record_run)

    server.main(["sse", "not-a-port"])

    captured = capsys.readouterr()
    assert "Invalid port 'not-a-port', using 8848" in captured.err
    assert calls == [
        (
            (),
            {
                "transport": "sse",
                "host": "0.0.0.0",
                "port": 8848,
                "path": "/mcp",
            },
        )
    ]
