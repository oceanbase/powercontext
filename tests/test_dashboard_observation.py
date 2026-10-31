# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
# http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Observe navigation and live health through the authenticated HTML boundary."""

import re
from html import unescape
from typing import cast
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import FastAPI

from powercontext.http._generated.models import ReadinessResponse, ReadinessStatus
from tests.test_dashboard import create_scope
from tests.test_dashboard import dashboard as dashboard


def href_for(html, label):
    for match in re.finditer(r'<a\b[^>]*href="([^"]+)"[^>]*>(.*?)</a>', html, re.S):
        text = " ".join(unescape(re.sub(r"<[^>]*>", "", match[2])).split()).removesuffix(" →")
        if text == label:
            return unescape(match[1])
    raise AssertionError(label)


def test_home_empty_processing_opens_sources_and_review_action_is_useful(dashboard):
    scope = create_scope(dashboard, "Compact home")["scope_id"]
    response = dashboard.get("/dashboard/home", params={"scope": scope, "lang": "en"})
    assert response.status_code == 200
    sources = href_for(response.text, "Saved sources")
    assert parse_qs(urlsplit(sources).query)["record_type"] == ["sources"]
    assert "View pending candidates" not in response.text
    observation = href_for(response.text, "Automatic processing")
    assert dashboard.get(observation).status_code == 200


@pytest.mark.parametrize("public_reads", [False, True])
def test_observation_tabs_period_and_stage_navigation(dashboard, monkeypatch, public_reads):
    scope = create_scope(dashboard, "Observation navigation")["scope_id"]
    if public_reads:
        monkeypatch.setattr("powercontext.server.dashboard.api._builtin_runtime", lambda request: None)
    response = dashboard.get("/dashboard/processing", params={"scope": scope, "period": "30d", "lang": "en"})
    assert response.status_code == 200
    for label, path in [
        ("Automatic processing", "processing"),
        ("Retrieval and use", "usage"),
        ("Runtime state", "runtime"),
    ]:
        destination = href_for(response.text, label)
        assert urlsplit(destination).path == f"/dashboard/{path}"
        assert parse_qs(urlsplit(destination).query)["period"] == ["30d"]
        assert dashboard.get(destination).status_code == 200
    html_match = re.search(r"<aside\b.*?</aside>", response.text, re.S)
    assert html_match is not None
    sidebar = html_match[0]
    direct = href_for(sidebar, "Automatic processing")
    assert parse_qs(urlsplit(direct).query)["record_type"] == ["sources"]
    assert 'class="nav nav-tabs observation-tabs"' not in response.text
    today = href_for(response.text, "Today")
    changed = dashboard.get(today)
    assert changed.status_code == 200
    assert changed.url.params["period"] == "today"
    assert changed.url.params["scope"] == scope
    assert "data-processing-results" not in response.text
    assert "Processing history is unavailable for this period" in response.text
    for label in ["Saved sources", "Dream task records"]:
        assert dashboard.get(href_for(response.text, label)).status_code == 200


@pytest.mark.parametrize("ready", [True, False])
def test_runtime_shows_readiness_without_scope_inventory(dashboard, ready):
    async def probe():
        return ReadinessResponse(
            status=ReadinessStatus.READY if ready else ReadinessStatus.NOT_READY,
            checks={"runtime": "ready" if ready else "not_ready"},
        )

    cast(FastAPI, dashboard.app).state.readiness_probe = probe
    response = dashboard.get("/dashboard/runtime", params={"lang": "en"})
    assert response.status_code == 200, response.text
    assert "All services" in response.text
    assert ("Ready to serve" if ready else "Not ready to serve") in response.text
    assert "Process is responding" in response.text
    assert 'data-runtime-check="runtime"' in response.text
    dashboard.headers.pop("Authorization")
    denied = dashboard.get("/dashboard/runtime")
    assert denied.status_code == 401
    assert 'data-runtime-check="runtime"' not in denied.text


def test_runtime_failed_probe_is_unknown_and_does_not_hide_live_response(dashboard):
    async def probe():
        raise RuntimeError("internal backend detail")  # noqa: TRY003 - Verify provider details stay private.

    cast(FastAPI, dashboard.app).state.readiness_probe = probe
    response = dashboard.get("/dashboard/runtime", params={"lang": "en"})
    assert response.status_code == 200
    assert "Check unavailable" in response.text
    assert "Process is responding" in response.text
    assert "Ready to serve" not in response.text
    assert "internal backend detail" not in response.text


