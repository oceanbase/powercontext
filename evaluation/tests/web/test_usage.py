import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Self, cast

import pytest
from pydantic import ValidationError

from powercontext_eval.web.usage import (
    CLIENT_INFO,
    ApiKeyUsageProbe,
    CodexUsageProbe,
    UsageProtocolError,
    UsageSnapshot,
    UsageUnavailable,
    is_fresh,
)

NOW = datetime(2026, 7, 29, 9, 0, tzinfo=UTC)
RESET_TIMESTAMP = 1_785_902_973


@pytest.fixture
def fake_codex(tmp_path: Path) -> Path:
    executable = tmp_path / "fake-codex"
    executable.write_text(
        """#!/usr/bin/env python3
import json
import os
from pathlib import Path
import select
import sys
import time

home = Path(os.environ["CODEX_HOME"])
auth_path = home / "auth.json"
auth = json.loads(auth_path.read_text())
requests = []
line = sys.stdin.readline()
if line.strip():
    requests.append(json.loads(line))
print(json.dumps({"id": 0, "result": {"userAgent": "fake", "codexHome": str(home)}}), flush=True)
for _request in range(3):
    line = sys.stdin.readline()
    if not line:
        break
    if line.strip():
        requests.append(json.loads(line))
record_path = auth.get("record_path")
if record_path:
    Path(record_path).write_text(json.dumps({
        "requests": requests,
        "codex_home": str(home),
        "home_files": sorted(path.name for path in home.iterdir()),
        "auth_mode": oct(auth_path.stat().st_mode & 0o777),
        "proxy": os.environ.get("HTTPS_PROXY"),
    }))

mode = auth.get("mode", "normal")
secret = auth.get("secret", "")
if mode == "require_open_stdin":
    readable, _, _ = select.select([sys.stdin], [], [], 0)
    if readable and sys.stdin.read(1) == "":
        raise SystemExit(0)
if mode == "hang":
    time.sleep(10)
if mode == "nonzero":
    print(f"private failure: {secret}", file=sys.stderr)
    raise SystemExit(7)
if mode == "malformed":
    print("{not-json")
    raise SystemExit(0)
if mode == "oversize":
    print(json.dumps({"id": 0, "result": {"blob": "x" * 4096}}))

rate_limit = {
    "limitId": "codex",
    "primary": {
        "usedPercent": 9,
        "windowDurationMins": 10080,
        "resetsAt": 1785902973,
    },
    "rateLimitReachedType": None,
    "planType": "pro",
    "email": secret,
}
if mode == "invalid_percent":
    rate_limit["primary"]["usedPercent"] = 101

if mode == "fallback":
    rate_result = {"rateLimits": rate_limit}
elif mode == "missing_codex":
    other = {
        "limitId": "codex_other",
        "primary": {
            "usedPercent": 42,
            "windowDurationMins": 60,
            "resetsAt": 1785902973,
        },
    }
    rate_result = {
        "rateLimits": other,
        "rateLimitsByLimitId": {"codex_other": other},
    }
else:
    other = {
        "limitId": "codex_other",
        "primary": {
            "usedPercent": 42,
            "windowDurationMins": 60,
            "resetsAt": 1785902973,
        },
    }
    rate_result = {
        "rateLimits": other,
        "rateLimitsByLimitId": {
            "codex_other": other,
            "codex": rate_limit,
        },
    }

responses = [
    {"id": 1, "result": rate_result},
    {
        "id": 2,
        "result": {
            "summary": {"lifetimeTokens": 1234},
            "dailyUsageBuckets": [{"startDate": "2026-07-29", "tokens": 1234}],
        },
    },
]
for response in responses:
    print(json.dumps(response))
""",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return executable


def write_auth(tmp_path: Path, **values: object) -> Path:
    auth_json = tmp_path / "auth.json"
    auth_json.write_text(json.dumps(values), encoding="utf-8")
    auth_json.chmod(0o600)
    return auth_json


def probe(
    fake_codex: Path,
    auth_json: Path,
    *,
    timeout_seconds: float = 5,
    output_limit_bytes: int = 1_048_576,
) -> CodexUsageProbe:
    return CodexUsageProbe(
        codex_binary=fake_codex,
        auth_json=auth_json,
        proxy_url="http://127.0.0.1:7890",
        timeout_seconds=timeout_seconds,
        output_limit_bytes=output_limit_bytes,
    )


def test_probe_reads_normalized_subscription_usage_and_sends_exact_protocol(
    tmp_path: Path,
    fake_codex: Path,
) -> None:
    record_path = tmp_path / "record.json"
    auth_json = write_auth(tmp_path, record_path=str(record_path), secret="do-not-return")

    snapshot = probe(fake_codex, auth_json).read(now=NOW)

    assert snapshot == UsageSnapshot(
        limit_id="codex",
        used_percent=9,
        remaining_percent=91,
        window_duration_minutes=10_080,
        resets_at=datetime.fromtimestamp(RESET_TIMESTAMP, UTC),
        observed_at=NOW,
        rate_limit_reached_type=None,
        plan_type="pro",
        account_tokens=1234,
    )
    record = json.loads(record_path.read_text(encoding="utf-8"))
    assert record["requests"] == [
        {"method": "initialize", "id": 0, "params": {"clientInfo": CLIENT_INFO}},
        {"method": "initialized", "params": {}},
        {"method": "account/rateLimits/read", "id": 1},
        {"method": "account/usage/read", "id": 2},
    ]
    assert record["home_files"] == ["auth.json"]
    assert record["auth_mode"] == "0o600"
    assert record["proxy"] == "http://127.0.0.1:7890"
    assert not Path(record["codex_home"]).exists()


def test_probe_keeps_app_server_stdin_open_until_all_responses_arrive(tmp_path: Path, fake_codex: Path) -> None:
    snapshot = probe(fake_codex, write_auth(tmp_path, mode="require_open_stdin")).read(now=NOW)

    assert snapshot.used_percent == 9


def test_probe_prefers_the_named_codex_bucket_over_legacy_fallback(tmp_path: Path, fake_codex: Path) -> None:
    snapshot = probe(fake_codex, write_auth(tmp_path)).read(now=NOW)

    assert snapshot.limit_id == "codex"
    assert snapshot.used_percent == 9


def test_probe_accepts_the_backward_compatible_codex_bucket(tmp_path: Path, fake_codex: Path) -> None:
    snapshot = probe(fake_codex, write_auth(tmp_path, mode="fallback")).read(now=NOW)

    assert snapshot.limit_id == "codex"
    assert snapshot.used_percent == 9


def test_probe_rejects_a_response_without_the_codex_bucket(tmp_path: Path, fake_codex: Path) -> None:
    with pytest.raises(UsageProtocolError, match="Codex rate-limit bucket is unavailable"):
        probe(fake_codex, write_auth(tmp_path, mode="missing_codex")).read(now=NOW)


def test_probe_rejects_invalid_normalized_percentages(tmp_path: Path, fake_codex: Path) -> None:
    with pytest.raises(UsageProtocolError, match="Codex usage response is invalid"):
        probe(fake_codex, write_auth(tmp_path, mode="invalid_percent")).read(now=NOW)


def test_probe_timeout_is_a_safe_unavailable_result(tmp_path: Path, fake_codex: Path) -> None:
    with pytest.raises(UsageUnavailable, match="Codex usage probe timed out"):
        probe(fake_codex, write_auth(tmp_path, mode="hang"), timeout_seconds=0.05).read(now=NOW)


def test_probe_output_ceiling_is_enforced(tmp_path: Path, fake_codex: Path) -> None:
    with pytest.raises(UsageUnavailable, match="Codex usage probe exceeded its output limit"):
        probe(fake_codex, write_auth(tmp_path, mode="oversize"), output_limit_bytes=1024).read(now=NOW)


def test_probe_nonzero_exit_does_not_expose_server_output_or_auth(tmp_path: Path, fake_codex: Path) -> None:
    secret = "account-secret-value"

    with pytest.raises(UsageUnavailable, match="Codex usage probe failed") as captured:
        probe(fake_codex, write_auth(tmp_path, mode="nonzero", secret=secret)).read(now=NOW)

    assert secret not in str(captured.value)
    assert "private failure" not in str(captured.value)


def test_probe_rejects_malformed_json_without_returning_raw_output(tmp_path: Path, fake_codex: Path) -> None:
    with pytest.raises(UsageProtocolError, match="Codex usage response is malformed") as captured:
        probe(fake_codex, write_auth(tmp_path, mode="malformed")).read(now=NOW)

    assert "{not-json" not in str(captured.value)


def test_probe_requires_an_authorized_auth_file(tmp_path: Path, fake_codex: Path) -> None:
    with pytest.raises(UsageUnavailable, match="Codex authorization is unavailable"):
        probe(fake_codex, tmp_path / "missing-auth.json").read(now=NOW)


class _ModelsResponse:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, limit: int) -> bytes:
        return self._payload[:limit]


