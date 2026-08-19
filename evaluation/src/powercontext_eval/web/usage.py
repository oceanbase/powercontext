"""Bounded, secret-safe Codex subscription usage collection."""

from __future__ import annotations

import json
import math
import os
import select
import shutil
import signal
import subprocess
import tempfile
import time
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, Literal, Self
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from powercontext_eval.process import build_process_environment

CLIENT_INFO = {
    "name": "powercontext_eval",
    "title": "PowerContext Evaluation",
    "version": "0.1.0",
}
_DEFAULT_OUTPUT_LIMIT_BYTES = 1_048_576


class UsageUnavailable(RuntimeError):
    """The Worker could not obtain a trustworthy current usage snapshot."""


class UsageProtocolError(UsageUnavailable):
    """The App Server response did not satisfy the expected public protocol."""


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class UsageSnapshot(_FrozenModel):
    """A normalized account-wide Codex subscription usage observation."""

    limit_id: Literal["codex"]
    used_percent: Annotated[int, Field(ge=0, le=100)]
    remaining_percent: Annotated[int, Field(ge=0, le=100)]
    window_duration_minutes: Annotated[int, Field(ge=1)]
    resets_at: datetime
    observed_at: datetime
    rate_limit_reached_type: str | None = None
    plan_type: str | None = None
    account_tokens: Annotated[int, Field(ge=0)] | None = None
    probe_version: Literal[1] = 1

    @field_validator("resets_at", "observed_at")
    @classmethod
    def require_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
            raise ValueError("Usage timestamps must use UTC")
        return value

    @model_validator(mode="after")
    def require_complementary_percentages(self) -> Self:
        if self.used_percent + self.remaining_percent != 100:
            raise ValueError("Used and remaining percentages must add to 100")
        return self


class CodexUsageProbe:
    """Read account usage through a short-lived local Codex App Server."""

    def __init__(
        self,
        *,
        codex_binary: Path,
        auth_json: Path,
        proxy_url: str,
        timeout_seconds: float = 15,
        output_limit_bytes: int = _DEFAULT_OUTPUT_LIMIT_BYTES,
    ) -> None:
        if not isinstance(codex_binary, Path) or not codex_binary.is_absolute():
            raise ValueError("codex_binary must be an absolute Path")
        if not isinstance(auth_json, Path) or not auth_json.is_absolute():
            raise ValueError("auth_json must be an absolute Path")
        if not isinstance(proxy_url, str) or not proxy_url or "\0" in proxy_url:
            raise ValueError("proxy_url must be a non-empty string")
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be finite and greater than zero")
        if isinstance(output_limit_bytes, bool) or not isinstance(output_limit_bytes, int) or output_limit_bytes <= 0:
            raise ValueError("output_limit_bytes must be a positive integer")

        self._codex_binary = codex_binary
        self._auth_json = auth_json
        self._proxy_url = proxy_url
        self._timeout_seconds = float(timeout_seconds)
        self._output_limit_bytes = output_limit_bytes

    def read(self, *, now: datetime) -> UsageSnapshot:
        """Collect and normalize one snapshot without retaining raw account output."""

        _require_utc(now, name="now")
        if not self._auth_json.is_file():
            raise UsageUnavailable("Codex authorization is unavailable")

        try:
            with tempfile.TemporaryDirectory(prefix="powercontext-eval-codex-") as temporary:
                codex_home = Path(temporary)
                auth_copy = codex_home / "auth.json"
                shutil.copyfile(self._auth_json, auth_copy)
                auth_copy.chmod(0o600)
                output = self._run(codex_home)
        except OSError:
            raise UsageUnavailable("Codex usage probe failed") from None

        return _parse_snapshot(output, observed_at=now)

    def _run(self, codex_home: Path) -> bytes:
        requests = (
            {"method": "initialize", "id": 0, "params": {"clientInfo": CLIENT_INFO}},
            {"method": "initialized", "params": {}},
            {"method": "account/rateLimits/read", "id": 1},
            {"method": "account/usage/read", "id": 2},
        )
        request_bytes = tuple(
            json.dumps(request, separators=(",", ":")).encode("utf-8") + b"\n" for request in requests
        )
        proxy_environment = {
            "CODEX_HOME": os.fspath(codex_home),
            "HOME": os.fspath(codex_home),
            "HTTP_PROXY": self._proxy_url,
            "HTTPS_PROXY": self._proxy_url,
            "ALL_PROXY": self._proxy_url,
            "http_proxy": self._proxy_url,
            "https_proxy": self._proxy_url,
            "all_proxy": self._proxy_url,
        }

        process = subprocess.Popen(
            (os.fspath(self._codex_binary), "app-server", "--listen", "stdio://"),
            cwd=codex_home,
            env=build_process_environment(proxy_environment),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            start_new_session=os.name == "posix",
            shell=False,
        )
        output = bytearray()
        deadline = time.monotonic() + self._timeout_seconds
        try:
            assert process.stdin is not None
            assert process.stdout is not None
            process.stdin.write(request_bytes[0])
            process.stdin.flush()
            followups_sent = False
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise UsageUnavailable("Codex usage probe timed out")
                readable, _, _ = select.select((process.stdout,), (), (), remaining)
                if not readable:
                    raise UsageUnavailable("Codex usage probe timed out")
                chunk = os.read(process.stdout.fileno(), min(65_536, self._output_limit_bytes + 1))
                if not chunk:
                    returncode = process.poll()
                    if returncode is None:
                        continue
                    if returncode != 0:
                        raise UsageUnavailable("Codex usage probe failed")
                    return bytes(output)
                output.extend(chunk)
                if len(output) > self._output_limit_bytes:
                    raise UsageUnavailable("Codex usage probe exceeded its output limit")
                response_ids = _response_ids(output)
                if 0 in response_ids and not followups_sent:
                    process.stdin.write(b"".join(request_bytes[1:]))
                    process.stdin.flush()
                    followups_sent = True
                if response_ids >= {0, 1, 2}:
                    return bytes(output)
        finally:
            _stop_probe_process(process)


