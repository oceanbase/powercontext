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

"""Persisted preparation, delivery and reported feedback remain distinct and authorized."""

import json
import re
import sqlite3
from datetime import UTC, datetime, timedelta
from html import unescape
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest
import time_machine

from powercontext.server.authz import PrincipalRef
from powercontext.server.dashboard.navigation import collection_return
from tests.test_dashboard import commit_handoff, create_scope
from tests.test_dashboard import dashboard as dashboard
from tests.test_dashboard_scope_selection import _grant


def prepare(client, scope):
    saved = client.post(
        "/v1/memory/remember",
        json={
            "scope_id": scope,
            "kind": "fact",
            "text": "Default deployment region is Shanghai.",
        },
    )
    assert saved.status_code == 200, saved.text
    prepared = client.post("/v1/context/prepare", json={"scope_id": scope, "query": "deployment", "max_bytes": 4000})
    assert prepared.status_code == 200, prepared.text
    assert prepared.json()["status"] == "ready"
    listing = client.get("/dashboard/usage", params={"scope": scope, "view": "records", "lang": "en"})
    assert listing.status_code == 200, listing.text
    url = next(unescape(value) for value in re.findall(r'href="([^"]+)"', listing.text) if "usage_id=" in value)
    return saved.json()["records"][0]["artifact"], url


def form_for(page, scope, url):
    params = parse_qs(urlsplit(url).query)
    match = re.search(r'name="feedback_id" value="([^"]+)"', page.text)
    assert match is not None, page.text
    return {
        "scope": scope,
        "feedback_id": match[1],
        "record_kind": params["usage_kind"][0],
        "record_id": params["usage_id"][0],
        "adoption": "yes",
        "helpful": "no",
        "reason": "Used the region, but the deployment check still failed.",
    }


def test_preparation_reader_keeps_exact_versions_without_storing_queries(dashboard, tmp_path):
    scope = create_scope(dashboard, "Usage history")["scope_id"]
    artifact, url = prepare(dashboard, scope)
    path = f"/v1/scopes/{scope}/artifacts/atomic-memory/{artifact['artifact_id']}"
    head = dashboard.get(path)
    revised = dashboard.put(
        path,
        headers={"If-Match": head.headers["etag"]},
        json={"content": {"kind": "fact", "text": "Default deployment region is Beijing."}},
    )
    assert revised.status_code == 200, revised.text
    page = dashboard.get(url)
    assert page.status_code == 200, page.text
    assert "Default deployment region is Shanghai." in page.text
    assert "Default deployment region is Beijing." not in page.text
    memory_url = next(
        unescape(value)
        for value in re.findall(r'href="([^"]+)"', page.text)
        if "/dashboard/notes?" in value and "artifact=" in value
    )
    assert parse_qs(urlsplit(memory_url).query)["revision"] == ["1"]
    memory_page = dashboard.get(memory_url)
    reading = memory_page.text.split('id="note-reading"', 1)[1].split("</article>", 1)[0]
    assert "Default deployment region is Shanghai." in reading
    assert "Default deployment region is Beijing." not in reading
    assert "Included in prepared context 1 times this period" in memory_page.text
    returned = next(
        unescape(value)
        for value in re.findall(r'href="([^"]+)"', memory_page.text)
        if "/dashboard/usage?" in value and "usage_id=" in value
    )
    assert parse_qs(urlsplit(returned).query) == parse_qs(urlsplit(url).query)
    assert "Back to usage records" in memory_page.text
    latest = dashboard.get(
        "/dashboard/notes",
        params={"scope": scope, "artifact": artifact["artifact_id"], "revision": 2, "lang": "en"},
    )
    assert latest.status_code == 200, latest.text
    assert "Included in prepared context 0 times this period" in latest.text
    with sqlite3.connect(tmp_path / "data.db") as connection:
        payload = json.loads(
            connection.execute("SELECT payload FROM pc_context_usage_records WHERE scope_id=?", (scope,)).fetchone()[0]
        )
    assert payload["items"][0]["artifact"] == artifact
    assert "query" not in payload and "content" not in payload
    assert "Shanghai" not in json.dumps(payload)


