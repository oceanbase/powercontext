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

"""W3C WebDriver interactions with the real installed application and Server."""

from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path

import httpx
from installed_boundaries import exercise_note_budget, exercise_search_limit
from installed_fixture import isolated_server
from real_server import HarnessFailure

ELEMENT = "element-6066-11e4-a52e-4f735466cecf"
NOTE = "desktopinstalledci 中文安装后验收\n纯文本 café <script>literal</script>"


class InstalledPage:
    def __init__(self, client: httpx.Client, prefix: str) -> None:
        self.client = client
        self.prefix = prefix

    def post(self, path: str, payload: dict[str, object]):
        response = self.client.post(self.prefix + path, json=payload)
        response.raise_for_status()
        return response.json()["value"]

    def element(self, xpath: str) -> str:
        for _ in range(100):
            response = self.client.post(self.prefix + "/element", json={"using": "xpath", "value": xpath})
            if response.is_success:
                identifier = response.json()["value"][ELEMENT]
                enabled = self.client.get(self.prefix + f"/element/{identifier}/enabled")
                enabled.raise_for_status()
                if enabled.json()["value"]:
                    return identifier
            elif response.json().get("value", {}).get("error") not in {"no such element", "stale element reference"}:
                response.raise_for_status()
            time.sleep(0.2)
        raise HarnessFailure("installed_element_timeout", xpath)

    def click_element(self, identifier: str) -> None:
        # Scroll through the browser before a real pointer click. WebDriver's
        # automatic edge alignment can put the target behind a wrapping sticky bar.
        self.observe(
            "arguments[0].scrollIntoView({block: 'center', inline: 'nearest'});",
            [{ELEMENT: identifier}],
        )
        self.post(f"/element/{identifier}/click", {})

    def click(self, xpath: str) -> None:
        self.click_element(self.element(xpath))

    def button(self, text: str) -> None:
        self.click(f"//button[normalize-space(.)='{text}']")

    def field(self, label: str, tag: str = "input") -> str:
        return self.element(f"//label[normalize-space(text())='{label}']//{tag}")

    def type(self, label: str, value: str, tag: str = "input") -> None:
        self.post(f"/element/{self.field(label, tag)}/value", {"text": value})

    def observe(self, script: str, args: list[object] | None = None):
        return self.post("/execute/sync", {"script": script, "args": args or []})

    def wait(self, script: str, args: list[object] | None = None) -> None:
        for _ in range(100):
            if self.observe(script, args):
                return
            time.sleep(0.2)
        raise HarnessFailure("installed_observation_timeout")

    def wait_text(self, text: str) -> None:
        self.wait("return document.body.innerText.includes(arguments[0]);", [text])

    def profile(self, name: str) -> None:
        self.click(
            f"//ul[@class='profile-list']//span[@class='profile-name'][normalize-space(.)='{name}']/ancestor::button"
        )

    def open_connection_menu(self) -> None:
        self.click("//div[contains(@class,'topbar')]/div[contains(@class,'menu-wrap')][1]/button")

    def activate(self, name: str) -> None:
        self.button("使用此连接")
        self.element(
            f"//ul[@class='profile-list']/li[.//span[@class='profile-name'][normalize-space(.)='{name}']]//span[contains(@class,'badge')]"
        )
        self.button("记忆")

    def connect(self, name: str, endpoint: str) -> None:
        self.button("连接")
        self.type("连接名称", name)
        self.type("Server 地址", endpoint)
        self.click("//label[normalize-space(text())='已验证兼容配置']/select/option[@value='sqlite-1.1.1-v1']")
        self.button("保存配置")
        self.activate(name)

    def select_scope(self, scope_id: str) -> None:
        self.button("精确范围")
        self.click("//summary[normalize-space(.)='精确 Scope ID']")
        self.type("精确 Scope ID", scope_id)
        self.button("选择范围")
        self.wait_text("当前范围: Desktop installed CI")
        self.button("关闭")

    def expect_empty_context(self) -> None:
        observed = self.observe("""return {
          reader: !!document.querySelector('.reader'),
          hits: document.querySelectorAll('.memory-hits li').length,
          draft: document.querySelector('.memory-workspace textarea')?.value,
          query: [...document.querySelectorAll('label')].find(
            label => label.textContent.trim() === '全文搜索关键词')?.querySelector('input')?.value
        };""")
        if observed != {"reader": False, "hits": 0, "draft": "", "query": ""}:
            raise HarnessFailure("installed_previous_context_not_cleared")

    def search_read(self, text: str, keyword: str = "desktopinstalledci") -> dict[str, object]:
        self.type("全文搜索关键词", keyword)
        self.button("搜索")
        self.button("阅读精确版本")
        self.wait_text("记忆详情")
        if self.observe("return document.querySelector('.reader > .plain-text')?.textContent;") != text:
            raise HarnessFailure("installed_exact_body_mismatch")
        return json.loads(self.observe("return document.querySelector('.reader pre')?.textContent;"))

    def paste(self) -> str:
        field = self.field("记忆内容", "textarea")
        self.click_element(field)
        self.post(f"/element/{field}/value", {"text": "\ue009v\ue000"})
        for _ in range(100):
            value = self.observe("return arguments[0].value;", [{ELEMENT: field}])
            if value:
                return value
            time.sleep(0.2)
        raise HarnessFailure("installed_clipboard_paste_timeout")

    def clear_note(self) -> None:
        field = self.field("记忆内容", "textarea")
        # Keys preserve React input events and do not invoke product internals.
        self.post(f"/element/{field}/value", {"text": "\ue009a\ue000\ue003"})
        if self.observe("return arguments[0].value;", [{ELEMENT: field}]) != "":
            raise HarnessFailure("installed_note_not_cleared")


