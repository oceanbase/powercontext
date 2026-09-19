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

"""Verify independent Server availability after forced installed-app termination."""

from __future__ import annotations

import subprocess

import httpx
from installed_fixture import isolated_server
from installed_workflow import InstalledPage
from real_server import HarnessFailure


def exercise_forced_exit(client: httpx.Client, prefix: str, app: subprocess.Popen[bytes]) -> dict[str, object]:
    page = InstalledPage(client, prefix)
    note = "desktoplifecyclectest 异常关闭前保存的记忆"
    with isolated_server() as (server, scope, wheel_digest):
        page.connect("Desktop CI lifecycle", str(server.base_url).rstrip("/"))
        page.select_scope(scope)
        page.type("记忆内容", note, "textarea")
        page.button("保存记忆")
        # The preceding workflow intentionally leaves an unknown last write. A new
        # explicit write must still pass the product's duplicate-risk confirmation.
        alert = client.get(prefix + "/alert/text")
        alert.raise_for_status()
        expected_prompt = "上次提交结果未知，再次保存可能产生重复记录。仍要提交这次输入吗？"  # noqa: RUF001 - exact localized UI
        if alert.json()["value"] != expected_prompt:
            raise HarnessFailure("installed_unknown_retry_confirmation_missing")
        page.post("/alert/accept", {})
        page.wait_text("保存成功。")
        citation = page.search_read(note, "desktoplifecyclectest")
        if app.poll() is not None:
            raise HarnessFailure("installed_app_exited_before_forced_exit")
        # Kill only the exact application process launched by this harness.
        app.kill()
        app.wait(timeout=15)
        server.get("/health/ready").raise_for_status()
        original = server.post("/v1/memory/entries/get", json={"scope_id": scope, "citation": citation})
        original.raise_for_status()
        if original.json()["text"] != note or original.json()["citation"] != citation:
            raise HarnessFailure("installed_forced_exit_changed_saved_memory")
        after_text = "Independent write after Desktop forced exit"
        written = server.post("/v1/memory/remember", json={"scope_id": scope, "kind": "note", "text": after_text})
        written.raise_for_status()
        after_citation = written.json()["entry"]["citation"]
        read_back = server.post("/v1/memory/entries/get", json={"scope_id": scope, "citation": after_citation})
        read_back.raise_for_status()
        if read_back.json()["text"] != after_text or read_back.json()["citation"] != after_citation:
            raise HarnessFailure("installed_forced_exit_independent_write_unreadable")
        return {
            "serverWheelSha256": wheel_digest,
            "applicationExitCode": app.returncode,
            "explicitSaveAfterUnknownConfirmed": True,
            "forcedOwnedApplicationExit": True,
            "serverReadyAfterExit": True,
            "originalExactReadAfterExit": True,
            "independentWriteAndExactReadAfterExit": True,
        }