def test_feedback_is_reported_idempotent_and_scope_bound(dashboard, tmp_path):
    scope = create_scope(dashboard, "Reported feedback")["scope_id"]
    _, url = prepare(dashboard, scope)
    page = dashboard.get(url)
    assert page.status_code == 200, page.text
    form = form_for(page, scope, url)
    endpoint = "/dashboard/usage/feedback?lang=en"
    denied = dashboard.post(endpoint, data=form, headers={"Origin": "https://foreign.example"}, follow_redirects=False)
    assert denied.status_code == 403
    for _ in range(2):
        result = dashboard.post(endpoint, data=form, headers={"Origin": "http://testserver"}, follow_redirects=False)
        assert result.status_code == 303, result.text
    loaded = dashboard.get(result.headers["location"])
    assert loaded.status_code == 200, loaded.text
    assert form["reason"] in loaded.text and "User reported" in loaded.text
    with sqlite3.connect(tmp_path / "data.db") as connection:
        assert connection.execute(
            "SELECT count(*) FROM pc_context_usage_feedback WHERE scope_id=?", (scope,)
        ).fetchone() == (1,)
    conflict = dashboard.post(endpoint, data={**form, "helpful": "yes"}, headers={"Origin": "http://testserver"})
    assert conflict.status_code == 409, conflict.text
    other = create_scope(dashboard, "Different scope")["scope_id"]
    forged = dashboard.post(
        endpoint, data={**form, "scope": other, "feedback_id": uuid4().hex}, headers={"Origin": "http://testserver"}
    )
    assert forged.status_code == 404, forged.text
    assert form["reason"] not in forged.text


def test_editing_feedback_keeps_the_saved_reason_unless_cleared(dashboard, tmp_path):
    scope = create_scope(dashboard, "Edited feedback")["scope_id"]
    _, url = prepare(dashboard, scope)
    endpoint = "/dashboard/usage/feedback?lang=en"
    reported = form_for(dashboard.get(url), scope, url)
    saved = dashboard.post(endpoint, data=reported, headers={"Origin": "http://testserver"}, follow_redirects=False)
    assert saved.status_code == 303, saved.text

    editor = dashboard.get(url)
    assert editor.status_code == 200, editor.text
    prefilled = re.search(r'<textarea[^>]*name="reason"[^>]*>(.*?)</textarea>', editor.text, re.S)
    assert prefilled is not None, editor.text
    assert prefilled[1].strip() == reported["reason"]

    # Editing only the adoption keeps the reported explanation instead of silently clearing it.
    revised = {**form_for(editor, scope, url), "adoption": "no", "reason": prefilled[1].strip()}
    changed = dashboard.post(endpoint, data=revised, headers={"Origin": "http://testserver"}, follow_redirects=False)
    assert changed.status_code == 303, changed.text
    reopened = dashboard.get(url)
    assert reopened.status_code == 200, reopened.text
    assert reported["reason"] in reopened.text
    with sqlite3.connect(tmp_path / "data.db") as connection:
        rows = connection.execute(
            "SELECT json_extract(payload, '$.reason') FROM pc_context_usage_feedback"
            " WHERE scope_id=? ORDER BY created_at DESC, feedback_id DESC",
            (scope,),
        ).fetchall()
    assert [row[0] for row in rows] == [reported["reason"], reported["reason"]]

    # An explicit clear still works.
    cleared = dashboard.post(
        endpoint,
        data={**form_for(reopened, scope, url), "adoption": "no", "reason": ""},
        headers={"Origin": "http://testserver"},
        follow_redirects=False,
    )
    assert cleared.status_code == 303, cleared.text
    assert reported["reason"] not in dashboard.get(url).text


def test_viewer_cannot_submit_feedback_or_read_another_scope(dashboard, monkeypatch):
    scope = create_scope(dashboard, "Private preparation")["scope_id"]
    _, url = prepare(dashboard, scope)
    form = form_for(dashboard.get(url), scope, url)
    reader = {"type": "user", "id": "usage-reader"}
    _grant(dashboard, reader, {"type": "scope", "scope_id": scope}, "scope.viewer")
    authentication = dashboard.app.state.authentication_provider
    with monkeypatch.context() as patch:
        patch.setattr(authentication, "_principal", PrincipalRef(**reader))
        opened = dashboard.get(url)
        assert opened.status_code == 200, opened.text
        assert 'name="feedback_id"' not in opened.text
        denied = dashboard.post("/dashboard/usage/feedback", data=form, headers={"Origin": "http://testserver"})
        assert denied.status_code == 403, denied.text
    other = create_scope(dashboard, "Unshared")["scope_id"]
    with monkeypatch.context() as patch:
        patch.setattr(authentication, "_principal", PrincipalRef(**reader))
        denied = dashboard.get("/dashboard/usage", params={"scope": other, "view": "records"})
        assert denied.status_code == 403
        assert "Default deployment region" not in denied.text


