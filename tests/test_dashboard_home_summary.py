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

"""Read the home summary through real scoped operations and isolated storage."""

import re
from datetime import UTC, datetime, timedelta
from html import unescape
from typing import cast
from urllib.parse import parse_qs, urlsplit

import pytest
import time_machine
from fastapi import FastAPI
from fastapi.testclient import TestClient

from powercontext.server.dashboard.routes import LABELS
from tests.test_dashboard import commit_handoff, create_scope
from tests.test_dashboard import dashboard as dashboard


def summary_value(html: str, name: str) -> str:
    match = re.search(rf'data-summary="{re.escape(name)}">([^<]*)<', html)
    assert match is not None
    return unescape(match[1]).strip()


def test_home_headings_use_saved_text_instead_of_internal_payloads(dashboard):
    scope = create_scope(dashboard, "Readable home records")["scope_id"]
    saved = dashboard.post(
        "/v1/memory/remember", json={"scope_id": scope, "kind": "fact", "text": "Check the API contract."}
    )
    assert saved.status_code == 200, saved.text
    profile = dashboard.post(
        f"/v1/scopes/{scope}/artifacts",
        json={"family": "profile", "content": {"content": "# Payment service\n\nReview release evidence."}},
    )
    assert profile.status_code == 201, profile.text
    assert dashboard.portal is not None
    dashboard.portal.call(cast(FastAPI, dashboard.app).state.application.atomic_memory.for_scope(scope).flush)
    home = dashboard.get("/dashboard/home", params={"scope": scope, "lang": "en"})
    assert home.status_code == 200, home.text
    assert re.search(r'<a class="home-processing-input"[^>]*>Check the API contract\.</a>', home.text)
    assert re.search(r'<a class="home-processing-input"[^>]*>Payment service</a>', home.text)
    assert "powercontext.atomic-memory.v1" not in home.text


@pytest.mark.parametrize("public_reads", [False, True])
def test_home_summary_matches_scope_statistics_and_not_preview_size(dashboard: TestClient, monkeypatch, public_reads):
    scope = create_scope(dashboard, "Summary project")["scope_id"]
    child = create_scope(dashboard, "Empty child", parent=scope)["scope_id"]
    for index in range(5):
        saved = dashboard.post(
            "/v1/memory/remember", json={"scope_id": scope, "kind": "fact", "text": f"Project rule {index}."}
        )
        assert saved.status_code == 200, saved.text
    captured = dashboard.post(
        "/v1/sources/content", json={"scope_id": scope, "source_id": "summary-notes", "content": "Project notes."}
    )
    assert captured.status_code == 202, captured.text
    expected = dashboard.post("/v1/stats", json={"selection": {"mode": "exact", "scope_ids": [scope]}})
    assert expected.status_code == 200, expected.text
    inventory = expected.json()["inventory"]
    if public_reads:
        monkeypatch.setattr("powercontext.server.dashboard.api._builtin_runtime", lambda request: None)
    for headers, language in (({}, "zh"), ({"HX-Request": "true"}, "en")):
        page = dashboard.get("/dashboard/home", params={"scope": scope, "lang": language}, headers=headers)
        assert page.status_code == 200, page.text
        assert summary_value(page.text, "sources") == str(inventory["sources"]["total"])
        assert summary_value(page.text, "memories") == "5"
        assert summary_value(page.text, "pending") == "0"
        assert page.text.index('id="home-summary"') < page.text.index('id="usage"')
        review_link = next(
            unescape(url)
            for url in re.findall(r'href="([^"]+)"', page.text)
            if "/dashboard/review?" in url and "resource_type=all" in url
        )
        assert (
            parse_qs(urlsplit(review_link).query).items()
            >= {"scope": [scope], "resource_type": ["all"], "review_status": ["pending"]}.items()
        )
    empty = dashboard.get("/dashboard/home", params={"scope": child, "lang": "zh"})
    assert empty.status_code == 200, empty.text
    assert summary_value(empty.text, "sources") == "0"
    assert summary_value(empty.text, "memories") == "0"
    assert LABELS["home_overview_empty"] in empty.text