def test_runtime_degraded_preserves_partial_availability(dashboard):
    async def probe():
        return ReadinessResponse(status=ReadinessStatus.DEGRADED, checks={"generation": "unavailable"})

    cast(FastAPI, dashboard.app).state.readiness_probe = probe
    response = dashboard.get("/dashboard/runtime", params={"lang": "en"})
    assert response.status_code == 200
    assert "Some capabilities are available" in response.text
    assert "Not ready to serve" not in response.text
    assert "Unavailable" in response.text
    assert "Enforced" in response.text


def test_empty_overview_explains_unchanged_counts_and_shows_selected_window(dashboard):
    import time_machine

    with time_machine.travel("2026-11-07T09:00:00Z", tick=False):
        scope = create_scope(dashboard, "Recorded events versus inventory")["scope_id"]
        saved = dashboard.post(
            "/v1/memory/remember", json={"scope_id": scope, "kind": "fact", "text": "Existing saved memory."}
        )
        assert saved.status_code == 200
        for period, start in [("today", "2026-11-07 00:00 UTC"), ("30d", "2026-10-09 00:00 UTC")]:
            response = dashboard.get("/dashboard/processing", params={"scope": scope, "period": period, "lang": "en"})
            assert response.status_code == 200
            assert "Processing history is unavailable for this period" in response.text
            assert "Missing records do not prove inactivity or successful processing." in response.text
            assert start in response.text
            assert "2026-11-08 00:00 UTC" in response.text
        notes = dashboard.get("/dashboard/notes", params={"scope": scope, "lang": "en"})
        assert "Existing saved memory." in notes.text


@pytest.mark.parametrize("window,start", [("90d", "2026-08-10"), ("custom", "2026-01-03")])
def test_processing_extended_and_custom_windows_preserve_navigation(dashboard, window, start):
    import time_machine

    with time_machine.travel("2026-11-07T09:00:00Z", tick=False):
        scope = create_scope(dashboard, "Flexible reporting window")["scope_id"]
        params = {"scope": scope, "period": "7d", "processing_period": window, "lang": "en"}
        if window == "custom":
            params.update(processing_start="2026-01-03", processing_end="2026-03-30")
        page = dashboard.get("/dashboard/processing", params=params)
        assert page.status_code == 200, page.text
        assert f"{start} 00:00 UTC" in page.text
        if window == "custom":
            assert "2026-03-31 00:00 UTC" in page.text
        attempts = next(
            unescape(href)
            for href in re.findall(r'href="([^"]+)"', page.text)
            if parse_qs(urlsplit(unescape(href)).query).get("record_type") == ["attempts"]
        )
        assert parse_qs(urlsplit(attempts).query)["processing_period"] == [window]
        opened = dashboard.get(attempts)
        assert opened.status_code == 200, opened.text
        assert f"{start} 00:00 UTC" in opened.text
        usage = dashboard.get(href_for(page.text, "Retrieval and use"))
        assert usage.status_code == 200, usage.text


@pytest.mark.parametrize(
    "start,end",
    [
        ("", "2026-11-07"),
        ("bad", "2026-11-07"),
        ("2026-11-08", "2026-11-07"),
        ("2024-01-01", "2026-11-07"),
        ("9999-12-31", "9999-12-31"),
    ],
)
def test_custom_processing_window_rejects_invalid_dates_with_editable_form(dashboard, start, end):
    response = dashboard.get(
        "/dashboard/processing",
        params={
            "processing_period": "custom",
            "processing_start": start,
            "processing_end": end,
            "lang": "en",
        },
    )
    assert response.status_code == 422
    assert 'name="processing_start"' in response.text
    assert "Choose valid start and end dates, in order, covering at most 92 days." in response.text


