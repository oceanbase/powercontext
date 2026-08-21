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

import os
import socket
import stat
import tempfile
from pathlib import Path

import pytest

from powercontext_eval.tokensflow import (
    DrainDeadline,
    TokensFlowInfrastructureError,
    UnsafeTokensFlowConfiguration,
    parse_tokensflow_version,
    snapshot_tokensflow_home,
    tokensflow_queue_caught_up,
    tokensflow_queue_negative_detected,
    tokensflow_runtime_environment,
    tokensflow_secret_variants,
)


def test_tokensflow_runtime_environment_allows_dynamic_config_but_rejects_credentials() -> None:
    source = {
        "TOKENSFLOW_API_URL": "https://current.invalid",
        "TOKENSFLOW_PROFILE": "current-profile",
        "TOKENSFLOW_ACCESS_TOKEN": "must-not-cross",
        "TOKENSFLOW_CLIENT_SECRET": "must-not-cross",
        "TOKENSFLOW_PASSWORD_FILE": "must-not-cross",
        "TOKENSFLOW_CREDENTIALS": "must-not-cross",
        "TOKENSFLOW_AUTH_URL": "must-not-cross",
        "TOKENSFLOW_SIGNING_KEY": "must-not-cross",
        "HTTP_PROXY": "http://host-proxy.invalid",
        "XDG_CONFIG_HOME": "/host/config",
    }

    assert tokensflow_runtime_environment(source) == {
        "TOKENSFLOW_API_URL": "https://current.invalid",
        "TOKENSFLOW_PROFILE": "current-profile",
    }


def test_parse_tokensflow_version_accepts_real_single_line_metadata_shape() -> None:
    metadata_suffix = b"(build:0123456789abcdef01234)"
    assert len(metadata_suffix) == 29

    assert parse_tokensflow_version(b"TokensFlow 1.0.16 " + metadata_suffix + b"\n") == "1.0.16"
    assert parse_tokensflow_version(b"tokensflow 1.0.16\n") == "1.0.16"


@pytest.mark.parametrize(
    "raw",
    [
        b"prefix TokensFlow 1.0.16",
        b"TokensFlow 1.0.16 suffix",
        b"TokensFlow 1.0.16 (valid) trailing",
        b"TokensFlow 1.0.16 (one) (two)",
        b"TokensFlow 1.0.16 (metadata;command)",
        b"TokensFlow 1.0.16 (metadata\x1b)",
        b"TokensFlow 1.0.16 (" + b"a" * 65 + b")",
        b"TokensFlow 1.0.16\nmalicious second line",
        b"TokensFlow 1.0.16\rmetadata second line",
    ],
)
def test_parse_tokensflow_version_rejects_multiline_control_or_surrounding_garbage(raw: bytes) -> None:
    with pytest.raises(TokensFlowInfrastructureError, match="^TokensFlow version check failed$"):
        parse_tokensflow_version(raw)


def test_drain_deadline_uses_one_fixed_budget_across_steps() -> None:
    observed = iter((10.0, 22.5, 69.5, 70.0))
    deadline = DrainDeadline(clock=lambda: next(observed))

    assert deadline.remaining() == 47.5
    assert deadline.remaining() == 0.5
    with pytest.raises(TokensFlowInfrastructureError, match="^TokensFlow drain timed out$") as captured:
        deadline.remaining()

    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None


def test_drain_deadline_rejects_nonpositive_timeout_without_calling_clock() -> None:
    with pytest.raises(TokensFlowInfrastructureError, match="^TokensFlow drain timed out$"):
        DrainDeadline(timeout_seconds=0, clock=lambda: pytest.fail("clock must not be called"))


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (b"[PASS] queue: caught up (0 pending files)\n", True),
        (b"header\r\n[PASS] queue: caught up (0 pending files)\r\nfooter\r\n", True),
        (b"[WARN] queue: caught up (0 pending files)\n", False),
        (b"[PASS] queue: caught up (2 pending files)\n", False),
        (b"queue: caught up (0 pending files)\n", False),
        (b"[PASS] queue: caught up (0 pending files) extra\n", False),
        (b"[PASS] recent uploads: caught up (0 pending files)\n", False),
    ],
)
def test_tokensflow_queue_requires_one_exact_complete_pass_line(status: bytes, expected: bool) -> None:
    assert tokensflow_queue_caught_up(status) is expected