def exercise_memory(client: httpx.Client, prefix: str) -> dict[str, object]:
    page = InstalledPage(client, prefix)
    with isolated_server() as (server, scope_id, wheel_digest):
        page.connect("Desktop CI synthetic", str(server.base_url).rstrip("/"))
        page.select_scope(scope_id)
        exercise_note_budget(page, server, scope_id)
        page.type("记忆内容", NOTE, "textarea")
        before_submit = server.post(
            "/v1/memory/search", json={"scope_id": scope_id, "query": "desktopinstalledci", "mode": "fts", "limit": 10}
        )
        before_submit.raise_for_status()
        if before_submit.json()["hits"]:
            raise HarnessFailure("installed_enter_submitted_without_button")
        page.button("保存记忆")
        page.wait_text("保存成功。")
        citation = page.search_read(NOTE)
        response = server.post("/v1/memory/entries/get", json={"scope_id": scope_id, "citation": citation})
        response.raise_for_status()
        if response.json()["text"] != NOTE or response.json()["citation"] != citation:
            raise HarnessFailure("installed_independent_exact_read_mismatch")
        page.button("复制正文")
        page.wait_text("已复制")
        if page.paste() != NOTE:
            raise HarnessFailure("installed_body_clipboard_mismatch")
        page.clear_note()
        page.button("复制精确引用")
        if json.loads(page.paste()) != citation:
            raise HarnessFailure("installed_citation_clipboard_mismatch")
        page.clear_note()
        exercise_search_limit(page, server, scope_id)
        exercise_connection_isolation(page, server, scope_id, citation)
        exercise_unknown_write(page)
        return {
            "serverWheelSha256": wheel_digest,
            "mode": "anonymous loopback SQLite, no model",
            "explicitConnectionAndScope": True,
            "saveSearchExactRead": True,
            "independentServerExactRead": True,
            "bodyAndCitationClipboardPaste": True,
            "twoServerConnectionIsolation": True,
            "disconnectReconnectClearsContent": True,
            "unsavedDraftCancelAndDiscard": True,
            "inactiveProfileRemovalPreservesServerData": True,
            "enterDoesNotSubmit": True,
            "rawUtf8BudgetBoundary": True,
            "emptyAndCappedSearchPresentation": True,
            "committedLostResponseUnknownWithoutReplay": True,
        }


def current_unchanged_entry(server: httpx.Client, scope: str, original: dict[str, object]) -> dict[str, object]:
    current = server.post(
        "/v1/memory/search",
        json={"scope_id": scope, "query": "desktopinstalledci", "mode": "fts", "limit": 10},
    )
    current.raise_for_status()
    current_hits = current.json()["hits"]
    if len(current_hits) != 1:
        raise HarnessFailure("installed_original_server_search_ambiguous")
    current_citation_a = current_hits[0]["citation"]
    # New independent notes advance the artifact revision, while this entry's
    # version remains unchanged. Preserve the original citation for exact reads.
    if any(current_citation_a[key] != original[key] for key in ("entry_id", "entry_version_id")):
        raise HarnessFailure("installed_original_entry_version_changed")
    return current_citation_a