def test_home_summary_tracks_candidate_review_and_preserves_navigation(dashboard: TestClient):
    scope = create_scope(dashboard, "Summary review")["scope_id"]
    captured = dashboard.post(
        "/v1/sources/content", json={"scope_id": scope, "source_id": "review", "content": "Inspect the report."}
    )
    assert captured.status_code == 202, captured.text
    proposed = dashboard.post(
        "/v1/skill/propose",
        json={
            "scope_id": scope,
            "proposal": {
                "name": "inspect-report",
                "description": "Inspect the report",
                "instructions": "Read the report.",
                "validation": ["Keep unknown results explicit."],
            },
            "source_refs": [{"name": "content", "source_id": "review"}],
            "artifact_refs": [],
        },
    )
    assert proposed.status_code == 201, proposed.text
    params = {"scope": scope, "period": "30d", "lang": "en", "theme": "dark"}
    pending = dashboard.get("/dashboard/home", params=params)
    assert pending.status_code == 200, pending.text
    assert summary_value(pending.text, "pending") == "1"
    assert summary_value(pending.text, "skill") == "0"
    candidate = proposed.json()
    approved = dashboard.post(
        "/v1/candidates/approve",
        json={"scope_id": scope, "candidate_id": candidate["candidate_id"], "expected_version": candidate["version"]},
    )
    assert approved.status_code == 200, approved.text
    home = dashboard.get("/dashboard/home", params=params)
    assert home.status_code == 200, home.text
    assert summary_value(home.text, "pending") == "0"
    assert summary_value(home.text, "skill") == "1"
    skill_link = next(
        unescape(url)
        for url in re.findall(r'href="([^"]+)"', home.text)
        if "/dashboard/methods?" in url and "kind=skill" in url
    )
    opened = dashboard.get(skill_link)
    assert opened.status_code == 200, opened.text
    assert opened.url.params["scope"] == scope
    assert opened.url.params["period"] == "30d"
    assert '<html lang="en">' in opened.text
    assert "inspect-report" in opened.text


def test_home_summary_period_filters_preparations_but_not_inventory(dashboard: TestClient):
    scope = create_scope(dashboard, "Summary period")["scope_id"]
    saved = dashboard.post(
        "/v1/memory/remember", json={"scope_id": scope, "kind": "fact", "text": "Review the API contract."}
    )
    assert saved.status_code == 200, saved.text
    with time_machine.travel(datetime.now(UTC) - timedelta(days=10), tick=False):
        previous = dashboard.post("/v1/context/prepare", json={"scope_id": scope, "query": "API contract"})
        assert previous.status_code == 200, previous.text
    current = dashboard.post("/v1/context/prepare", json={"scope_id": scope, "query": "API contract"})
    assert current.status_code == 200, current.text
    for period, count in (("today", "1"), ("30d", "2")):
        home = dashboard.get("/dashboard/home", params={"scope": scope, "period": period})
        assert home.status_code == 200, home.text
        assert summary_value(home.text, "preparations") == count
        assert summary_value(home.text, "memories") == "1"