@pytest.mark.parametrize(
    "negative",
    [
        b"queue: pending files: 1",
        b"accounting queue: 1 pending file",
        b"queue: rejected batches: 2",
        b"accounting: record rejected",
        b"queue: failed checks: 1",
        b"[FAIL] queue inspection",
        b"queue: blocked ingest batches: 1",
        b"accounting queue: collector circuit: open failures=1",
        b"queue circuit-open",
    ],
)
def test_tokensflow_queue_rejects_explicit_negative_state_even_with_caught_up_marker(negative: bytes) -> None:
    status = b"caught up (0 pending files)\n" + negative + b"\n"

    assert tokensflow_queue_caught_up(status) is False
    assert tokensflow_queue_negative_detected(status) is True


def test_tokensflow_queue_fails_closed_without_exact_caught_up_marker() -> None:
    assert tokensflow_queue_caught_up(b"queue idle; pending files: 0\n") is False
    assert tokensflow_queue_negative_detected(b"queue idle; pending files: 0\n") is False


def test_tokensflow_queue_ignores_nonqueue_failures_but_rejects_queue_scoped_failures() -> None:
    real_shape = (
        b"[FAIL] daemon: not running after graceful TERM\n"
        b"[PASS] queue: caught up (0 pending files)\n"
        b"[FAIL] runtime: service manager unavailable\n"
        b"[FAIL] auth: optional identity refresh failed\n"
        b"[FAIL] config: optional update metadata failed\n"
    )
    assert tokensflow_queue_caught_up(real_shape) is True
    assert tokensflow_queue_negative_detected(real_shape) is False

    queue_failure = real_shape + b"[FAIL] queue: accounting verification failed\n"
    assert tokensflow_queue_caught_up(queue_failure) is False
    assert tokensflow_queue_negative_detected(queue_failure) is True


def _profile(tmp_path: Path, credentials: str = '{"access":"first"}') -> Path:
    user_home = tmp_path / "profile"
    config = user_home / ".tokensflow"
    config.mkdir(parents=True, mode=0o700)
    (config / "credentials.json").write_text(credentials)
    return user_home


def test_snapshot_tokensflow_home_is_private_and_content_current(tmp_path: Path) -> None:
    source_home = _profile(tmp_path)
    config = source_home / ".tokensflow"
    (config / "config.toml").write_text('endpoint = "current"\n')
    nested = config / "profiles" / "active"
    nested.mkdir(parents=True)
    (nested / "settings.json").write_text('{"mode":"live"}')
    destination = tmp_path / "arm/runtime/tokensflow-home"

    snapshot = snapshot_tokensflow_home(source_home, destination)

    assert snapshot.user_home == destination
    assert snapshot.credentials == destination / ".tokensflow/credentials.json"
    assert snapshot.credentials.read_text() == '{"access":"first"}'
    assert (destination / ".tokensflow/config.toml").read_text() == 'endpoint = "current"\n'
    assert (destination / ".tokensflow/profiles/active/settings.json").read_text() == '{"mode":"live"}'
    assert (destination / ".local/share/tokensflow").is_dir()
    for directory in (
        destination,
        destination / ".tokensflow",
        destination / ".tokensflow/profiles",
        destination / ".tokensflow/profiles/active",
        destination / ".local",
        destination / ".local/share",
        destination / ".local/share/tokensflow",
    ):
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    for file_path in (
        snapshot.credentials,
        destination / ".tokensflow/config.toml",
        destination / ".tokensflow/profiles/active/settings.json",
    ):
        assert stat.S_IMODE(file_path.stat().st_mode) == 0o600


def test_snapshot_uses_current_content_for_each_new_arm(tmp_path: Path) -> None:
    source_home = _profile(tmp_path)

    first = snapshot_tokensflow_home(source_home, tmp_path / "first")
    (source_home / ".tokensflow/credentials.json").write_text('{"access":"second"}')
    second = snapshot_tokensflow_home(source_home, tmp_path / "second")

    assert first.credentials.read_text() == '{"access":"first"}'
    assert second.credentials.read_text() == '{"access":"second"}'


