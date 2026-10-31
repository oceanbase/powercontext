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

"""Global recall ranking through the public list and Dashboard boundaries."""

import re
import sqlite3
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import time_machine

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.context_usage import ContextUsageItem, ContextUsageRecord
from powercontext.server.authz import PrincipalRef
from tests.test_dashboard import create_scope
from tests.test_dashboard import dashboard as dashboard
from tests.test_dashboard_scope_selection import _grant


def remember(client, scope, text):
    response = client.post("/v1/memory/remember", json={"scope_id": scope, "kind": "fact", "text": text})
    assert response.status_code == 200, response.text
    return response.json()["records"][0]["artifact"]


def record(tmp_path, scope, refs, *, created_at=None):
    value = ContextUsageRecord(
        usage_id=uuid4().hex,
        scope_id=scope,
        created_at=created_at or datetime.now(UTC) - timedelta(seconds=1),
        status="ready",
        content_bytes=100,
        items=tuple(
            ContextUsageItem(scope_id=origin, artifact=ArtifactRef.model_validate(ref)) for origin, ref in refs
        ),
    )
    with sqlite3.connect(tmp_path / "data.db") as connection:
        connection.execute(
            "INSERT INTO pc_context_usage_records (scope_id,usage_id,created_at,payload) VALUES (?,?,?,?)",
            (
                scope,
                value.usage_id,
                value.created_at.replace(tzinfo=None).isoformat(sep=" "),
                value.model_dump_json().encode(),
            ),
        )


def listing(client, scope, **kwargs):
    response = client.post("/v1/atomic-memory/list", json={"scope_id": scope, "sort_by": "recall_count", **kwargs})
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize("public_reads", [False, True])
def test_dashboard_ranks_all_memories_before_its_first_page(dashboard, tmp_path, monkeypatch, public_reads):
    scope = create_scope(dashboard, "Global recall order")["scope_id"]
    refs = sorted(
        [remember(dashboard, scope, f"Fact {index}.") for index in range(8)], key=lambda ref: ref["artifact_id"]
    )
    hot = refs[-1]
    for _ in range(3):
        record(tmp_path, scope, [(scope, hot)])
    record(tmp_path, scope, [(scope, refs[-2])])
    if public_reads:
        monkeypatch.setattr("powercontext.server.dashboard.api._builtin_runtime", lambda request: None)
    response = dashboard.get("/dashboard/notes", params={"scope": scope, "period": "30d", "lang": "en"})
    assert response.status_code == 200, response.text
    ids = re.findall(r'class="[^"]*note-selector[^>]*href="[^"]*artifact=([^&"]+)', response.text)
    assert ids == [hot["artifact_id"], refs[-2]["artifact_id"], *[ref["artifact_id"] for ref in refs[:4]]]
    assert "Recalled 3 times" in response.text
    assert "Most recalled" in response.text


def test_ranked_pagination_counts_revisions_once_and_keeps_zero_items(dashboard, tmp_path):
    scope = create_scope(dashboard, "Recall pages")["scope_id"]
    refs = sorted(
        [remember(dashboard, scope, f"Fact {index}.") for index in range(5)], key=lambda ref: ref["artifact_id"]
    )
    hot = refs[-1]
    path = f"/v1/scopes/{scope}/artifacts/atomic-memory/{hot['artifact_id']}"
    head = dashboard.get(path)
    assert (
        dashboard.put(
            path,
            headers={"If-Match": head.headers["etag"]},
            json={"content": {"kind": "fact", "text": "Updated hot fact."}},
        ).status_code
        == 200
    )
    new = {**hot, "revision": 2}
    record(tmp_path, scope, [(scope, hot), (scope, new)])
    record(tmp_path, scope, [(scope, hot)])
    record(tmp_path, scope, [(scope, refs[-2])])
    page = listing(dashboard, scope, limit=2)
    assert [item["artifact"]["artifact_id"] for item in page["items"]] == [hot["artifact_id"], refs[-2]["artifact_id"]]
    assert page["items"][0]["artifact"]["revision"] == 2
    assert page["recall_counts"] == {hot["artifact_id"]: 2, refs[-2]["artifact_id"]: 1}
    ids = [item["artifact"]["artifact_id"] for item in page["items"]]
    cursor = page["next_cursor"]
    assert (
        dashboard.post(
            "/v1/atomic-memory/list",
            json={"scope_id": scope, "sort_by": "recall_count", "period": "30d", "cursor": cursor},
        ).status_code
        == 400
    )
    assert dashboard.post("/v1/atomic-memory/list", json={"scope_id": scope, "cursor": cursor}).status_code == 400
    while cursor:
        page = listing(dashboard, scope, limit=2, cursor=cursor)
        assert set(page["recall_counts"].values()) == {0}
        ids.extend(item["artifact"]["artifact_id"] for item in page["items"])
        cursor = page["next_cursor"]
    assert ids == [hot["artifact_id"], refs[-2]["artifact_id"], *[ref["artifact_id"] for ref in refs[:3]]]


