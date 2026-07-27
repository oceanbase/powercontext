import json
from types import TracebackType
from typing import Self

import httpx
import pytest
from typer.testing import CliRunner

import powercontext.client.cli as client_cli
from powercontext.cli.app import create_cli
from powercontext.client import PowerContextClient
from powercontext.http import Capabilities, MemorySearchMode
from powercontext.server.app import create_app


def test_capabilities_flow_through_server_sdk_and_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    server_capabilities = Capabilities(
        source_types=["git-commit"],
        artifact_families=["memory", "handoff"],
        memory_extraction=True,
        search_modes=[MemorySearchMode.FTS],
    )
    server_app = create_app(capability_provider=lambda: server_capabilities)

    class InProcessClient:
        def __init__(self) -> None:
            self._http_client: httpx.AsyncClient | None = None
            self._sdk: PowerContextClient | None = None

        async def __aenter__(self) -> Self:
            self._http_client = httpx.AsyncClient(
                transport=httpx.ASGITransport(app=server_app),
                base_url="http://testserver",
            )
            self._sdk = PowerContextClient("http://testserver", http_client=self._http_client)
            return self

        async def __aexit__(
            self,
            exc_type: type[BaseException] | None,
            exc_value: BaseException | None,
            traceback: TracebackType | None,
        ) -> None:
            assert self._http_client is not None
            await self._http_client.aclose()

        async def get_capabilities(self) -> Capabilities:
            assert self._sdk is not None
            return await self._sdk.get_capabilities()

    monkeypatch.setattr(client_cli, "PowerContextClient", lambda *_args, **_kwargs: InProcessClient())
    result = CliRunner().invoke(create_cli([client_cli.app]), ["client", "--json", "capabilities"])

    assert result.exit_code == 0
    assert json.loads(result.output) == {
        "source_types": ["git-commit"],
        "artifact_families": ["memory", "handoff"],
        "memory_extraction": True,
        "search_modes": ["fts"],
    }