def test_api_key_probe_checks_models_without_running_inference(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def open_request(request: object, *, timeout: float) -> _ModelsResponse:
        captured["request"] = request
        captured["timeout"] = timeout
        return _ModelsResponse(b'{"data":[{"id":"gpt-5.6-sol"},{"id":"other"}]}')

    monkeypatch.setattr("powercontext_eval.web.usage.urlopen", open_request)
    snapshot = ApiKeyUsageProbe(
        api_key="private-api-key",
        base_url="http://100.88.99.11/v1",
        model="gpt-5.6-sol",
        timeout_seconds=3,
    ).read(now=NOW)

    request = cast(Any, captured["request"])
    assert request.full_url == "http://100.88.99.11/v1/models"
    assert request.get_header("Authorization") == "Bearer private-api-key"
    assert captured["timeout"] == 3
    assert snapshot.used_percent == 0
    assert snapshot.remaining_percent == 100
    assert snapshot.plan_type == "api-key"


def test_api_key_probe_fails_closed_when_model_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "powercontext_eval.web.usage.urlopen",
        lambda _request, *, timeout: _ModelsResponse(b'{"data":[{"id":"other"}]}'),
    )

    with pytest.raises(UsageUnavailable, match="model is unavailable"):
        ApiKeyUsageProbe(
            api_key="private-api-key",
            base_url="https://models.invalid/v1",
            model="gpt-5.6-sol",
        ).read(now=NOW)


