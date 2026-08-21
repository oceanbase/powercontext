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


class AccountUsage(_FrozenModel):
    """Public account admission state without pretending API-key billing is subscription usage."""

    mode: Literal["subscription", "api_key"]
    sufficient: bool
    usage: UsageSnapshot | None

    @model_validator(mode="after")
    def require_mode_consistency(self) -> Self:
        if self.mode == "api_key":
            if not self.sufficient or self.usage is not None:
                raise ValueError("API-key usage must always be sufficient and contain no subscription snapshot")
        elif self.usage is None:
            raise ValueError("Subscription usage requires a current snapshot")
        return self


class CodexUsageProbe:
    """Read account usage through a short-lived local Codex App Server."""

    def __init__(
        self,
        *,
        codex_binary: Path,
        auth_json: Path,
        proxy_url: str | None = None,
        codex_config: Path | None = None,
        timeout_seconds: float = 15,
        output_limit_bytes: int = _DEFAULT_OUTPUT_LIMIT_BYTES,
    ) -> None:
        if not isinstance(codex_binary, Path) or not codex_binary.is_absolute():
            raise ValueError("codex_binary must be an absolute Path")
        if not isinstance(auth_json, Path) or not auth_json.is_absolute():
            raise ValueError("auth_json must be an absolute Path")
        if codex_config is not None and (not isinstance(codex_config, Path) or not codex_config.is_absolute()):
            raise ValueError("codex_config must be an absolute Path")
        if proxy_url is not None and (not isinstance(proxy_url, str) or not proxy_url or "\0" in proxy_url):
            raise ValueError("proxy_url must be a non-empty string or None")
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
        self._codex_config = codex_config
        self._proxy_url = proxy_url
        self._timeout_seconds = float(timeout_seconds)
        self._output_limit_bytes = output_limit_bytes

    def read(self, *, now: datetime) -> UsageSnapshot:
        """Collect and normalize one snapshot without retaining raw account output."""

        _require_utc(now, name="now")
        if not self._auth_json.is_file():
            raise UsageUnavailable("Codex authorization is unavailable")
        if self._codex_config is not None and (not self._codex_config.is_file() or self._codex_config.is_symlink()):
            raise UsageUnavailable("Codex provider configuration is unavailable")

        try:
            with tempfile.TemporaryDirectory(prefix="powercontext-eval-codex-") as temporary:
                codex_home = Path(temporary)
                auth_copy = codex_home / "auth.json"
                shutil.copyfile(self._auth_json, auth_copy)
                auth_copy.chmod(0o600)
                if self._codex_config is not None:
                    config_copy = codex_home / "config.toml"
                    shutil.copyfile(self._codex_config, config_copy)
                    config_copy.chmod(0o600)
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
        request_bytes = b"".join(
            json.dumps(request, separators=(",", ":")).encode("utf-8") + b"\n" for request in requests
        )
        proxy_environment = {
            "CODEX_HOME": os.fspath(codex_home),
            "HOME": os.fspath(codex_home),
        }
        if self._proxy_url is not None:
            proxy_environment.update(
                {
                    "HTTP_PROXY": self._proxy_url,
                    "HTTPS_PROXY": self._proxy_url,
                    "ALL_PROXY": self._proxy_url,
                    "http_proxy": self._proxy_url,
                    "https_proxy": self._proxy_url,
                    "all_proxy": self._proxy_url,
                }
            )
        else:
            proxy_environment.update(
                {
                    "ALL_PROXY": "",
                    "HTTP_PROXY": "",
                    "HTTPS_PROXY": "",
                    "all_proxy": "",
                    "http_proxy": "",
                    "https_proxy": "",
                }
            )

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
            process.stdin.write(request_bytes)
            process.stdin.flush()
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
                if _response_ids(output) >= {0, 1, 2}:
                    return bytes(output)
        finally:
            _stop_probe_process(process)


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