def test_home_summary_review_count_and_link_include_tag_candidates(dashboard: TestClient):
    from powercontext.builtin.catalog_changes.models import CatalogChangeCandidate
    from powercontext.builtin.persistence.catalog_changes import CatalogCandidateRepository

    scope = create_scope(dashboard, "Tag candidate overview")["scope_id"]
    created = dashboard.post(
        f"/v1/scopes/{scope}/artifacts",
        json={
            "family": "experience",
            "content": {
                "situation": "Release",
                "action": "Check",
                "outcome": "Verified",
                "lesson": "Check the report.",
            },
        },
    )
    assert created.status_code == 201, created.text
    ref = {key: created.json()[key] for key in ("family", "artifact_id", "revision")}
    captured = dashboard.post(
        "/v1/sources/content", json={"scope_id": scope, "source_id": "tag-evidence", "content": "Verified report."}
    )
    assert captured.status_code == 202, captured.text
    tags = dashboard.get(f"/v1/scopes/{scope}/artifacts/experience/{ref['artifact_id']}/tags")
    assert tags.status_code == 200, tags.text
    candidate = CatalogChangeCandidate.model_validate({
        "candidate_id": "summary-tag-candidate",
        "version": 1,
        "origin": "manual",
        "reason": "Verified report.",
        "sources": [{"source_type": "content", "source_id": "tag-evidence"}],
        "proposal": {
            "target": {"type": "artifact", "family": "experience", "artifact_id": ref["artifact_id"]},
            "basis_ref": ref,
            "expected_etag": tags.headers["ETag"],
            "before_tags": [],
            "after_tags": ["verified"],
        },
    })

    async def store_candidate():
        async with cast(FastAPI, dashboard.app).state.application.primary_database.transaction() as connection:
            await CatalogCandidateRepository().create(connection, scope, candidate)

    assert dashboard.portal is not None
    dashboard.portal.call(store_candidate)
    page = dashboard.get("/dashboard/home", params={"scope": scope})
    assert page.status_code == 200, page.text
    assert summary_value(page.text, "pending") == "1"
    review_link = next(
        unescape(url)
        for url in re.findall(r'href="([^"]+)"', page.text)
        if "/dashboard/review?" in url and "resource_type=all" in url
    )
    review = dashboard.get(review_link)
    assert review.status_code == 200, review.text
    assert candidate.candidate_id in review.text


def test_home_summary_inventory_failure_does_not_look_empty(dashboard: TestClient, monkeypatch):
    from powercontext.builtin.runtime.scope_reads import ScopeReadSnapshot

    scope = create_scope(dashboard, "Readable summary")["scope_id"]
    saved = dashboard.post(
        "/v1/memory/remember", json={"scope_id": scope, "kind": "fact", "text": "Readable despite inventory failure."}
    )
    assert saved.status_code == 200, saved.text

    async def unavailable(self):
        raise RuntimeError

    monkeypatch.setattr(ScopeReadSnapshot, "inventory", unavailable)
    page = dashboard.get("/dashboard/home", params={"scope": scope})
    assert page.status_code == 200, page.text
    assert summary_value(page.text, "memories") == LABELS["home_unknown"]
    assert LABELS["home_inventory_unavailable"] in page.text
    assert LABELS["home_overview_empty"] not in page.text
    assert "Readable despite inventory failure." in page.text


@pytest.mark.parametrize("public_reads", [False, True])
def test_home_includes_processing_summary_and_direct_record_navigation(dashboard, monkeypatch, public_reads):
    scope = create_scope(dashboard, "Home processing overview")["scope_id"]
    if public_reads:
        monkeypatch.setattr("powercontext.server.dashboard.api._builtin_runtime", lambda request: None)
    response = dashboard.get("/dashboard/home", params={"scope": scope, "period": "30d", "lang": "en"})
    assert response.status_code == 200, response.text
    section = response.text.split('<section id="recent-processing"', 1)[1].split("</section>", 1)[0]
    assert "Automatic processing" in section
    assert "30 days" not in section
    assert 'id="home-processing-summary"' not in section
    assert 'data-bs-toggle="dropdown"' not in section

    links = [unescape(value) for value in re.findall(r'href="([^"]+)"', section) if "/dashboard/processing?" in value]
    assert any(parse_qs(urlsplit(link).query).get("record_type") == ["sources"] for link in links)
    assert all(parse_qs(urlsplit(link).query)["period"] == ["30d"] for link in links)
    saved = response.text.split('<section id="notes"', 1)[1].split("</section>", 1)[0]
    assert 'id="home-summary"' in response.text
    assert response.text.index('id="home-summary"') < response.text.index('id="recent-processing"')
    assert 'id="home-processing-summary"' not in saved
    assert "record_type=overview" not in response.text