def test_ranking_binds_period_and_first_page_cutoff(dashboard, tmp_path):
    scope = create_scope(dashboard, "Recall window")["scope_id"]
    refs = sorted(
        [remember(dashboard, scope, f"Window fact {index}.") for index in range(3)], key=lambda ref: ref["artifact_id"]
    )
    now = datetime(2026, 11, 8, 12, tzinfo=UTC)
    for _ in range(2):
        record(tmp_path, scope, [(scope, refs[-1])], created_at=now - timedelta(days=8))
    with time_machine.travel(now, tick=False):
        today = listing(dashboard, scope, period="today", limit=1)
        assert today["recall_counts"] == {refs[0]["artifact_id"]: 0}
        month = listing(dashboard, scope, period="30d", limit=1)
        assert month["recall_counts"] == {refs[-1]["artifact_id"]: 2}
    for _ in range(4):
        record(tmp_path, scope, [(scope, refs[1])], created_at=now + timedelta(seconds=1))
    with time_machine.travel(now + timedelta(seconds=2), tick=False):
        following = listing(dashboard, scope, period="today", limit=1, cursor=today["next_cursor"])
        assert following["recall_counts"] == {refs[1]["artifact_id"]: 0}
        fresh = listing(dashboard, scope, period="today", limit=1)
        assert fresh["recall_counts"] == {refs[1]["artifact_id"]: 4}


def test_ranking_excludes_unreadable_preparations(dashboard, tmp_path, monkeypatch):
    scope = create_scope(dashboard, "Visible memory")["scope_id"]
    foreign = create_scope(dashboard, "Private preparation")["scope_id"]
    ref = remember(dashboard, scope, "Visible fact.")
    secret = remember(dashboard, foreign, "Private fact.")
    record(tmp_path, foreign, [(scope, ref)])
    record(tmp_path, scope, [(scope, ref), (foreign, secret)])
    record(tmp_path, scope, [(scope, ref)])
    reader = {"type": "user", "id": "ranking-reader"}
    _grant(dashboard, reader, {"type": "scope", "scope_id": scope}, "scope.viewer")
    monkeypatch.setattr(dashboard.app.state.authentication_provider, "_principal", PrincipalRef(**reader))
    page = listing(dashboard, scope)
    assert page["recall_counts"] == {ref["artifact_id"]: 1}
    monkeypatch.undo()
    granted = dashboard.post(
        "/v1/access/bindings/create",
        json={
            "subject": reader,
            "resource": {"type": "scope", "scope_id": foreign},
            "role": "scope.viewer",
            "idempotency_key": uuid4().hex,
        },
    )
    assert granted.status_code == 201, granted.text
    monkeypatch.setattr(dashboard.app.state.authentication_provider, "_principal", PrincipalRef(**reader))
    assert listing(dashboard, scope)["recall_counts"] == {ref["artifact_id"]: 3}


def test_recall_sort_metadata_workload_sample(dashboard, tmp_path, capsys):
    from time import perf_counter

    from powercontext.builtin.artifacts.atomic_memory import AtomicMemoryContent

    scope = create_scope(dashboard, "Recall ranking scale")["scope_id"]
    atomic = dashboard.app.state.application.atomic_memory.for_scope(scope)
    values = tuple(
        AtomicMemoryContent(kind="fact", text=f"Scale fact {index}.", description=f"Fact {index}")
        for index in range(1000)
    )
    created = dashboard.portal.call(atomic.create, values)
    hot = created.records[-1].ref.model_dump(mode="json")
    for _ in range(200):
        record(tmp_path, scope, [(scope, hot)])
    started = perf_counter()
    page = listing(dashboard, scope, limit=6)
    elapsed = perf_counter() - started
    assert page["items"][0]["artifact"]["artifact_id"] == hot["artifact_id"]
    assert page["recall_counts"][hot["artifact_id"]] == 200
    assert len(page["items"]) == 6
    with capsys.disabled():
        print(f"\nrecall ranking sample: 1000 current memories, 200 preparations, 6 returned, {elapsed:.3f}s")