def test_overview_reads_existing_daily_aggregates_without_fabricating_detail_history(dashboard):
    import time_machine

    from tests.test_dashboard_home_summary import summary_value

    scope = create_scope(dashboard, "Aggregate-only observation")["scope_id"]
    foreign = create_scope(dashboard, "Other aggregate scope")["scope_id"]
    saved = dashboard.post(
        "/v1/memory/remember", json={"scope_id": scope, "kind": "fact", "text": "Saved deployment policy."}
    )
    assert saved.status_code == 200
    runtime = cast(FastAPI, dashboard.app).state.application
    runtime.context_usage.recording_enabled = False
    for day, count in [("2026-10-20T09:00:00Z", 2), ("2026-10-24T09:00:00Z", 1)]:
        with time_machine.travel(day, tick=False):
            for _ in range(count):
                response = dashboard.post("/v1/context/prepare", json={"scope_id": scope, "query": "deployment"})
                assert response.status_code == 200
    with time_machine.travel("2026-11-07T09:00:00Z", tick=False):
        for period, expected in [("today", "0"), ("30d", "3")]:
            page = dashboard.get("/dashboard/processing", params={"scope": scope, "period": period, "lang": "en"})
            assert page.status_code == 200
            assert 'data-summary="observation-sources"' not in page.text
            assert "data-processing-results" not in page.text
            assert "Nothing needs attention" not in page.text
            home = dashboard.get("/dashboard/home", params={"scope": scope, "period": period, "lang": "en"})
            assert summary_value(home.text, "sources") == "1"
            assert summary_value(home.text, "memories") == "1"
            assert summary_value(home.text, "preparations") == expected
        custom = dashboard.get(
            "/dashboard/processing",
            params={
                "scope": scope,
                "processing_period": "custom",
                "processing_start": "2026-10-20",
                "processing_end": "2026-10-20",
                "lang": "en",
            },
        )
        assert custom.status_code == 200
        assert 'data-summary="observation-preparations"' not in custom.text
        assert "Processing history is unavailable for this period" in custom.text
        summary = dashboard.get(f"/v1/scopes/{scope}/processing/summary").json()
        assert (summary["attempts"], summary["deliveries"]) == (0, 0)
        usage = dashboard.get(
            "/dashboard/usage", params={"scope": scope, "view": "records", "period": "30d", "lang": "en"}
        )
        assert "No usage records" in usage.text
        empty = dashboard.get("/dashboard/processing", params={"scope": foreign, "period": "30d", "lang": "en"})
        assert "Processing history is unavailable for this period" in empty.text


def test_public_only_custom_window_does_not_mix_inventory_and_processing(dashboard, monkeypatch):
    from powercontext.server.dashboard.routes import LABELS

    scope = create_scope(dashboard, "Public-only coverage")["scope_id"]
    saved = dashboard.post("/v1/memory/remember", json={"scope_id": scope, "kind": "fact", "text": "Saved policy."})
    assert saved.status_code == 200
    monkeypatch.setattr("powercontext.server.dashboard.api._builtin_runtime", lambda request: None)
    page = dashboard.get(
        "/dashboard/processing",
        params={
            "scope": scope,
            "processing_period": "custom",
            "processing_start": "2026-10-20",
            "processing_end": "2026-10-24",
            "lang": "zh",
        },
    )
    assert page.status_code == 200, page.text
    assert 'data-summary="observation-sources"' not in page.text
    assert LABELS["processing_history_unknown"] in page.text
    assert LABELS["processing_live_scope"] in page.text


@pytest.mark.parametrize("state", ["idle", "pending", "blocked"])
def test_processing_overview_shows_scope_topic_state_outside_history_window(dashboard, monkeypatch, state):
    from powercontext.server.dashboard.api import DashboardAPI

    scope = create_scope(dashboard, "Topic processing state")["scope_id"]
    original = DashboardAPI.read

    async def read(api, path, payload=None):
        if path == "/v1/topic-memory/processing/status":
            assert payload == {"scope_id": scope}
            return {
                "state": state,
                "source_cursor": 10,
                "source_head": 142,
                "target_source_through": 142,
                "block": {
                    "blocked_at": "2025-01-01T00:00:00Z",
                    "attempts": 3,
                    "error_code": "window_attempt_limit",
                    "last_error_code": None,
                }
                if state == "blocked"
                else None,
            }
        return await original(api, path, payload)

    monkeypatch.setattr(DashboardAPI, "read", read)
    page = dashboard.get("/dashboard/processing", params={"scope": scope, "period": "today", "lang": "en"})
    assert page.status_code == 200
    assert {"idle": "No pending window", "pending": "Work pending", "blocked": "Blocked since"}[state] in page.text
    assert "Processing history is unavailable for this period" in page.text
    if state == "blocked":
        assert "2025-01-01" in page.text
        assert "window_attempt_limit" in page.text
        assert "The difference is not a pending-source count." in page.text


def test_processing_overview_exposes_recent_source_and_exact_detail(dashboard):
    scope = create_scope(dashboard, "Recent source results")["scope_id"]
    saved = dashboard.post(
        "/v1/memory/remember", json={"scope_id": scope, "kind": "fact", "text": "Use the deployment checklist."}
    )
    assert saved.status_code == 200
    page = dashboard.get("/dashboard/processing", params={"scope": scope, "lang": "en"})
    assert page.status_code == 200
    assert "Recent sources and results" in page.text
    assert "Use the deployment checklist." in page.text
    destination = href_for(page.text, "Use the deployment checklist.")
    assert "source_id" in parse_qs(urlsplit(destination).query)
    assert dashboard.get(destination).status_code == 200