@pytest.mark.parametrize("topic_state", ["idle", "blocked"])
def test_home_prioritizes_problems_and_combines_processing_evidence(dashboard, monkeypatch, topic_state):
    from powercontext.server.dashboard.api import DashboardAPI

    scope = create_scope(dashboard, "Home information hierarchy")["scope_id"]
    original = DashboardAPI.read

    async def read(api, path, payload=None):
        value = await original(api, path, payload)
        if path == "/v1/topic-memory/processing/status":
            return {
                **value,
                "state": topic_state,
                "block": {"error_code": "window_attempt_limit", "blocked_at": "2026-11-08T04:00:00Z"}
                if topic_state == "blocked"
                else None,
            }
        if path.endswith("/processing/summary"):
            return {**value, "attempts": 1, "inputs_completed": 3, "created": 2, "updated": 4}
        return value

    original_project = DashboardAPI.project

    async def project(api, scope, operation, **params):
        value = await original_project(api, scope, operation, **params)
        if operation == "processing_summary":
            return {**value, "attempts": 1, "inputs_completed": 3, "created": 2, "updated": 4}
        return value

    monkeypatch.setattr(DashboardAPI, "project", project)
    monkeypatch.setattr(DashboardAPI, "read", read)
    page = dashboard.get("/dashboard/home", params={"scope": scope, "lang": "en"})
    assert page.status_code == 200, page.text
    processing = page.text.split('<section id="recent-processing"', 1)[1].split("</section>", 1)[0]
    assert "Recent processing results" in processing
    assert 'class="home-metric-cell"' not in processing
    if topic_state == "blocked":
        assert page.text.index('id="home-issues"') < page.text.index('id="home-summary"')
        assert page.text.index('id="home-issues"') < page.text.index('id="home-token-usage"')
        issues = page.text.split('id="home-issues"', 1)[1].split("</section>", 1)[0]
        assert "Topic Memory" in issues
        assert "Automatic retry limit reached" in issues
        destination = next(
            unescape(value) for value in re.findall(r'href="([^"]+)"', issues) if "record_type=attempts" in value
        )
        assert parse_qs(urlsplit(destination).query)["record_type"] == ["attempts"]
        opened = dashboard.get(destination)
        assert opened.status_code == 200, opened.text
        assert "Automatic processing" in opened.text
    else:
        assert 'id="home-issues"' not in page.text
    assert 'data-summary="failed"' not in processing
    assert 'data-summary="unconfirmed"' not in processing


def test_home_keeps_token_usage_and_links_to_resources(dashboard):
    scope = create_scope(dashboard, "Home token usage")["scope_id"]
    page = dashboard.get("/dashboard/home", params={"scope": scope, "lang": "zh"})
    assert page.status_code == 200
    section = page.text.split('id="home-token-usage"', 1)[1].split('id="usage"', 1)[0]
    assert "Token用量" in section
    links = [unescape(value) for value in re.findall(r'href="([^"]+)"', section) if "/dashboard/usage?" in value]
    assert links
    assert all(parse_qs(urlsplit(value).query).get("view") == ["resources"] for value in links)


def test_home_handoff_is_prominent_and_opens_the_displayed_revision(dashboard):
    scope = create_scope(dashboard, "Resume work")["scope_id"]
    empty = dashboard.get("/dashboard/home", params={"scope": scope})
    assert 'id="handoff"' not in empty.text
    reference = commit_handoff(dashboard, scope)
    page = dashboard.get("/dashboard/home", params={"scope": scope, "lang": "zh"})
    assert page.status_code == 200
    assert page.text.index('id="handoff"') < page.text.index('id="recent-processing"')
    card = page.text.split('id="handoff"', 1)[1].split("</section>", 1)[0]
    for text in (
        "Review the HTTP contract",
        "Review remains in progress.",
        "Continue the review.",
        "可继续",
        "打开交接",
    ):
        assert text in card
    target = next(unescape(href) for href in re.findall(r'href="([^"]+)"', card) if "handoff-detail?" in href)
    assert (
        parse_qs(urlsplit(target).query).items()
        >= {"artifact": [reference["artifact_id"]], "revision": [str(reference["revision"])], "scope": [scope]}.items()
    )
    detail = dashboard.get(target)
    assert detail.status_code == 200
    assert "Continue the review." in detail.text