@pytest.mark.parametrize("sort_by", ["identity", "recall_count"])
@pytest.mark.parametrize("query", [None, "deployment"])
@pytest.mark.parametrize("public_reads", [False, True])
def test_note_counts_remain_visible_in_every_list_mode(dashboard, tmp_path, monkeypatch, sort_by, query, public_reads):
    scope = create_scope(dashboard, "Consistent recall metadata")["scope_id"]
    hot = remember(dashboard, scope, "Deployment uses the Shanghai region.")
    remember(dashboard, scope, "Deployment uses a manual approval.")
    record(tmp_path, scope, [(scope, hot)])
    if public_reads:
        monkeypatch.setattr("powercontext.server.dashboard.api._builtin_runtime", lambda request: None)
    params = {"scope": scope, "period": "today", "note_sort": sort_by, "lang": "en"}
    if query:
        params.update(q=query, mode="fts")
    page = dashboard.get("/dashboard/notes", params=params)
    assert page.status_code == 200, page.text
    rows = re.findall(r'<a\b[^>]*class="[^"]*note-selector[^>]*>(.*?)</a>', page.text, re.S)
    assert len(rows) == 2
    assert all("Recalled " in row and " times" in row for row in rows)
    assert sum("Recalled 1 times" in row for row in rows) == 1
    assert sum("Recalled 0 times" in row for row in rows) == 1
    choices = re.findall(r'<a class="dropdown-item[^>]*>(.*?)</a>', page.text, re.S)
    assert not any(label in ["Today", "7 days", "30 days"] for label in choices)


def test_count_lookup_is_bounded_to_requested_identities(dashboard, tmp_path):
    scope = create_scope(dashboard, "Page count lookup")["scope_id"]
    refs = [remember(dashboard, scope, f"Lookup fact {index}.") for index in range(3)]
    record(tmp_path, scope, [(scope, refs[-1])])
    for mode in ["identity", "recall_count"]:
        response = dashboard.post(
            "/v1/atomic-memory/list",
            json={
                "scope_id": scope,
                "artifact_ids": [refs[-1]["artifact_id"], refs[0]["artifact_id"]],
                "sort_by": mode,
                "period": "today",
            },
        )
        assert response.status_code == 200, response.text
        value = response.json()
        assert {item["artifact"]["artifact_id"] for item in value["items"]} == {
            refs[-1]["artifact_id"],
            refs[0]["artifact_id"],
        }
        if mode == "recall_count":
            assert value["recall_counts"] == {refs[-1]["artifact_id"]: 1, refs[0]["artifact_id"]: 0}


def test_count_failure_keeps_metadata_line_and_does_not_claim_zero(dashboard, monkeypatch):
    from powercontext.server.dashboard.api import DashboardAPI
    from powercontext.server.dashboard.errors import ReadError

    scope = create_scope(dashboard, "Unavailable count")["scope_id"]
    remember(dashboard, scope, "Saved deployment preference.")
    original = DashboardAPI.atomic_memory_list

    async def read(api, *args, **kwargs):
        if kwargs.get("artifact_ids"):
            raise ReadError(503, "service_unavailable")
        return await original(api, *args, **kwargs)

    monkeypatch.setattr(DashboardAPI, "atomic_memory_list", read)
    page = dashboard.get("/dashboard/notes", params={"scope": scope, "note_sort": "identity", "lang": "en"})
    assert page.status_code == 200
    rows = re.findall(r'<a\b[^>]*class="[^"]*note-selector[^>]*>(.*?)</a>', page.text, re.S)
    assert len(rows) == 1
    assert "Recall count unavailable" in rows[0]
    assert "Recalled 0 times" not in rows[0]


def test_inactive_memory_counts_are_visible_in_both_orders(dashboard, tmp_path):
    scope = create_scope(dashboard, "Forgotten memory metadata")["scope_id"]
    ref = remember(dashboard, scope, "A retired deployment choice.")
    record(tmp_path, scope, [(scope, ref)])
    changed = dashboard.post(
        "/v1/atomic-memory/lifecycle",
        json={"scope_id": scope, "target": {"artifact": ref, "state_version": 0}, "state": "forgotten"},
    )
    assert changed.status_code == 200, changed.text
    for order in ["identity", "recall_count"]:
        page = dashboard.get(
            "/dashboard/notes", params={"scope": scope, "note_state": "forgotten", "note_sort": order, "lang": "en"}
        )
        assert page.status_code == 200, page.text
        rows = re.findall(r'<a\b[^>]*class="[^"]*note-selector[^>]*>(.*?)</a>', page.text, re.S)
        assert len(rows) == 1
        assert "Recalled 1 times" in rows[0]