def test_delivery_and_handoff_outcome_follow_exact_saved_records(dashboard):
    scope = create_scope(dashboard, "Work delivery")["scope_id"]
    handoff = commit_handoff(dashboard, scope)
    bootstrap = dashboard.post(
        "/v1/context/bootstrap",
        json={
            "scope_id": scope,
            "enabled": True,
            "integration": "dashboard-test",
            "lifecycle": "resume",
            "handoff": handoff,
        },
    )
    assert bootstrap.status_code == 200, bootstrap.text
    receipt = bootstrap.json()["receipt"]["receipt_id"]
    query = {"scope": scope, "view": "records", "usage_kind": "bootstrap", "usage_id": receipt, "lang": "en"}
    pending = dashboard.get("/dashboard/usage", params=query)
    assert pending.status_code == 200, pending.text
    assert "Delivery unconfirmed" in pending.text
    delivery = dashboard.post(
        "/v1/context/bootstrap/receipts", json={"scope_id": scope, "receipt_id": receipt, "outcome": "injected"}
    )
    assert delivery.status_code == 200, delivery.text
    delivered = dashboard.get("/dashboard/usage", params=query)
    assert "Delivered" in delivered.text and "does not establish adoption" in delivered.text
    handoff_url = next(
        unescape(value)
        for value in re.findall(r'href="([^"]+)"', delivered.text)
        if "/dashboard/handoff-detail?" in value
    )
    handoff_page = dashboard.get(handoff_url)
    assert handoff_page.status_code == 200, handoff_page.text
    assert "Back to usage records" in handoff_page.text
    return_url = next(
        unescape(value)
        for value in re.findall(r'href="([^"]+)"', handoff_page.text)
        if "/dashboard/usage?" in value and "usage_id=" in value
    )
    returned = dashboard.get(return_url)
    assert returned.status_code == 200, returned.text
    assert returned.url.params["usage_id"] == receipt
    accepted = dashboard.post(
        "/v1/work/handoffs/acknowledge",
        json={
            "scope_id": scope,
            "source_id": "receiver",
            "receiver": dashboard.app.state.authentication_provider._principal.id,
            "status": "accepted",
            "selection": "exact",
            "revision": handoff,
            "receiver_checks": {"live_state": "confirmed", "capability": "confirmed", "authorization": "confirmed"},
            "message": "I checked the current state and accepted this handoff.",
        },
    )
    assert accepted.status_code == 200, accepted.text
    outcome = dashboard.post(
        "/v1/work/outcomes/record",
        json={
            "scope_id": scope,
            "source_id": "result",
            "outcome": {
                "schema": "powercontext.task-outcome.v1",
                "trust": "untrusted_observation",
                "objective": "Review the HTTP contract",
                "status": "succeeded",
                "summary": "The contract review is complete.",
                "observations": [{"text": "The contract was checked.", "basis": "declared", "evidence": []}],
                "checks": [],
                "produced_artifacts": [],
                "remaining_work": [],
                "handoff_receipt_ref": accepted.json()["receipt"]["source"],
            },
        },
    )
    assert outcome.status_code == 202, outcome.text
    page = dashboard.get(
        "/dashboard/handoff-detail",
        params={"scope": scope, "artifact": handoff["artifact_id"], "revision": handoff["revision"], "lang": "en"},
    )
    assert page.status_code == 200, page.text
    assert "I checked the current state" in page.text and "The contract review is complete." in page.text
    assert "Reported task outcome" in page.text


def test_memory_detail_reports_period_usage(dashboard):
    scope = create_scope(dashboard, "Memory usage")["scope_id"]
    artifact, _ = prepare(dashboard, scope)
    unused = dashboard.post(
        "/v1/memory/remember",
        json={"scope_id": scope, "kind": "fact", "text": "Lunch is at noon."},
    ).json()["records"][0]["artifact"]
    used = dashboard.get(
        "/dashboard/notes",
        params={
            "scope": scope,
            "artifact": artifact["artifact_id"],
            "revision": artifact["revision"],
            "lang": "en",
        },
    )
    assert used.status_code == 200, used.text
    assert "Included in prepared context 1 times this period" in used.text
    assert "Last used" in used.text
    never = dashboard.get(
        "/dashboard/notes",
        params={"scope": scope, "artifact": unused["artifact_id"], "revision": unused["revision"], "lang": "en"},
    )
    assert never.status_code == 200, never.text
    assert "Included in prepared context 0 times this period" in never.text
    assert "Last used" not in never.text