def test_snapshot_allows_missing_optional_config_toml(tmp_path: Path) -> None:
    destination = tmp_path / "destination"

    snapshot_tokensflow_home(_profile(tmp_path), destination)

    assert not (destination / ".tokensflow/config.toml").exists()


@pytest.mark.parametrize("missing", ["profile", "config", "credentials"])
def test_snapshot_rejects_missing_required_source(tmp_path: Path, missing: str) -> None:
    source_home = tmp_path / "profile"
    if missing in {"config", "credentials"}:
        source_home.mkdir()
    if missing == "credentials":
        (source_home / ".tokensflow").mkdir()

    with pytest.raises(UnsafeTokensFlowConfiguration, match="safe TokensFlow profile"):
        snapshot_tokensflow_home(source_home, tmp_path / "destination")


@pytest.mark.parametrize("linked_component", ["home", "config", "file"])
def test_snapshot_rejects_symlinks(tmp_path: Path, linked_component: str) -> None:
    real_home = _profile(tmp_path)
    source_home = real_home
    if linked_component == "home":
        source_home = tmp_path / "linked-home"
        source_home.symlink_to(real_home, target_is_directory=True)
    elif linked_component == "config":
        source_home = tmp_path / "wrapper"
        source_home.mkdir()
        (source_home / ".tokensflow").symlink_to(real_home / ".tokensflow", target_is_directory=True)
    else:
        target = tmp_path / "external.json"
        target.write_text("{}")
        credentials = real_home / ".tokensflow/credentials.json"
        credentials.unlink()
        credentials.symlink_to(target)

    with pytest.raises(UnsafeTokensFlowConfiguration, match="safe TokensFlow profile"):
        snapshot_tokensflow_home(source_home, tmp_path / "destination")


@pytest.mark.parametrize("entry_kind", ["fifo", "socket"])
def test_snapshot_rejects_special_entries(tmp_path: Path, entry_kind: str) -> None:
    with tempfile.TemporaryDirectory(prefix="tokensflow-test-", dir="/tmp") as temporary:
        short_root = Path(temporary)
        source_home = _profile(short_root)
        special = source_home / ".tokensflow" / entry_kind
        listener: socket.socket | None = None
        if entry_kind == "fifo":
            os.mkfifo(special)
        else:
            listener = socket.socket(socket.AF_UNIX)
            listener.bind(os.fspath(special))
        try:
            with pytest.raises(UnsafeTokensFlowConfiguration, match="safe TokensFlow profile"):
                snapshot_tokensflow_home(source_home, short_root / "destination")
        finally:
            if listener is not None:
                listener.close()


def test_snapshot_rejects_lexical_path_escape(tmp_path: Path) -> None:
    source_home = _profile(tmp_path)
    escaped_destination = tmp_path / "arm" / ".." / "escaped"

    with pytest.raises(UnsafeTokensFlowConfiguration, match="safe TokensFlow profile"):
        snapshot_tokensflow_home(source_home, escaped_destination)


def test_snapshot_rejects_preexisting_destination(tmp_path: Path) -> None:
    destination = tmp_path / "destination"
    destination.mkdir()

    with pytest.raises(UnsafeTokensFlowConfiguration, match="safe TokensFlow profile"):
        snapshot_tokensflow_home(_profile(tmp_path), destination)


def test_tokensflow_secret_variants_only_expand_sensitive_string_fields(tmp_path: Path) -> None:
    source_home = _profile(
        tmp_path,
        (
            '{"access_token":"long-access-secret","enabled":true,"expires_in":3600,'
            '"token_type":"Bearer","nested":{"refresh_token":"secret/value"}}'
        ),
    )

    variants = tokensflow_secret_variants(source_home / ".tokensflow/credentials.json")

    assert "long-access-secret" in variants
    assert "secret/value" in variants
    assert "secret%2Fvalue" in variants
    assert "true" not in variants
    assert "3600" not in variants
    assert "Bearer" not in variants