@pytest.mark.parametrize("page,label", [("processing", "Automatic processing"), ("usage", "Retrieval and use")])
def test_retrieval_and_use_has_direct_sidebar_navigation(dashboard, page, label):
    scope = create_scope(dashboard, "Direct usage navigation")["scope_id"]
    response = dashboard.get(
        f"/dashboard/{page}", params={"scope": scope, "period": "30d", "view": "records", "lang": "en"}
    )
    assert response.status_code == 200
    html_match = re.search(r"<aside\b.*?</aside>", response.text, re.S)
    assert html_match is not None
    sidebar = html_match[0]
    destination = href_for(sidebar, "Retrieval and use")
    params = parse_qs(urlsplit(destination).query)
    assert params["scope"] == [scope]
    assert params["period"] == ["30d"]
    assert params["view"] == ["records"]
    html_match = re.search(r'<a\b[^>]*aria-current="page"[^>]*>(.*?)</a>', sidebar, re.S)
    assert html_match is not None
    selected = html_match[1]
    assert label in selected
    assert "Experiences &amp; Skills" in sidebar
    main = response.text.split("<main ", 1)[1]
    assert (
        'class="nav nav-tabs observation-tabs"' not in main if page == "usage" else "Retrieval and use</a>" not in main
    )


@pytest.mark.parametrize("view,label", [("records", "Retrieval and use"), ("resources", "Token usage")])
def test_usage_views_are_separate_sidebar_entries(dashboard, view, label):
    scope = create_scope(dashboard, "Direct usage view navigation")["scope_id"]
    response = dashboard.get("/dashboard/usage", params={"scope": scope, "view": view, "period": "30d", "lang": "en"})
    assert response.status_code == 200
    html_match = re.search(r"<aside\b.*?</aside>", response.text, re.S)
    assert html_match is not None
    sidebar = html_match[0]
    for target, title in [("records", "Retrieval and use"), ("resources", "Token usage")]:
        destination = href_for(sidebar, title)
        query = parse_qs(urlsplit(destination).query)
        assert query["view"] == [target]
        assert query["scope"] == [scope]
        assert query["period"] == ["30d"]
    html_match = re.search(r'<a\b[^>]*aria-current="page"[^>]*>(.*?)</a>', sidebar, re.S)
    assert html_match is not None
    selected = html_match[1]
    assert label in selected
    assert f'name="view" value="{view}"' in sidebar
    main = response.text.split("<main ", 1)[1]
    assert 'class="nav nav-tabs' not in main
    assert f"<title>{label} · PowerContext</title>" in response.text


def test_sidebar_sections_follow_the_business_workflow(dashboard):
    scope = create_scope(dashboard, "Sidebar workflow")["scope_id"]
    page = dashboard.get("/dashboard/home", params={"scope": scope, "lang": "zh"})
    assert page.status_code == 200
    html_match = re.search(r"<aside\b.*?</aside>", page.text, re.S)
    assert html_match is not None
    sidebar = html_match[0]
    destinations = []
    for tag in re.findall(r"<a\b[^>]*>", sidebar):
        classes = re.search(r'class="([^"]*)"', tag)
        if classes and "nav-link" in classes[1].split():
            href = re.search(r'href="([^"]+)"', tag)
            assert href is not None
            destinations.append(urlsplit(unescape(href[1])).path)
    assert destinations == [
        "/dashboard/home",
        "/dashboard/processing",
        "/dashboard/processing",
        "/dashboard/notes",
        "/dashboard/topics",
        "/dashboard/methods",
        "/dashboard/profile",
        "/dashboard/review",
        "/dashboard/handoff",
        "/dashboard/usage",
        "/dashboard/usage",
        "/dashboard/runtime",
        "/dashboard/prompts",
    ]
    groups = [
        re.sub(r"<[^>]+>", "", text).strip()
        for text in re.findall(r'<li class="nav-section"[^>]*>(.*?)</li>', sidebar, re.S)
    ]
    assert groups == ["运行时数据", "已保存内容", "日常工作", "观测", "配置"]
    for text in ["自动处理", "Dream 处理", "检索与使用", "Token用量", "提示词"]:
        assert href_for(sidebar, text)
