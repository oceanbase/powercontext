import json

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

import powercontext.client.cli as client_cli
from powercontext.api import Capabilities
from powercontext.cli.app import create_cli
from powercontext.client import PowerContextClient
from powercontext.server.app import create_app


def test_capabilities_flow_through_server_sdk_and_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    server_capabilities = Capabilities(
        source_types=["git-commit"],
        artifact_families=["memory", "handoff"],
        memory_extraction=True,
        search_modes=["fts"],
    )
    server_app = create_app(capability_provider=lambda: server_capabilities)

    with TestClient(server_app) as http_client:
        sdk = PowerContextClient("http://testserver", http_client=http_client)

        def create_client(base_url: str, *, timeout: float) -> PowerContextClient:
            return sdk

        monkeypatch.setattr(client_cli, "PowerContextClient", create_client)
        result = CliRunner().invoke(create_cli([client_cli.app]), ["client", "--json", "capabilities"])

    assert result.exit_code == 0
    assert json.loads(result.output) == {
        "source_types": ["git-commit"],
        "artifact_families": ["memory", "handoff"],
        "memory_extraction": True,
        "search_modes": ["fts"],
    }
