# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import annotations

import io
import json
from urllib.error import URLError

import pytest

from powercontext_eval_swebench_pro.powercontext_sut import _MCP_EVIDENCE_SCRIPT


@pytest.mark.parametrize(
    ("samples", "count"),
    [
        ("", 0),
        (
            (
                'powercontext_server_transport_requests_total{transport="http",operation="get_capabilities",outcome="success"} 9\n'
                'powercontext_server_transport_requests_total{operation="mcp.initialize",outcome="success",transport="mcp"} 1\n'
                'powercontext_server_transport_requests_total{transport="mcp",operation="mcp.tools.list",outcome="success"} 2\n'
                'powercontext_server_transport_requests_total{transport="mcp",operation="mcp.tools.call",outcome="failure"} 3\n'
                'powercontext_server_transport_requests_created{transport="mcp"} 100\n'
            ),
            6,
        ),
    ],
)
def test_mcp_probe_counts_prometheus_samples(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], samples: str, count: int
) -> None:
    payload = "# TYPE powercontext_server_transport_requests_total counter\n" + samples
    monkeypatch.setattr("urllib.request.urlopen", lambda *args, **kwargs: io.BytesIO(payload.encode()))

    exec(_MCP_EVIDENCE_SCRIPT, {})  # noqa: S102 -- Exercise the fixed, trusted container probe.

    assert json.loads(capsys.readouterr().out) == {"mcp_requests": count}


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        b"\xff",
        b"not prometheus metrics",
        b"# TYPE powercontext_server_transport_requests_total gauge\n",
        *[
            (
                "# TYPE powercontext_server_transport_requests_total counter\n"
                f'powercontext_server_transport_requests_total{{transport="mcp"}} {value}\n'
            ).encode()
            for value in ("-1", "1.5", "NaN", "+Inf", "invalid")
        ],
    ],
)
def test_mcp_probe_reports_missing_or_invalid_metrics(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], payload: bytes
) -> None:
    monkeypatch.setattr("urllib.request.urlopen", lambda *args, **kwargs: io.BytesIO(payload))

    exec(_MCP_EVIDENCE_SCRIPT, {})  # noqa: S102 -- Exercise the fixed, trusted container probe.

    assert json.loads(capsys.readouterr().out) == {"error": "malformed_metrics"}


@pytest.mark.parametrize("error", [URLError("unreachable"), TimeoutError("timed out")])
def test_mcp_probe_read_failure_does_not_produce_evidence(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], error: Exception
) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        raise error

    monkeypatch.setattr("urllib.request.urlopen", fail)

    with pytest.raises(type(error)):
        exec(_MCP_EVIDENCE_SCRIPT, {})  # noqa: S102 -- Exercise the fixed, trusted container probe.

    assert capsys.readouterr().out == ""