def test_memory_detail_counts_preparations_from_a_referencing_scope(dashboard):
    shared = create_scope(dashboard, "Shared memory usage")["scope_id"]
    created = dashboard.post(
        "/v1/scopes",
        json={
            "title": "Referencing preparation",
            "summary": "Uses shared memory",
            "idempotency_key": "referencing-preparation",
            "context_references": [shared],
        },
    )
    assert created.status_code == 201, created.text
    reader = created.json()["scope_id"]
    saved = dashboard.post(
        "/v1/memory/remember",
        json={"scope_id": shared, "kind": "fact", "text": "Default deployment region is Shanghai."},
    )
    assert saved.status_code == 200, saved.text
    artifact = saved.json()["records"][0]["artifact"]
    prepared = dashboard.post("/v1/context/prepare", json={"scope_id": reader, "query": "deployment"})
    assert prepared.status_code == 200, prepared.text
    assert "Shanghai" in prepared.json()["content"]
    page = dashboard.get(
        "/dashboard/notes",
        params={"scope": shared, "artifact": artifact["artifact_id"], "revision": artifact["revision"], "lang": "en"},
    )
    assert page.status_code == 200, page.text
    assert "Included in prepared context 1 times this period" in page.text
    assert "Last used" in page.text


def test_cross_scope_usage_count_requires_read_access_to_the_whole_preparation(dashboard, monkeypatch):
    shared = create_scope(dashboard, "Visible deployment memory")["scope_id"]
    private = create_scope(dashboard, "Private deployment memory")["scope_id"]
    created = dashboard.post(
        "/v1/scopes",
        json={
            "title": "Private preparation owner",
            "summary": "Includes two Scopes",
            "idempotency_key": "private-preparation-owner",
            "context_references": [shared, private],
        },
    )
    assert created.status_code == 201, created.text
    owner = created.json()["scope_id"]
    artifacts = []
    for scope, text in ((shared, "Shanghai deployment region."), (private, "Private deployment policy.")):
        saved = dashboard.post("/v1/memory/remember", json={"scope_id": scope, "kind": "fact", "text": text})
        assert saved.status_code == 200, saved.text
        artifacts.append(saved.json()["records"][0]["artifact"])
    prepared = dashboard.post("/v1/context/prepare", json={"scope_id": owner, "query": "deployment"})
    assert prepared.status_code == 200, prepared.text
    assert "Shanghai" in prepared.json()["content"] and "Private deployment policy" in prepared.json()["content"]
    viewer = {"type": "user", "id": "cross-scope-usage-reader"}
    authentication = dashboard.app.state.authentication_provider
    params = {"scope": shared, "artifact": artifacts[0]["artifact_id"], "revision": 1, "lang": "en"}
    for scope, count in ((shared, 0), (owner, 0), (private, 1)):
        grant = dashboard.post(
            "/v1/access/bindings/create",
            json={
                "subject": viewer,
                "resource": {"type": "scope", "scope_id": scope},
                "role": "scope.viewer",
                "idempotency_key": f"cross-scope-reader-{scope}",
            },
        )
        assert grant.status_code == 201, grant.text
        with monkeypatch.context() as patch:
            patch.setattr(authentication, "_principal", PrincipalRef(**viewer))
            page = dashboard.get("/dashboard/notes", params=params)
            assert page.status_code == 200, page.text
            assert f"Included in prepared context {count} times this period" in page.text
            assert ("Last used" in page.text) == (count == 1)
            assert "Private deployment policy" not in page.text