@pytest.mark.parametrize("payload", [b"not-json", b"{}", b'{"data":{}}'])
def test_api_key_probe_rejects_invalid_models_responses(
    monkeypatch: pytest.MonkeyPatch,
    payload: bytes,
) -> None:
    monkeypatch.setattr(
        "powercontext_eval.web.usage.urlopen",
        lambda _request, *, timeout: _ModelsResponse(payload),
    )

    with pytest.raises(UsageProtocolError, match="model response is invalid"):
        ApiKeyUsageProbe(
            api_key="private-api-key",
            base_url="https://models.invalid/v1",
            model="gpt-5.6-sol",
        ).read(now=NOW)


@pytest.mark.parametrize(
    "overrides",
    [
        {"timeout_seconds": 0},
        {"timeout_seconds": True},
        {"output_limit_bytes": 0},
        {"output_limit_bytes": True},
    ],
)
def test_probe_rejects_unsafe_resource_bounds(tmp_path: Path, fake_codex: Path, overrides: dict[str, object]) -> None:
    auth_json = write_auth(tmp_path)
    unsafe_overrides = cast(Any, overrides)

    with pytest.raises((TypeError, ValueError)):
        CodexUsageProbe(
            codex_binary=fake_codex,
            auth_json=auth_json,
            proxy_url="http://127.0.0.1:7890",
            **unsafe_overrides,
        )


def test_usage_snapshot_requires_utc_timestamps() -> None:
    with pytest.raises(ValidationError):
        UsageSnapshot(
            limit_id="codex",
            used_percent=9,
            remaining_percent=91,
            window_duration_minutes=10_080,
            resets_at=NOW.replace(tzinfo=None),
            observed_at=NOW,
        )


def test_freshness_rejects_future_and_stale_snapshots(tmp_path: Path, fake_codex: Path) -> None:
    snapshot = probe(fake_codex, write_auth(tmp_path)).read(now=NOW)

    assert is_fresh(snapshot, now=NOW + timedelta(seconds=120), max_age=timedelta(seconds=120))
    assert not is_fresh(snapshot, now=NOW + timedelta(seconds=121), max_age=timedelta(seconds=120))
    assert not is_fresh(snapshot, now=NOW - timedelta(seconds=1), max_age=timedelta(seconds=120))


def test_freshness_rejects_negative_maximum_age(tmp_path: Path, fake_codex: Path) -> None:
    snapshot = probe(fake_codex, write_auth(tmp_path)).read(now=NOW)

    with pytest.raises(ValueError, match="max_age"):
        is_fresh(snapshot, now=NOW, max_age=timedelta(seconds=-1))
