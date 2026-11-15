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

"""Observe explicit connection and exact-Scope choices through the installed UI."""

from __future__ import annotations

import tempfile
from pathlib import Path

import httpx
from installed_fixture import exact_memory_text, fixture_compatibility_profile, isolated_server
from installed_workflow import ELEMENT, InstalledPage
from real_server import HarnessFailure


def exercise_connection_journey(client: httpx.Client, prefix: str) -> dict[str, object]:
    page = InstalledPage(client, prefix)
    name = "Desktop CI connection journey"
    title = "Desktop installed CI"
    keyword = "desktopconnectionjourney"
    note = f"{keyword} 仅写入明确选择的第二个同名范围"
    with tempfile.TemporaryDirectory(prefix="desktop-connection-journey-") as directory:
        requests = Path(directory) / "readiness-requests"
        with isolated_server(readiness_requests=requests) as (server, first_scope, wheel_digest):
            created = server.post(
                "/v1/scopes",
                json={"title": title, "summary": "Exact Scope selection", "idempotency_key": "connection-second-scope"},
            )
            created.raise_for_status()
            second_scope = created.json()["scope_id"]
            if first_scope == second_scope:
                raise HarnessFailure("installed_duplicate_scope_fixture_invalid")
            before = len(requests.read_text(encoding="utf-8").splitlines())
            page.button("连接")
            page.type("连接名称", name)
            page.type("Server 地址", str(server.base_url).rstrip("/"))
            qualification = fixture_compatibility_profile()
            page.click(f"//label[normalize-space(text())='已验证兼容配置']/select//option[@value='{qualification}']")
            page.button("保存配置")
            page.wait_text("配置已保存，尚未执行新的连接验证。")  # noqa: RUF001 - exact localized UI
            expect_disconnected(page)
            page.button("检查连接")
            page.wait_text("连接检查已完成，请核对下方各项结果。")  # noqa: RUF001 - exact localized UI
            expect_disconnected(page)
            page.activate(name)
            page.type("按范围标题查找", title)
            page.button("查找范围")
            for scope_id in (first_scope, second_scope):
                page.element(f"//ul[@class='scope-list']/li[code[normalize-space(.)='{scope_id}']]/button")
            page.click(f"//ul[@class='scope-list']/li[code[normalize-space(.)='{second_scope}']]/button")
            page.wait("return !document.querySelector('[role=dialog]');")
            page.wait(
                "return document.querySelector('.scope-trigger code')?.textContent === arguments[0];", [second_scope]
            )
            page.type("记忆内容", note, "textarea")
            page.button("保存")
            page.wait_text("保存成功。")
            reference = page.search_read(note, keyword)
            if exact_memory_text(server, second_scope, reference) != note:
                raise HarnessFailure("installed_exact_scope_write_unreadable")
            other = server.post(
                "/v1/memory/search",
                json={"scope_id": first_scope, "query": keyword, "mode": "fts", "limit": 10},
            )
            other.raise_for_status()
            if other.json()["hits"]:
                raise HarnessFailure("installed_same_title_scope_received_write")
            page.select_scope(first_scope)
            page.expect_empty_context()
            page.open_connection_menu()
            page.click(f"//button[@role='menuitem'][span[normalize-space(.)='{name} · 当前连接']]")
            if page.observe("return arguments[0].value;", [{ELEMENT: page.field("连接名称")}]) != name:
                raise HarnessFailure("installed_connection_menu_opened_wrong_profile")
            page.wait(
                "return document.querySelector('.scope-trigger code')?.textContent === arguments[0];", [first_scope]
            )
            ordinary = len(requests.read_text(encoding="utf-8").splitlines())
            if ordinary != before:
                raise HarnessFailure("installed_ordinary_connection_journey_probed_readiness")
            page.button("探测就绪状态")
            page.wait_text("就绪探测时间")
            after_probe = len(requests.read_text(encoding="utf-8").splitlines())
            if after_probe != before + 1:
                raise HarnessFailure("installed_explicit_readiness_probe_count")
            page.button("记忆")
            return {
                "serverWheelSha256": wheel_digest,
                "saveAndPassiveCheckRemainDisconnected": True,
                "explicitActivationOpensScopeSelection": True,
                "duplicateTitleScopesSelectedByExactId": True,
                "writeStoredOnlyInSelectedScope": True,
                "scopeSwitchClearsPrivateResults": True,
                "globalMenuOpensExactProfileWithoutChangingScope": True,
                "readinessRequests": {
                    "beforeJourney": before,
                    "afterOrdinaryJourney": ordinary,
                    "afterExplicitProbe": after_probe,
                },
            }


def expect_disconnected(page: InstalledPage) -> None:
    if page.observe("return document.querySelector('.topbar .menu-trigger .label')?.textContent;") != "未连接":
        raise HarnessFailure("installed_passive_action_activated_connection")
    if page.observe("return !!document.querySelector('[role=dialog]');"):
        raise HarnessFailure("installed_passive_action_opened_scope_picker")