def test_home_preparation_card_has_count_recent_details_and_all_link(dashboard):
    scope = create_scope(dashboard, "Preparation details")["scope_id"]
    prepared = dashboard.post("/v1/context/prepare", json={"scope_id": scope, "query": "No saved facts."})
    assert prepared.status_code == 200, prepared.text
    handoff = commit_handoff(dashboard, scope)
    for _ in range(4):
        delivery = dashboard.post(
            "/v1/context/bootstrap",
            json={
                "scope_id": scope,
                "enabled": True,
                "integration": "home-test",
                "lifecycle": "resume",
                "handoff": handoff,
            },
        )
        assert delivery.status_code == 200, delivery.text
    page = dashboard.get("/dashboard/home", params={"scope": scope, "lang": "zh"})
    assert page.status_code == 200
    assert page.text.index('id="home-token-usage"') < page.text.index('id="usage"')
    card = page.text.split('id="usage"', 1)[1].split("</section>", 1)[0]
    assert summary_value(card, "preparations") == "1"
    assert "召回数量" in card and "0 个" in card and "详情" in card and "查看全部" in card
    detail = next(unescape(href) for href in re.findall(r'href="([^"]+)"', card) if "usage_id=" in href)
    opened = dashboard.get(detail)
    assert opened.status_code == 200
    assert "usage_kind=preparation" in detail


def test_home_processing_ignores_period_for_its_summary(dashboard, monkeypatch):
    from powercontext.server.dashboard.api import DashboardAPI

    scope = create_scope(dashboard, "All processing history")["scope_id"]
    original = DashboardAPI.project
    summaries = []

    async def project(api, selected_scope, operation, **params):
        if operation == "processing_summary":
            summaries.append(params)
        return await original(api, selected_scope, operation, **params)

    monkeypatch.setattr(DashboardAPI, "project", project)
    for period in ("today", "7d", "30d"):
        page = dashboard.get("/dashboard/home", params={"scope": scope, "period": period, "lang": "en"})
        assert page.status_code == 200
        section = page.text.split('<section id="recent-processing"', 1)[1].split("</section>", 1)[0]
        assert not any(caption in section for caption in (">Today<", ">7 days<", ">30 days<"))
    assert summaries == [{"all_time": True}] * 3


def test_home_limits_previews_and_uses_the_full_token_chart(dashboard):
    scope = create_scope(dashboard, "Three row previews")["scope_id"]
    for index in range(4):
        saved = dashboard.post(
            "/v1/memory/remember", json={"scope_id": scope, "kind": "fact", "text": f"Deployment rule {index}."}
        )
        assert saved.status_code == 200
        prepared = dashboard.post("/v1/context/prepare", json={"scope_id": scope, "query": "Deployment"})
        assert prepared.status_code == 200
    home = dashboard.get("/dashboard/home", params={"scope": scope, "period": "7d", "lang": "en"})
    assert home.status_code == 200
    for identity in ("recent-processing", "notes", "usage"):
        section = home.text.split(f'id="{identity}"', 1)[1].split("</section>", 1)[0]
        bodies = re.findall(r"<tbody>(.*?)</tbody>", section, re.S)
        if identity == "recent-processing":
            assert len(bodies) <= 1
            assert not bodies or len(re.findall(r"<tr\b", bodies[0])) <= 3
        else:
            assert len(bodies) == 1
            assert len(re.findall(r"<tr\b", bodies[0])) == 3
        assert "<details" not in section
    assert home.text.index('id="home-summary"') < home.text.index('id="home-token-usage"')
    assert home.text.index('id="home-token-usage"') < home.text.index('id="recent-processing"')
    usage = dashboard.get(
        "/dashboard/usage", params={"scope": scope, "period": "7d", "view": "resources", "lang": "en"}
    )
    assert usage.status_code == 200
    for page in (home, usage):
        assert 'class="token-comparison"' in page.text
        assert 'data-compact="false"' in page.text
        assert 'data-compact="true"' not in page.text
