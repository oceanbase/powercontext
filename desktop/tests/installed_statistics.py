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

"""Compare the real installed statistics view with independent Server responses."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

import httpx
from installed_fixture import isolated_server
from real_server import HarnessFailure

if TYPE_CHECKING:
    from pathlib import Path

    from installed_workflow import InstalledPage

LABELS: dict[str, dict[str, Any]] = {
    "zh": {
        "load": "加载统计",
        "period": "用量周期（UTC）",  # noqa: RUF001 - exact localized UI label
        "inventory": "存量快照",
        "usage": "已记录的模型用量合计",
        "daily": "每日模型用量（UTC）",  # noqa: RUF001 - exact localized UI caption
        "recall": "召回 token 估算",
        "recall_daily": "每日召回估算（UTC）",  # noqa: RUF001 - exact localized UI caption
        "coverage": "返回的范围覆盖",
        "dates": "UTC 起止日期（含首尾）",  # noqa: RUF001 - exact localized UI label
        "unknown": "未知",
        "operations": ["生成", "嵌入"],
        "recall_rows": [
            "准备次数",
            "就绪准备次数",
            "可比较准备次数",
            "基线 token（估算）",  # noqa: RUF001 - exact localized UI label
            "召回 token（估算）",  # noqa: RUF001 - exact localized UI label
            "token 减少量（估算）",  # noqa: RUF001 - exact localized UI label
        ],
        "inventory_rows": [
            "来源",
            "已完成 Memory 处理的来源",
            "待处理 Memory 的来源",
            "制品",
            "候选",
            "待决候选",
            "已批准候选",
            "已拒绝候选",
            "记忆条目",
            "有效记忆条目",
            "无效记忆条目",
        ],
    },
    "en": {
        "load": "Load statistics",
        "period": "Usage period (UTC)",
        "inventory": "Inventory snapshot",
        "usage": "Recorded model usage totals",
        "daily": "Daily model usage (UTC)",
        "recall": "Recall token estimates",
        "recall_daily": "Daily recall estimates (UTC)",
        "coverage": "Returned Scope coverage",
        "dates": "Inclusive UTC dates",
        "unknown": "Unknown",
        "operations": ["Generation", "Embedding"],
        "recall_rows": [
            "Preparations",
            "Ready preparations",
            "Comparable preparations",
            "Baseline tokens (estimate)",
            "Recalled tokens (estimate)",
            "Token reduction (estimate)",
        ],
        "inventory_rows": [
            "Sources",
            "Sources processed for Memory",
            "Sources pending Memory processing",
            "Artifacts",
            "Candidates",
            "Pending candidates",
            "Approved candidates",
            "Rejected candidates",
            "Memory entries",
            "Active Memory entries",
            "Inactive Memory entries",
        ],
    },
}


def expect_cleared(page: InstalledPage, previous_scope: str) -> None:
    page.wait(
        """const root = document.querySelector('.statistics');
      return root && !root.querySelector('table') && !root.textContent.includes(arguments[0]);""",
        [previous_scope],
    )


def load_and_compare(
    page: InstalledPage, server: httpx.Client, scope: str, period: str, language: str = "zh"
) -> dict[str, Any]:
    labels = LABELS[language]
    page.click(
        f"//section[contains(@class,'statistics')]//label[normalize-space(text())='{labels['period']}']/select/option[@value='{period}']"
    )
    # Establish a new completed response, rather than accepting a previous table
    # while the click's React update is still pending.
    previous = page.observe("return document.querySelector('.statistics time')?.dateTime || null;")
    for _ in range(3):
        page.button(labels["load"])
        page.wait(
            """const root = document.querySelector('.statistics');
          const time = root?.querySelector('time');
          return time && time.dateTime !== arguments[0] && root.querySelector('table');""",
            [previous],
        )
        observed = page.observe("""const root = document.querySelector('.statistics');
          return {
            asOf: root.querySelector('time').dateTime,
            facts: Object.fromEntries([...root.querySelectorAll('dl > div')].map(row => [
              row.querySelector('dt').textContent.trim(), row.querySelector('dd').textContent.trim()])),
            charts: [...root.querySelectorAll('svg[role=img]')].map(chart => chart.getAttribute('aria-label')),
            tables: Object.fromEntries([...root.querySelectorAll('table')].map(table => [
              table.querySelector('caption').textContent.trim(),
              [...table.querySelectorAll('tbody tr')].filter(row => !row.querySelector('td[colspan]'))
                .map(row => [...row.children].map(cell => cell.textContent.trim()))]))
          };""")
        response = server.post(
            "/v1/stats", json={"selection": {"mode": "exact", "scope_ids": [scope]}, "period": period}
        )
        response.raise_for_status()
        expected = response.json()
        dates = observed["facts"][labels["dates"]]
        resolved = expected["usage"]["period"]
        if resolved["start_date"] in dates and resolved["end_date"] in dates:
            break
        # The two independent reads can straddle UTC midnight. Reload against
        # the new inclusive dates without fabricating historical values.
        previous = observed["asOf"]
    else:
        raise HarnessFailure("installed_statistics_period_did_not_settle")
    as_of = datetime.fromisoformat(observed["asOf"].replace("Z", "+00:00"))
    if as_of.utcoffset() != timedelta(0) or "UTC" not in dates:
        raise HarnessFailure("installed_statistics_utc_missing")
    if scope not in observed["facts"][labels["coverage"]] or len(observed["charts"]) != 3:
        raise HarnessFailure("installed_statistics_scope_or_charts_missing")

    def display(value: Any) -> str:
        if value is None:
            return labels["unknown"]
        return f"{value:,}" if isinstance(value, int) else str(value)

    def table(caption: str, rows: list[list[Any]]) -> None:
        actual = Counter(tuple(row) for row in observed["tables"][caption])
        wanted = Counter(tuple(display(value) for value in row) for row in rows)
        if actual != wanted:
            raise HarnessFailure("installed_statistics_table_mismatch", caption)

    inventory = expected["inventory"]
    counts = [inventory["sources"][key] for key in ("total", "memory_processed", "memory_pending")]
    counts += [inventory["artifacts"]["total"]]
    counts += [inventory["candidates"][key] for key in ("total", "pending", "approved", "rejected")]
    counts += [inventory["memory"]["entries"][key] for key in ("total", "active", "inactive")]
    table(labels["inventory"], [[name, value] for name, value in zip(labels["inventory_rows"], counts, strict=True)])
    operations = ("generation", "embedding")
    metrics = ("requests", "input_tokens", "output_tokens")
    usage = expected["usage"]
    table(
        labels["usage"],
        [
            [labels["operations"][index], *[usage["totals"][operation][key] for key in metrics]]
            for index, operation in enumerate(operations)
        ],
    )
    table(
        labels["daily"],
        [
            [day["date"], labels["operations"][index], *[day[operation][key] for key in metrics]]
            for day in usage["daily"]
            for index, operation in enumerate(operations)
        ],
    )
    fields = (
        "preparations",
        "ready_preparations",
        "comparable_preparations",
        "baseline_tokens",
        "recalled_tokens",
        "token_reduction",
    )
    table(
        labels["recall"],
        [[label, expected["recall"]["totals"][key]] for label, key in zip(labels["recall_rows"], fields, strict=True)],
    )
    table(labels["recall_daily"], [[day["date"], *[day[key] for key in fields]] for day in expected["recall"]["daily"]])
    return expected


def open_statistics(page: InstalledPage, language: str = "zh") -> None:
    page.button("Observation" if language == "en" else "观察")
    tab = "Current-Scope inventory & usage" if language == "en" else "当前 Scope 库存与用量"
    page.click(f"//button[@role='tab' and normalize-space(.)='{tab}']")


def exercise_statistics(
    page: InstalledPage, server: httpx.Client, scope: str, usage_control: Path
) -> dict[str, object]:
    open_statistics(page)
    page.wait("return !!document.querySelector('.statistics');")
    if page.observe("return !!document.querySelector('.statistics table');"):
        raise HarnessFailure("installed_statistics_loaded_without_action")
    empty = load_and_compare(page, server, scope, "30d")
    if empty["inventory"]["memory"]["entries"]["total"] != 0:
        raise HarnessFailure("installed_statistics_fresh_inventory_not_empty")
    usage_control.touch()
    partial = load_and_compare(page, server, scope, "7d")
    if partial["usage"]["totals"]["generation"] != {"requests": 2, "input_tokens": None, "output_tokens": 9}:
        raise HarnessFailure("installed_statistics_partial_usage_not_preserved")
    for period in ("today", "30d"):
        load_and_compare(page, server, scope, period)
    page.button("设置与诊断")
    page.click("//select[@id='language']/option[@value='en']")
    open_statistics(page, "en")
    load_and_compare(page, server, scope, "7d", "en")
    page.button("Settings & diagnostics")
    page.click("//select[@id='language']/option[@value='zh']")
    open_statistics(page)
    load_and_compare(page, server, scope, "7d")
    created = server.post(
        "/v1/scopes",
        json={
            "title": "Desktop installed CI stats B",
            "summary": "Statistics isolation",
            "idempotency_key": "desktop-stats-scope-b",
        },
    )
    created.raise_for_status()
    next_scope = created.json()["scope_id"]
    page.select_scope(next_scope)
    expect_cleared(page, scope)
    load_and_compare(page, server, next_scope, "7d")
    with isolated_server() as (other, other_scope, _):
        seeded = other.post(
            "/v1/memory/remember",
            json={"scope_id": other_scope, "kind": "note", "text": "Statistics installed fixture note"},
        )
        seeded.raise_for_status()
        page.connect("Desktop CI stats B", str(other.base_url).rstrip("/"))
        page.select_scope(other_scope)
        open_statistics(page)
        expect_cleared(page, next_scope)
        different = load_and_compare(page, other, other_scope, "7d")
        if different["inventory"]["memory"]["entries"]["total"] != 1:
            raise HarnessFailure("installed_statistics_other_inventory_mismatch")
        page.button("连接")
        page.profile("Desktop CI synthetic")
        page.activate("Desktop CI synthetic")
        page.select_scope(scope)
        open_statistics(page)
        expect_cleared(page, other_scope)
        load_and_compare(page, server, scope, "7d")
    page.button("记忆")
    return {
        "exactScopeInventoryAndUsage": True,
        "independentServerTables": True,
        "utcPeriods": ["today", "7d", "30d"],
        "partialTokenReportRemainsUnknown": True,
        "bilingualStatistics": True,
        "scopeAndConnectionStatisticsIsolation": True,
    }