def test_processing_summary_distinguishes_delivery_and_latest_adoption_report(dashboard):
    scope = create_scope(dashboard, "Recorded use counts")["scope_id"]
    _, url = prepare(dashboard, scope)
    handoff = commit_handoff(dashboard, scope)
    receipts = []
    for event in ("pending", "failed", "injected"):
        response = dashboard.post(
            "/v1/context/bootstrap",
            json={
                "scope_id": scope,
                "enabled": True,
                "integration": "dashboard-test",
                "lifecycle": "resume",
                "event_id": event,
                "handoff": handoff,
            },
        )
        assert response.status_code == 200, response.text
        receipts.append(response.json()["receipt"]["receipt_id"])

    path = f"/v1/scopes/{scope}/processing/summary"
    pending = dashboard.get(path).json()
    assert (pending["deliveries"], pending["adopted"], pending["unreported"]) == (0, 0, 4)
    for receipt, outcome in zip(receipts[1:], ("failed", "injected"), strict=True):
        response = dashboard.post(
            "/v1/context/bootstrap/receipts", json={"scope_id": scope, "receipt_id": receipt, "outcome": outcome}
        )
        assert response.status_code == 200, response.text

    for adoption, adopted, unknown in (("yes", 1, 3), ("yes", 1, 3), ("no", 0, 3), ("unknown", 0, 4)):
        form = {**form_for(dashboard.get(url), scope, url), "feedback_id": uuid4().hex, "adoption": adoption}
        response = dashboard.post(
            "/dashboard/usage/feedback", data=form, headers={"Origin": "http://testserver"}, follow_redirects=False
        )
        assert response.status_code == 303, response.text
        summary = dashboard.get(path).json()
        assert (summary["deliveries"], summary["adopted"], summary["unreported"]) == (1, adopted, unknown)
    other = create_scope(dashboard, "Separate usage counts")["scope_id"]
    isolated = dashboard.get(f"/v1/scopes/{other}/processing/summary").json()
    assert (isolated["deliveries"], isolated["adopted"], isolated["unreported"]) == (0, 0, 0)


def test_processing_summary_uses_record_window_and_feedback_before_its_end(dashboard):
    scope = create_scope(dashboard, "Usage reporting window")["scope_id"]
    now = datetime.now(UTC)
    with time_machine.travel(now - timedelta(days=10), tick=False):
        _, old_url = prepare(dashboard, scope)
    _, current_url = prepare(dashboard, scope)
    endpoint = "/dashboard/usage/feedback"
    for url in (old_url, current_url):
        result = dashboard.post(
            endpoint,
            data=form_for(dashboard.get(url), scope, url),
            headers={"Origin": "http://testserver"},
            follow_redirects=False,
        )
        assert result.status_code == 303, result.text
    until = datetime.now(UTC) + timedelta(seconds=1)
    with time_machine.travel(until + timedelta(days=1), tick=False):
        form = {**form_for(dashboard.get(current_url), scope, current_url), "adoption": "no"}
        changed = dashboard.post(endpoint, data=form, headers={"Origin": "http://testserver"}, follow_redirects=False)
        assert changed.status_code == 303, changed.text
    path = f"/v1/scopes/{scope}/processing/summary"
    window = {"since": (now - timedelta(days=1)).isoformat(), "until": until.isoformat()}
    summary = dashboard.get(path, params=window).json()
    assert (summary["adopted"], summary["unreported"]) == (1, 0)
    latest = dashboard.get(path, params={**window, "until": (until + timedelta(days=2)).isoformat()}).json()
    assert (latest["adopted"], latest["unreported"]) == (0, 0)


@pytest.mark.parametrize(
    "destination",
    [
        "https://foreign.example/dashboard/usage?scope=S&view=records",
        "//foreign.example/dashboard/usage?scope=S&view=records",
        "/dashboard/usage?scope=S&scope=other&view=records",
        "/dashboard/usage?scope=S&view=records&usage_id=I",
        "/dashboard/usage?scope=S&view=records&usage_id=I&usage_kind=other",
        "/dashboard/usage?scope=S&view=records&next=https://foreign.example",
        "/dashboard/usage?scope=S&view=records&usage_history=%5B1%5D",
    ],
)
def test_usage_return_rejects_external_and_invalid_reading_context(destination):
    assert collection_return(destination, "S", page="handoff") is None


def test_usage_return_preserves_origin_scope_for_cross_scope_content():
    destination = "/dashboard/usage?scope=origin&view=records&usage_id=I&usage_kind=preparation&usage_cursor=C"
    returned = collection_return(destination, "referenced-scope", page="methods")
    assert returned is not None
    assert parse_qs(urlsplit(returned).query) == parse_qs(urlsplit(destination).query)