class ApiKeyUsageProbe:
    """Verify API-key connectivity and model availability without running inference."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout_seconds: float = 15,
        output_limit_bytes: int = _DEFAULT_OUTPUT_LIMIT_BYTES,
    ) -> None:
        from powercontext_eval.codex import is_safe_codex_model, is_safe_openai_base_url

        if not isinstance(api_key, str) or not api_key or any(character in api_key for character in "\0\r\n"):
            raise ValueError("api_key must be a safe non-empty string")
        if not is_safe_openai_base_url(base_url):
            raise ValueError("base_url must be a safe HTTP(S) URL")
        if not is_safe_codex_model(model):
            raise ValueError("model is unsafe")
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be finite and greater than zero")
        if isinstance(output_limit_bytes, bool) or not isinstance(output_limit_bytes, int) or output_limit_bytes <= 0:
            raise ValueError("output_limit_bytes must be a positive integer")

        self._api_key = api_key
        self._models_url = f"{base_url.rstrip('/')}/models"
        self._model = model
        self._timeout_seconds = float(timeout_seconds)
        self._output_limit_bytes = output_limit_bytes

    def read(self, *, now: datetime) -> UsageSnapshot:
        """Return a synthetic healthy snapshot after a bounded models-list request."""

        _require_utc(now, name="now")
        request = Request(
            self._models_url,
            headers={"Authorization": f"Bearer {self._api_key}", "Accept": "application/json"},
            method="GET",
        )
        try:
            with urlopen(request, timeout=self._timeout_seconds) as response:
                raw = response.read(self._output_limit_bytes + 1)
        except (HTTPError, URLError, OSError, TimeoutError):
            raise UsageUnavailable("API-key model probe failed") from None
        if len(raw) > self._output_limit_bytes:
            raise UsageUnavailable("API-key model probe exceeded its output limit")
        try:
            payload = json.loads(raw)
            models = payload["data"]
            if not isinstance(models, list):
                raise TypeError
            model_ids = {item["id"] for item in models if isinstance(item, dict) and isinstance(item.get("id"), str)}
        except (json.JSONDecodeError, KeyError, TypeError, UnicodeDecodeError):
            raise UsageProtocolError("API-key model response is invalid") from None
        if self._model not in model_ids:
            raise UsageUnavailable("Configured Codex model is unavailable through the API key")
        return UsageSnapshot(
            limit_id="codex",
            used_percent=0,
            remaining_percent=100,
            window_duration_minutes=1,
            resets_at=now + timedelta(minutes=1),
            observed_at=now,
            plan_type="api-key",
        )


def _response_ids(raw: bytearray) -> set[int]:
    response_ids: set[int] = set()
    for line in bytes(raw).splitlines():
        try:
            message = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if not isinstance(message, dict):
            continue
        response_id = message.get("id")
        if isinstance(response_id, int) and not isinstance(response_id, bool):
            response_ids.add(response_id)
    return response_ids


def _stop_probe_process(process: subprocess.Popen[bytes]) -> None:
    if process.stdin is not None and not process.stdin.closed:
        process.stdin.close()
    if process.stdout is not None and not process.stdout.closed:
        process.stdout.close()
    if process.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGTERM)
        else:  # pragma: no cover - the evaluation host is POSIX
            process.terminate()
        process.wait(timeout=1)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:  # pragma: no cover - the evaluation host is POSIX
                process.kill()
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            pass


def is_fresh(snapshot: UsageSnapshot, *, now: datetime, max_age: timedelta) -> bool:
    """Return whether a snapshot is current enough to authorize a new task."""

    _require_utc(now, name="now")
    if not isinstance(max_age, timedelta) or max_age < timedelta(0):
        raise ValueError("max_age must be a non-negative timedelta")
    age = now - snapshot.observed_at
    return timedelta(0) <= age <= max_age


def _parse_snapshot(raw: bytes, *, observed_at: datetime) -> UsageSnapshot:
    responses = _parse_responses(raw)
    rate_result = _required_result(responses, 1)
    usage_result = _required_result(responses, 2)
    _required_result(responses, 0)

    bucket = _select_codex_bucket(rate_result)
    primary = _required_object(bucket, "primary")
    try:
        used_percent = _strict_int(primary.get("usedPercent"), minimum=0, maximum=100)
        duration = _strict_int(primary.get("windowDurationMins"), minimum=1)
        reset_timestamp = _strict_int(primary.get("resetsAt"), minimum=0)
        resets_at = datetime.fromtimestamp(reset_timestamp, UTC)
        reached_type = _optional_string(bucket.get("rateLimitReachedType"))
        plan_type = _optional_string(bucket.get("planType"))
        account_tokens = _account_tokens(usage_result)
        return UsageSnapshot(
            limit_id="codex",
            used_percent=used_percent,
            remaining_percent=100 - used_percent,
            window_duration_minutes=duration,
            resets_at=resets_at,
            observed_at=observed_at,
            rate_limit_reached_type=reached_type,
            plan_type=plan_type,
            account_tokens=account_tokens,
        )
    except (KeyError, TypeError, ValueError, OSError, OverflowError, ValidationError):
        raise UsageProtocolError("Codex usage response is invalid") from None


def _parse_responses(raw: bytes) -> dict[int, Mapping[str, Any]]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise UsageProtocolError("Codex usage response is malformed") from None

    responses: dict[int, Mapping[str, Any]] = {}
    try:
        for line in text.splitlines():
            if not line:
                raise ValueError
            message = json.loads(line)
            if not isinstance(message, dict):
                raise TypeError
            response_id = message.get("id")
            if response_id is None:
                continue
            if isinstance(response_id, bool) or not isinstance(response_id, int) or response_id in responses:
                raise ValueError
            responses[response_id] = message
    except (json.JSONDecodeError, TypeError, ValueError):
        raise UsageProtocolError("Codex usage response is malformed") from None
    if not responses:
        raise UsageProtocolError("Codex usage response is malformed")
    return responses


def _required_result(responses: Mapping[int, Mapping[str, Any]], response_id: int) -> Mapping[str, Any]:
    response = responses.get(response_id)
    if response is None or "error" in response:
        raise UsageProtocolError("Codex usage request did not complete")
    result = response.get("result")
    if not isinstance(result, dict):
        raise UsageProtocolError("Codex usage response is malformed")
    return result


def _select_codex_bucket(rate_result: Mapping[str, Any]) -> Mapping[str, Any]:
    by_id = rate_result.get("rateLimitsByLimitId")
    if by_id is not None:
        if not isinstance(by_id, dict):
            raise UsageProtocolError("Codex usage response is invalid")
        bucket = by_id.get("codex")
        if bucket is not None:
            if not isinstance(bucket, dict) or bucket.get("limitId") != "codex":
                raise UsageProtocolError("Codex usage response is invalid")
            return bucket

    fallback = rate_result.get("rateLimits")
    if isinstance(fallback, dict) and fallback.get("limitId") == "codex":
        return fallback
    raise UsageProtocolError("Codex rate-limit bucket is unavailable")


def _required_object(mapping: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = mapping[key]
    if not isinstance(value, dict):
        raise TypeError
    return value


def _strict_int(value: Any, *, minimum: int, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError
    if value < minimum or (maximum is not None and value > maximum):
        raise ValueError
    return value


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError
    return value


def _account_tokens(usage_result: Mapping[str, Any]) -> int | None:
    summary = usage_result.get("summary")
    if summary is None:
        return None
    if not isinstance(summary, dict):
        raise TypeError
    lifetime_tokens = summary.get("lifetimeTokens")
    if lifetime_tokens is None:
        return None
    return _strict_int(lifetime_tokens, minimum=0)


def _require_utc(value: datetime, *, name: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise ValueError(f"{name} must use UTC")