def exercise_connection_isolation(
    page: InstalledPage,
    server_a: httpx.Client,
    scope_a: str,
    citation_a: dict[str, object],
) -> None:
    current_citation_a = current_unchanged_entry(server_a, scope_a, citation_a)
    text_b = "desktopinstalledci B 独立服务中的另一条记忆"
    with isolated_server() as (server_b, scope_b, _):
        seeded = server_b.post("/v1/memory/remember", json={"scope_id": scope_b, "kind": "note", "text": text_b})
        seeded.raise_for_status()
        citation_b = seeded.json()["entry"]["citation"]
        page.connect("Desktop CI B", str(server_b.base_url).rstrip("/"))
        page.expect_empty_context()
        page.select_scope(scope_b)
        if page.search_read(text_b) != citation_b:
            raise HarnessFailure("installed_second_server_citation_mismatch")
        draft = "desktopunsavedci 不应写入的草稿"
        page.type("记忆内容", draft, "textarea")
        page.open_connection_menu()
        page.button("断开桌面连接")
        alert = page.client.get(page.prefix + "/alert/text")
        alert.raise_for_status()
        if alert.json()["value"] != "丢弃尚未保存的输入？":  # noqa: RUF001 - exact localized UI
            raise HarnessFailure("installed_disconnect_discard_confirmation_missing")
        page.post("/alert/dismiss", {})
        if page.observe("return document.querySelector('.memory-workspace textarea')?.value;") != draft:
            raise HarnessFailure("installed_cancel_disconnect_lost_draft")
        if page.observe("return document.querySelector('.reader > .plain-text')?.textContent;") != text_b:
            raise HarnessFailure("installed_cancel_disconnect_changed_reader")
        page.open_connection_menu()
        page.button("断开桌面连接")
        page.post("/alert/accept", {})
        page.wait_text("尚未连接")
        page.expect_empty_context()
        page.button("连接")
        page.profile("Desktop CI synthetic")
        page.activate("Desktop CI synthetic")
        page.expect_empty_context()
        page.select_scope(scope_a)
        if page.search_read(NOTE) != current_citation_a:
            raise HarnessFailure("installed_reconnected_citation_mismatch")
        page.button("连接")
        page.profile("Desktop CI B")
        page.button("移除连接")
        page.post("/alert/accept", {})
        page.wait("""return ![...document.querySelectorAll('.profile-name')].some(
          name => name.textContent.trim() === 'Desktop CI B');""")
        for server, scope, citation, expected in (
            (server_a, scope_a, citation_a, NOTE),
            (server_b, scope_b, citation_b, text_b),
        ):
            response = server.post("/v1/memory/entries/get", json={"scope_id": scope, "citation": citation})
            response.raise_for_status()
            if response.json()["text"] != expected:
                raise HarnessFailure("installed_profile_operation_changed_server_data")
        page.button("记忆")
        if page.search_read(NOTE) != current_citation_a:
            raise HarnessFailure("installed_inactive_profile_removal_changed_active_connection")


def exercise_unknown_write(page: InstalledPage) -> None:
    note = "desktoplostuici 已提交但响应丢失的中文记忆"
    with tempfile.TemporaryDirectory(prefix="desktop-ui-response-loss-") as directory:
        counter = Path(directory) / "remember-count"
        counter.write_text("0", encoding="utf-8")
        with isolated_server(response_loss_counter=counter) as (server, scope, _):
            page.connect("Desktop CI response loss", str(server.base_url).rstrip("/"))
            page.select_scope(scope)
            page.type("记忆内容", note, "textarea")
            page.button("保存记忆")
            page.wait_text("提交结果未知。")
            if page.observe("return document.querySelector('.memory-workspace textarea')?.value;") != note:
                raise HarnessFailure("installed_unknown_write_lost_draft")
            citation = page.search_read(note, "desktoplostuici")
            matches = server.post(
                "/v1/memory/search",
                json={
                    "scope_id": scope,
                    "query": "desktoplostuici",
                    "mode": "fts",
                    "limit": 10,
                },
            )
            matches.raise_for_status()
            hits = matches.json()["hits"]
            if len(hits) != 1 or hits[0]["citation"] != citation or counter.read_text(encoding="utf-8") != "1":
                raise HarnessFailure("installed_unknown_write_replayed_or_missing")
            page.clear_note()