def test_sign_in_preserves_the_exact_usage_record_return(dashboard):
    scope = create_scope(dashboard, "Usage reading recovery")["scope_id"]
    _, url = prepare(dashboard, scope)
    usage = dashboard.get(url)
    memory_url = next(
        unescape(value)
        for value in re.findall(r'href="([^"]+)"', usage.text)
        if "/dashboard/notes?" in value and "artifact=" in value
    )
    token = dashboard.headers.pop("Authorization").removeprefix("Bearer ")
    login = dashboard.get(memory_url)
    assert login.status_code == 401, login.text
    next_field = re.search(r'name="next" value="([^"]+)"', login.text)
    assert next_field is not None
    destination = unescape(next_field[1])
    returned = parse_qs(urlsplit(destination).query)["return_to"][0]
    assert parse_qs(urlsplit(returned).query) == parse_qs(urlsplit(url).query)
    resumed = dashboard.post(
        "/dashboard/session", data={"token": token, "next": destination}, headers={"Origin": "http://testserver"}
    )
    assert resumed.status_code == 200, resumed.text
    assert "Back to usage records" in resumed.text and "Shanghai" in resumed.text


def test_preparation_detail_keeps_feedback_optional_and_delivery_notice_scoped(dashboard):
    scope = create_scope(dashboard, "Readable usage detail")["scope_id"]
    _, url = prepare(dashboard, scope)
    page = dashboard.get(url)
    assert page.status_code == 200
    assert "Default deployment region is Shanghai." in page.text
    editor = re.search(r'<details class="usage-feedback-editor"([^>]*)>(.*?)</details>', page.text, re.S)
    assert editor is not None
    assert "open" not in editor[1]
    assert 'name="feedback_id"' in editor[2]
    assert "does not establish adoption" not in page.text
    assert "No use feedback reported." in page.text


def test_usage_list_count_links_to_content_at_its_recorded_revision(dashboard):
    scope = create_scope(dashboard, "Distinguishable usage records")["scope_id"]
    artifact, url = prepare(dashboard, scope)
    path = f"/v1/scopes/{scope}/artifacts/atomic-memory/{artifact['artifact_id']}"
    head = dashboard.get(path)
    revised = dashboard.put(
        path,
        headers={"If-Match": head.headers["etag"]},
        json={"content": {"kind": "fact", "text": "The current region is Beijing."}},
    )
    assert revised.status_code == 200
    listing = dashboard.get("/dashboard/usage", params={"scope": scope, "view": "records", "lang": "en"})
    assert listing.status_code == 200
    assert "Recalled items" in listing.text
    assert "1 items" in listing.text
    assert "Default deployment region is Shanghai." not in listing.text
    assert "The current region is Beijing." not in listing.text
    detail = dashboard.get(url)
    assert detail.status_code == 200
    assert "Default deployment region is Shanghai." in detail.text
    assert "The current region is Beijing." not in detail.text


def test_usage_count_list_remains_available_when_content_is_unreadable(dashboard, monkeypatch):
    from powercontext.server.dashboard.api import DashboardAPI
    from powercontext.server.dashboard.errors import ReadError

    scope = create_scope(dashboard, "Unreadable usage preview")["scope_id"]
    _, url = prepare(dashboard, scope)

    async def denied(*args, **kwargs):
        raise ReadError(403, "forbidden")

    monkeypatch.setattr(DashboardAPI, "artifact_revision", denied)
    listing = dashboard.get("/dashboard/usage", params={"scope": scope, "view": "records", "lang": "en"})
    assert listing.status_code == 200
    assert "This content cannot be read." not in listing.text
    assert "1 items" in listing.text
    detail = dashboard.get(url)
    assert "You do not have access to this content." in detail.text
    assert "Default deployment region is Shanghai." not in listing.text
    assert "usage_id=" in listing.text


def test_usage_list_explains_an_empty_preparation(dashboard):
    scope = create_scope(dashboard, "Empty preparation")["scope_id"]
    prepared = dashboard.post("/v1/context/prepare", json={"scope_id": scope, "query": "No saved facts exist."})
    assert prepared.status_code == 200
    listing = dashboard.get("/dashboard/usage", params={"scope": scope, "view": "records", "lang": "en"})
    assert listing.status_code == 200
    assert "No context prepared" in listing.text
    assert "0 items" in listing.text
