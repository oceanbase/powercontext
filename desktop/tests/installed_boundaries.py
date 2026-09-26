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

"""Installed note budget and bounded search acceptance against a real Server."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
from real_server import HarnessFailure

if TYPE_CHECKING:
    from installed_workflow import InstalledPage


def exercise_note_budget(page: InstalledPage, server: httpx.Client, scope: str) -> None:
    prefix = "desktopbudgetci "
    note = prefix + "é" * ((8192 - len(prefix)) // 2)
    note += "x" * (8192 - len(note.encode("utf-8")))
    page.type("记忆内容", note + "é", "textarea")
    page.wait_text("正文超过 8192 UTF-8 字节")
    disabled = page.observe("""return [...document.querySelectorAll('button')].find(
      b => b.textContent.trim() === '保存记忆')?.disabled;""")
    if disabled is not True:
        raise HarnessFailure("installed_over_budget_save_enabled")
    matches = server.post(
        "/v1/memory/search", json={"scope_id": scope, "query": "desktopbudgetci", "mode": "fts", "limit": 10}
    )
    matches.raise_for_status()
    if matches.json()["hits"]:
        raise HarnessFailure("installed_over_budget_note_submitted")
    page.clear_note()
    page.type("记忆内容", note, "textarea")
    page.wait_text("8192 / 8192")
    page.button("保存记忆")
    page.wait_text("保存成功。")
    matches = server.post(
        "/v1/memory/search", json={"scope_id": scope, "query": "desktopbudgetci", "mode": "fts", "limit": 10}
    )
    matches.raise_for_status()
    hits = matches.json()["hits"]
    if len(hits) != 1:
        raise HarnessFailure("installed_boundary_note_missing_or_duplicated")
    entry = server.post("/v1/memory/entries/get", json={"scope_id": scope, "citation": hits[0]["citation"]})
    entry.raise_for_status()
    if entry.json()["text"] != note or len(entry.json()["text"].encode("utf-8")) != 8192:
        raise HarnessFailure("installed_boundary_note_truncated")


def exercise_search_limit(page: InstalledPage, server: httpx.Client, scope: str) -> None:
    for index in range(11):
        response = server.post(
            "/v1/memory/remember",
            json={"scope_id": scope, "kind": "note", "text": f"desktoplimitci independent result {index}"},
        )
        response.raise_for_status()
    for query, expected in [("desktopnonexistentci", 0), ("desktoplimitci", 10)]:
        page.type("全文搜索关键词", "\ue009a\ue000\ue003" + query)
        page.button("搜索")
        page.wait_text("没有匹配的记忆。" if expected == 0 else "已返回本次上限 10 条")
        count = page.observe("return document.querySelectorAll('.memory-hits li').length;")
        if count != expected:
            raise HarnessFailure("installed_search_result_count_mismatch")
        if page.observe("return !!document.querySelector('.reader');"):
            raise HarnessFailure("installed_search_kept_old_reader")
