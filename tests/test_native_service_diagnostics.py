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

import json
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

from powercontext.service.adapters.launchd import LaunchdUserAdapter
from powercontext.service.model import ServiceError, SupportState
from tests.native import test_personal_service_lifecycle as lifecycle


def test_native_startup_failure_preserves_diagnostics_after_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = LaunchdUserAdapter(home=tmp_path, identifier="com.oceanbase.powercontext.native.fixture")
    monkeypatch.setattr(adapter, "support", lambda: (SupportState.SUPPORTED, "fixture"))
    monkeypatch.setattr(lifecycle, "_native_adapter", lambda: adapter)
    monkeypatch.setattr(lifecycle, "_environment_file", lambda path: path / "fixture.env")
    controller = Mock()
    controller.install.side_effect = ServiceError("service did not become live")
    monkeypatch.setattr(lifecycle, "ServiceController", lambda adapter: controller)
    logs = tmp_path / "data" / "logs"
    logs.mkdir(parents=True)
    retry_state = logs / "launchd-retry-state.json"
    retry_state.write_text('{"attempts":[1]}', encoding="utf-8")
    token = logs / "launchd-retry.enabled"
    token.write_text("enabled", encoding="utf-8")
    monkeypatch.setattr(
        lifecycle.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, "state = running\npid = 123", ""),
    )

    def cleanup(adapter: LaunchdUserAdapter) -> None:
        retry_state.unlink()
        token.unlink()

    monkeypatch.setattr(lifecycle, "_cleanup", cleanup)
    with pytest.raises(pytest.fail.Exception, match="service did not become live"):
        lifecycle.test_native_personal_service_lifecycle(tmp_path)

    assert not retry_state.exists() and not token.exists()
    snapshot = json.loads((logs / "launchd-failure-snapshot.json").read_text(encoding="utf-8"))
    assert snapshot == {"retry_token_present": True, "retry_state": '{"attempts":[1]}'}
    assert "state = running\npid = 123" in (logs / "launchd-before-cleanup.log").read_text(encoding="utf-8")
