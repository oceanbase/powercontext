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

"""Inspect controlled durable operation records through the actual Dashboard."""

import asyncio
import re
from datetime import UTC, datetime, timedelta
from html import unescape
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy import inspect, select

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.authz import PrincipalRef
from powercontext.builtin.catalog_changes.models import CatalogChangeProposal
from powercontext.builtin.dream.models import CreateDreamRunRequest, DreamRecord, DreamRun
from powercontext.builtin.evidence.models import EvidenceManifest
from powercontext.builtin.persistence.dream import DreamRepository
from powercontext.builtin.persistence.tables import SHARED_METADATA
from powercontext.builtin.tags import ArtifactTagTarget
from powercontext.server.context import bind_principal, reset_principal
from powercontext.server.dashboard.api import DashboardAPI
from powercontext.server.dashboard.errors import ReadError
from powercontext.server.dashboard.operation_outcomes import DETAIL_CONCURRENCY
from powercontext.server.dashboard.preferences import CATALOGS
from powercontext.sources import SourceRef
from tests.test_dashboard import capture_source, commit_handoff, create_experience, create_scope
from tests.test_dashboard import dashboard as dashboard
from tests.test_dashboard_review_reading import propose
from tests.test_dashboard_scope_selection import _grant, _revoke


@pytest.fixture
def expire_inspection(monkeypatch):
    """Use real timeout cancellation once the requested read boundary is reached."""
    deadline = None
    active_reads = 0

    def controlled_timeout(_seconds):
        nonlocal deadline
        deadline = asyncio.timeout(None)
        return deadline

    async def expire(*, after_entries=1):
        nonlocal active_reads
        assert deadline is not None
        active_reads += 1
        try:
            if active_reads == after_entries:
                deadline.reschedule(asyncio.get_running_loop().time())
            await asyncio.Future()
        finally:
            active_reads -= 1

    monkeypatch.setattr("powercontext.server.dashboard.operation_outcomes.timeout", controlled_timeout)
    yield expire
    assert deadline is not None and deadline.expired(), "the intended inspection timeout was not exercised"
    assert active_reads == 0, "a dependent read survived inspection cancellation"


def saved_memory(client, scope):
    response = client.post(
        "/v1/memory/remember", json={"scope_id": scope, "kind": "fact", "text": "Saved independent content."}
    )
    assert response.status_code == 200, response.text
    return response.json()["records"][0]["artifact"]


def retain_run(client, scope, name, *, operation="refine_experience", target=None, candidate=None, **values):
    """Seed retained evidence without calling a generator or scheduling work."""
    baseline = ArtifactRef.model_validate(target) if target else None
    tag_target = values.pop("tag_target", None)
    request = CreateDreamRunRequest(
        operation=operation,
        artifacts=(baseline or ArtifactRef(family="experience", artifact_id="fixture-evidence", revision=1),),
        target=baseline,
        tag_target=tag_target,
        sources=(SourceRef(source_type="content", source_id="retained-evidence"),),
        idempotency_key=name,
    )
    run = DreamRun(
        scope_id=scope,
        run_id=name,
        operation=operation,
        target=baseline,
        tag_target=tag_target,
        candidate=candidate,
        accepted_at=datetime(2026, 11, 1, tzinfo=UTC) + timedelta(seconds=int(values.pop("order", 0))),
        **values,
    )

    async def store():
        async with client.app.state.application.primary_database.transaction() as connection:
            await DreamRepository().create(connection, DreamRecord(run=run, request=request, principal_id="fixture"))

    client.portal.call(store)
    return run


VOID_ELEMENTS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}


class _Element(HTMLParser):
    """Collect one element's own markup so assertions do not depend on surrounding layout."""

    def __init__(self, match):
        super().__init__(convert_charrefs=False)
        self._match = match
        self._depth = 0
        self.done = False
        self.parts: list[str] = []

    def _tag_text(self) -> str:
        return self.get_starttag_text() or ""

    def handle_starttag(self, tag, attrs):
        if self.done:
            return
        if self._depth == 0:
            if not self._match(tag, dict(attrs)):
                return
            self._depth = 1
        elif tag not in VOID_ELEMENTS:
            self._depth += 1
        self.parts.append(self._tag_text())

    def handle_startendtag(self, tag, attrs):
        if self.done:
            return
        if self._depth == 0:
            if self._match(tag, dict(attrs)):
                self.parts.append(self._tag_text())
                self.done = True
            return
        self.parts.append(self._tag_text())

    def handle_endtag(self, tag):
        if self.done or self._depth == 0:
            return
        self._depth -= 1
        self.parts.append(f"</{tag}>")
        if self._depth == 0:
            self.done = True

    def handle_data(self, data):
        if not self.done and self._depth:
            self.parts.append(data)

    def handle_comment(self, data):
        if not self.done and self._depth:
            self.parts.append(f"<!--{data}-->")


def _element(response, match, description):
    assert response.status_code == 200, response.text
    parser = _Element(match)
    parser.feed(response.text)
    parser.close()
    assert parser.done, f"{description} not found"
    return "".join(parser.parts)


def outcome_section(response):
    return "\n".join((
        _element(
            response,
            lambda tag, attrs: tag == "section" and "data-operation-outcomes" in attrs,
            "operation outcomes section",
        ),
        _element(
            response,
            lambda tag, attrs: tag == "div" and attrs.get("id") == "operation-outcomes-details",
            "operation outcomes drawer",
        ),
    ))


def drawer_section(response):
    return _element(
        response,
        lambda tag, attrs: tag == "div" and attrs.get("id") == "operation-outcomes-details",
        "operation outcomes drawer",
    )


def run_section(response, run_id):
    return _element(
        response,
        lambda tag, attrs: attrs.get("data-operation-run") == run_id,
        f"operation run {run_id}",
    )


@pytest.mark.parametrize("language", ["en", "zh"])
@pytest.mark.parametrize(
    "page,params",
    [
        ("notes", {}),
        ("methods", {"kind": "experience"}),
        ("methods", {"kind": "skill"}),
        ("profile", {}),
        ("topics", {}),
        ("prompts", {}),
        ("handoff", {}),
    ],
)
def test_empty_artifact_pages_explain_operation_evidence(dashboard, page, params, language):
    scope = create_scope(dashboard, "Evidence coverage")["scope_id"]
    response = dashboard.get(f"/dashboard/{page}", params={"scope": scope, "lang": language, **params})
    section = outcome_section(response)
    assert CATALOGS[language]["outcomes_heading"] in response.text
    assert CATALOGS[language]["outcomes_sync_not_applicable"] in section
    assert CATALOGS[language]["outcomes_local_unavailable"] in section
    assert CATALOGS[language]["outcomes_no_records"] in section


@pytest.mark.parametrize("public_reads", [False, True])
def test_candidate_decision_and_edited_result_keep_exact_versions(dashboard, monkeypatch, public_reads):
    scope = create_scope(dashboard, "Exact publication")["scope_id"]
    candidate = propose(dashboard, scope, "Original proposal")
    retain_run(
        dashboard,
        scope,
        "proposal-run",
        candidate={"candidate_id": candidate["candidate_id"], "version": 1},
        status="succeeded",
        outcome="proposed",
        completed_at=datetime(2026, 11, 1, tzinfo=UTC),
    )
    if public_reads:
        monkeypatch.setattr("powercontext.server.dashboard.api._builtin_runtime", lambda request: None)
    params = {"scope": scope, "lang": "en"}
    pending = dashboard.get("/dashboard/methods", params=params)
    assert "Candidate decision at read time: Pending" in run_section(pending, "proposal-run")
    assert "No published Artifact result" in run_section(pending, "proposal-run")
    revised = dashboard.post(
        "/v1/candidates/revise",
        json={
            "scope_id": scope,
            "candidate_id": candidate["candidate_id"],
            "expected_version": 1,
            "proposal": {**candidate["proposal"], "lesson": "Edited approved proposal"},
            "source_refs": candidate["source_refs"],
            "artifact_refs": candidate["artifact_refs"],
        },
    )
    assert revised.status_code == 200, revised.text
    approved = dashboard.post(
        "/v1/candidates/approve",
        json={"scope_id": scope, "candidate_id": candidate["candidate_id"], "expected_version": 2},
    )
    assert approved.status_code == 200, approved.text
    result = approved.json()["result_artifact"]
    opened = dashboard.get("/dashboard/experience", params={**params, "artifact": result["artifact_id"], "revision": 1})
    item = run_section(opened, "proposal-run")
    assert 'data-association="result"' in item
    assert f"{candidate['candidate_id']}@1" in item
    assert "Candidate version differs" in item
    assert "Version 2" in item
    assert "Artifact revision recorded by approval" in item
    assert "Edited approved proposal" in opened.text
    result_url = next(
        unescape(url)
        for url in re.findall(r'href="([^"]+)"', item)
        if urlsplit(unescape(url)).path == "/dashboard/experience"
    )
    assert parse_qs(urlsplit(result_url).query)["revision"] == ["1"]
    assert dashboard.get(result_url).status_code == 200


def test_recorded_states_history_and_same_identity_different_revision(dashboard):
    scope = create_scope(dashboard, "Historical outcomes")["scope_id"]
    selected = saved_memory(dashboard, scope)
    stamp = datetime(2026, 11, 2, tzinfo=UTC)
    records = (
        ("old-failure", {"status": "failed", "error": "provider-sensitive-error", "completed_at": stamp}),
        ("later-success", {"status": "succeeded", "outcome": "no_change", "completed_at": stamp}),
        ("needs-evidence", {"status": "succeeded", "outcome": "needs_evidence", "completed_at": stamp}),
        ("queued", {"status": "queued"}),
        ("active", {"status": "running", "started_at": stamp, "attempt_count": 1}),
    )
    for index, (name, values) in enumerate(records):
        retain_run(dashboard, scope, name, operation="revise_memory", target=selected, order=index, **values)
    unrelated = {**selected, "revision": selected["revision"] + 1}
    retain_run(dashboard, scope, "unrelated-revision", operation="revise_memory", target=unrelated, order=9)
    response = dashboard.get(
        "/dashboard/notes",
        params={"scope": scope, "artifact": selected["artifact_id"], "revision": selected["revision"], "lang": "en"},
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200, response.text
    assert 'data-association="scope"' in run_section(response, "unrelated-revision")
    for name, _ in records:
        assert 'data-association="target"' in run_section(response, name)
    assert "Recorded outcome: no change" in run_section(response, "later-success")
    assert "more evidence is needed" in run_section(response, "needs-evidence")
    assert "This historical run failed" in run_section(response, "old-failure")
    assert "provider-sensitive-error" not in response.text
    assert "Live execution observations are unavailable" in run_section(response, "active")
    assert "Saved independent content." in response.text


def test_input_only_relation_and_reused_rejected_proposal(dashboard):
    scope = create_scope(dashboard, "Input and reuse")["scope_id"]
    candidate = propose(dashboard, scope, "Existing proposal")
    approved = dashboard.post(
        "/v1/candidates/approve",
        json={"scope_id": scope, "candidate_id": candidate["candidate_id"], "expected_version": 1},
    ).json()
    selected = approved["result_artifact"]
    manifest = EvidenceManifest(
        artifacts=(ArtifactRef.model_validate(selected),), projection_digest="sha256:fixture", projection_bytes=0
    )
    retain_run(dashboard, scope, "input-only", operation="derive_skill", input_manifest=manifest)
    page = dashboard.get("/dashboard/methods", params={"scope": scope, "kind": "skill", "lang": "en"})
    assert 'data-association="scope"' in run_section(page, "input-only")
    # A selected Experience can be an input to another Experience creation without being its target.
    retain_run(dashboard, scope, "experience-input", input_manifest=manifest)
    opened = dashboard.get(
        "/dashboard/experience",
        params={"scope": scope, "artifact": selected["artifact_id"], "revision": 1, "lang": "en"},
    )
    assert 'data-association="input"' in run_section(opened, "experience-input")
    rejected = propose(dashboard, scope, "Rejected proposal")
    decision = dashboard.post(
        "/v1/candidates/reject",
        json={
            "scope_id": scope,
            "candidate_id": rejected["candidate_id"],
            "expected_version": 1,
            "reason": "Evidence is insufficient.",
        },
    )
    assert decision.status_code == 200, decision.text
    retain_run(
        dashboard,
        scope,
        "reused-rejection",
        status="succeeded",
        outcome="no_change",
        reused=True,
        candidate={"candidate_id": rejected["candidate_id"], "version": 1},
        reason="An unchanged proposal was already rejected.",
        order=10,
    )
    response = dashboard.get("/dashboard/methods", params={"scope": scope, "lang": "en"})
    item = run_section(response, "reused-rejection")
    assert "Recorded outcome: no change" in item and "Rejected" in item
    assert "An existing proposal was reused" in item and "No published Artifact result" in item


def test_prompt_key_and_revision_are_not_inferred_from_latest_run(dashboard):
    scope = create_scope(dashboard, "Prompt references")["scope_id"]
    selected_key = "experience.generate"
    for key in (selected_key, "skill.generate"):
        created = dashboard.post(
            f"/v1/scopes/{scope}/artifacts",
            json={
                "family": "prompt",
                "prompt_key": key,
                "content": {
                    "schema_version": "powercontext.prompt.v1",
                    "mode": "auto",
                    "instructions": "",
                    "demonstrations": [],
                },
            },
        )
        assert created.status_code == 201, created.text
        retain_run(
            dashboard,
            scope,
            key,
            operation="revise_prompt",
            target={"family": "prompt", "artifact_id": key, "revision": 1},
        )
    response = dashboard.get(
        "/dashboard/prompts", params={"scope": scope, "prompt_key": selected_key, "prompt_revision": 1, "lang": "en"}
    )
    assert response.status_code == 200, response.text
    assert 'data-association="target"' in run_section(response, selected_key)
    assert 'data-association="scope"' in run_section(response, "skill.generate")
    assert "describe configuration, not generation outcomes" in response.text


def test_handoff_commit_evidence_remains_separate_from_receiver_and_draft(dashboard):
    from tests.test_dashboard import commit_handoff

    scope = create_scope(dashboard, "Saved handoff")["scope_id"]
    selected = commit_handoff(dashboard, scope)
    retain_run(
        dashboard,
        scope,
        "handoff-refresh",
        operation="refresh_handoff",
        target=selected,
        status="succeeded",
        outcome="needs_evidence",
    )
    response = dashboard.get(
        "/dashboard/handoff-detail",
        params={"scope": scope, "artifact": selected["artifact_id"], "revision": selected["revision"], "lang": "en"},
    )
    assert response.status_code == 200, response.text
    assert "Review the HTTP contract" in response.text
    assert 'data-association="target"' in run_section(response, "handoff-refresh")
    assert "generated means a Draft was generated" in response.text
    assert "Commit can return an existing revision" in response.text


@pytest.mark.parametrize("missing_result", [False, True])
def test_tag_result_is_metadata_and_keeps_exact_content_basis(dashboard, monkeypatch, missing_result):
    scope = create_scope(dashboard, "Tag evidence")["scope_id"]
    candidate = propose(dashboard, scope, "Tag content basis")
    approved = dashboard.post(
        "/v1/candidates/approve",
        json={"scope_id": scope, "candidate_id": candidate["candidate_id"], "expected_version": 1},
    ).json()
    ref = approved["result_artifact"]
    source = dashboard.post(f"/v1/scopes/{scope}/sources", json={"content": "Verified category."}).json()
    tags = dashboard.get(f"/v1/scopes/{scope}/artifacts/experience/{ref['artifact_id']}/tags")
    proposal = CatalogChangeProposal(
        target=ArtifactTagTarget(family="experience", artifact_id=ref["artifact_id"]),
        basis_ref=ArtifactRef.model_validate(ref),
        expected_etag=tags.headers["etag"],
        before_tags=(),
        after_tags=("verified",),
    )

    async def propose_tags():
        principal = bind_principal(dashboard.app.state.authentication_provider._principal)
        try:
            return await dashboard.app.state.application.catalog_changes.for_scope(scope).propose(
                proposal,
                sources=(SourceRef(source_type="content", source_id=source["source_id"]),),
                reason="Verified category.",
            )
        finally:
            reset_principal(principal)

    stored = dashboard.portal.call(propose_tags)
    tag_approval = dashboard.post(
        "/v1/candidates/approve", json={"scope_id": scope, "candidate_id": stored.candidate_id, "expected_version": 1}
    )
    assert tag_approval.status_code == 200, tag_approval.text
    retain_run(
        dashboard,
        scope,
        "tag-run",
        operation="revise_tags",
        tag_target=proposal.model_dump(exclude={"before_tags", "after_tags"}),
        candidate={"kind": "tag", "candidate_id": stored.candidate_id, "version": 1},
        status="succeeded",
        outcome="proposed",
    )
    if missing_result:
        read = DashboardAPI.read

        async def incomplete(self, path, payload=None):
            value = await read(self, path, payload)
            if path == "/v1/candidates/get":
                value.pop("result", None)
            return value

        monkeypatch.setattr(DashboardAPI, "read", incomplete)
    response = dashboard.get(
        "/dashboard/experience",
        params={
            "scope": scope,
            "artifact": ref["artifact_id"],
            "revision": 1,
            "outcome_operation": "revise_tags",
            "lang": "en",
        },
    )
    assert response.status_code == 200, response.text
    item = run_section(response, "tag-run")
    assert 'data-association="tag_basis"' in item
    if missing_result:
        assert CATALOGS["en"]["outcomes_candidate_unavailable"] in item
        assert CATALOGS["en"]["outcomes_not_published"] not in item
    else:
        assert "The decision saved these tags" in item and "verified" in item
    assert "Artifact revision recorded by approval" not in item


def test_profile_generation_links_to_exact_run_outside_recent_window(dashboard):
    from powercontext.builtin.artifacts.profile.models import ProfileContent, ProfileDraft, ProfileGeneration
    from tests.test_dashboard_profile_handoff import _profile

    scope = create_scope(dashboard, "Profile origin")["scope_id"]
    original = _profile(dashboard, scope, "# Original profile")
    ref = ArtifactRef(family="profile", artifact_id="profile", revision=original["revision"])
    for index in range(8):
        retain_run(
            dashboard, scope, f"profile-run-{index}", operation="revise_profile", target=ref.model_dump(), order=index
        )
    runtime = dashboard.app.state.application

    async def save_generation():
        async with runtime.primary_database.transaction() as connection:
            previous = await runtime.profiles.artifacts.get(connection, scope, ref)
            await runtime.profiles.artifacts.revise(
                connection,
                scope,
                previous,
                ProfileDraft(
                    content=ProfileContent(
                        content="# Saved Dream profile",
                        generation=ProfileGeneration(
                            mode="dream_review_approved",
                            created_at=datetime(2026, 11, 2, tzinfo=UTC),
                            generator_id="fixture",
                            generator_version="1",
                            dream_run_id="profile-run-0",
                        ),
                    )
                ),
            )

    dashboard.portal.call(save_generation)
    response = dashboard.get("/dashboard/profile", params={"scope": scope, "lang": "en"})
    assert response.status_code == 200, response.text
    assert 'data-operation-run="profile-run-0"' not in response.text
    origin_url = next(
        unescape(url) for url in re.findall(r'href="([^"]+)"', response.text) if "outcome_run=profile-run-0" in url
    )
    opened = dashboard.get(origin_url)
    assert opened.status_code == 200, opened.text
    assert 'data-operation-run="profile-run-0"' in opened.text
    assert "Saved Dream profile" in opened.text


def test_bounded_pagination_exact_run_and_scope_switch(dashboard):
    scope = create_scope(dashboard, "Paged operations")["scope_id"]
    other = create_scope(dashboard, "Other Scope")["scope_id"]
    for index in range(8):
        retain_run(dashboard, scope, f"paged-{index}", order=index)
    retain_run(dashboard, other, "foreign-run")
    first = dashboard.get("/dashboard/methods", params={"scope": scope, "lang": "en", "kind": "experience"})
    assert first.status_code == 200, first.text
    assert first.text.count('data-operation-run="') == 6
    assert 'data-operation-run="paged-0"' not in first.text
    next_url = next(
        unescape(url) for url in re.findall(r'href="([^"]+)"', first.text) if "operation_runs_cursor=" in url
    )
    next_page = dashboard.get(next_url)
    assert next_page.status_code == 200, next_page.text
    assert next_page.text.count('data-operation-run="') == 2
    assert 'data-operation-run="paged-0"' in next_page.text
    exact = dashboard.get("/dashboard/methods", params={"scope": scope, "outcome_run": "paged-0"})
    assert exact.status_code == 200 and exact.text.count('data-operation-run="') == 1
    crossed = dashboard.get("/dashboard/methods", params={"scope": scope, "outcome_run": "foreign-run"})
    assert crossed.status_code == 200
    assert 'data-operation-run="foreign-run"' not in crossed.text
    assert CATALOGS["en"]["outcomes_read_unavailable"] in crossed.text
    switched = dashboard.get("/dashboard/methods", params={"scope": other, "lang": "en"})
    assert 'data-operation-run="foreign-run"' in switched.text and 'data-operation-run="paged-0"' not in switched.text


@pytest.mark.parametrize("failure", ["dream", "candidate", "result"])
def test_partial_inspection_failure_preserves_saved_content(dashboard, monkeypatch, failure):
    scope = create_scope(dashboard, "Partial evidence")["scope_id"]
    candidate = propose(dashboard, scope, "Content remains independently readable")
    approved = dashboard.post(
        "/v1/candidates/approve",
        json={"scope_id": scope, "candidate_id": candidate["candidate_id"], "expected_version": 1},
    ).json()
    result = approved["result_artifact"]
    retain_run(
        dashboard,
        scope,
        "retained-success",
        candidate={"candidate_id": candidate["candidate_id"], "version": 1},
        status="succeeded",
        outcome="proposed",
    )
    read = DashboardAPI.read

    async def fail(self, path, payload=None):
        if (failure == "dream" and "/dream?" in path) or (failure == "candidate" and path == "/v1/candidates/get"):
            raise ReadError(503, "service_unavailable")
        # The content read succeeds; only the independently authorized output check fails.
        if failure == "result" and "/revisions/" in path and "/artifacts/experience/" in path:
            if getattr(self, "outcome_test_read", False):
                raise ReadError(403, "access_denied")
            self.outcome_test_read = True
        return await read(self, path, payload)

    monkeypatch.setattr(DashboardAPI, "read", fail)
    response = dashboard.get(
        "/dashboard/experience", params={"scope": scope, "artifact": result["artifact_id"], "revision": 1, "lang": "en"}
    )
    assert response.status_code == 200, response.text
    assert "Content remains independently readable" in response.text
    if failure == "dream":
        assert CATALOGS["en"]["outcomes_read_unavailable"] in response.text
    else:
        item = run_section(response, "retained-success")
        assert "Succeeded" in item
        assert CATALOGS["en"][f"outcomes_{failure}_unavailable"] in item


def test_record_only_access_and_revocation_do_not_expose_scope_history(dashboard, monkeypatch):
    scope = create_scope(dashboard, "Private operation Scope")["scope_id"]
    selected = saved_memory(dashboard, scope)
    retain_run(dashboard, scope, "scope-secret-operation", operation="revise_memory", target=selected)
    reader = {"type": "user", "id": "exact-outcome-reader"}
    binding = _grant(
        dashboard,
        reader,
        {
            "type": "artifact",
            "scope_id": scope,
            "identity": {"family": "atomic-memory", "artifact_id": selected["artifact_id"]},
        },
        "artifact.viewer",
    )
    params = {"scope": scope, "artifact": selected["artifact_id"], "revision": 1, "lang": "en"}
    with monkeypatch.context() as patch:
        patch.setattr(dashboard.app.state.authentication_provider, "_principal", PrincipalRef(**reader))
        response = dashboard.get("/dashboard/notes", params=params)
        assert response.status_code == 200, response.text
        assert "Saved independent content." in response.text
        assert CATALOGS["en"]["outcomes_scope_unavailable"] in response.text
        assert "scope-secret-operation" not in response.text
    _revoke(dashboard, binding)
    monkeypatch.setattr(dashboard.app.state.authentication_provider, "_principal", PrincipalRef(**reader))
    denied = dashboard.get("/dashboard/notes", params=params)
    assert denied.status_code == 403
    assert "Saved independent content." not in denied.text and "scope-secret-operation" not in denied.text


def test_inspection_does_not_write_business_state_or_invoke_processing(dashboard, monkeypatch):
    scope = create_scope(dashboard, "Passive operation view")["scope_id"]
    saved_memory(dashboard, scope)
    retain_run(
        dashboard,
        scope,
        "passive-queued",
        operation="revise_memory",
        target={"family": "atomic-memory", "artifact_id": "old-target", "revision": 1},
    )
    runtime = dashboard.app.state.application

    async def prohibited(*args, **kwargs):
        pytest.fail("Inspection invoked processing or a readiness/recovery operation")

    monkeypatch.setattr(runtime._dream_service, "create", prohibited)
    monkeypatch.setattr(runtime._dream_service, "execute", prohibited)
    read = DashboardAPI.read

    async def passive_read(self, path, payload=None):
        if path in {"/health/ready", "/v1/topic-memory/processing/status", "/v1/topic-memory/processing/retry"}:
            pytest.fail("Artifact outcome inspection invoked a probe or recovery operation")
        return await read(self, path, payload)

    monkeypatch.setattr(DashboardAPI, "read", passive_read)

    async def stored_state():
        async with runtime.primary_database.transaction() as connection:
            existing = await connection.run_sync(lambda sync: set(inspect(sync).get_table_names()))
            return {
                table.name: (await connection.execute(select(table))).all()
                for table in SHARED_METADATA.sorted_tables
                if table.name in existing
                and not table.name.startswith("pc_access_")
                and table.name != "pc_runtime_leases"
            }

    before = dashboard.portal.call(stored_state)
    for page in ("notes", "methods", "profile", "topics", "prompts", "handoff"):
        for headers in ({}, {"HX-Request": "true"}):
            response = dashboard.get(f"/dashboard/{page}", params={"scope": scope}, headers=headers)
            assert response.status_code == 200, response.text
    assert dashboard.portal.call(stored_state) == before


def _listed_record(dashboard, scope, page, params):
    """Create one saved record through a supported write path; return its id and any run target."""
    if page == "methods" and params.get("kind") == "experience":
        source = capture_source(dashboard, scope, "A listed experience record.")
        artifact_id = create_experience(dashboard, scope, source, "Listed experience", "Keep list pages exact")
        return artifact_id, None
    if page == "methods" and params.get("kind") == "skill":
        created = dashboard.post(
            f"/v1/scopes/{scope}/artifacts",
            json={
                "family": "skill",
                "content": {
                    "name": "listed-skill",
                    "description": "A listed skill record",
                    "instructions": "Keep list pages exact.",
                    "validation": ["List pages show no selection"],
                },
            },
        )
        assert created.status_code == 201, created.text
        return created.json()["artifact_id"], None
    reference = commit_handoff(dashboard, scope)
    return reference["artifact_id"], reference


@pytest.mark.parametrize(
    "page,params,operation",
    [
        ("methods", {"kind": "experience"}, "refine_experience"),
        ("methods", {"kind": "skill"}, "derive_skill"),
        ("handoff", {}, "refresh_handoff"),
    ],
)
def test_list_pages_never_claim_a_selected_revision(dashboard, page, params, operation):
    scope = create_scope(dashboard, "List pages select nothing")["scope_id"]
    listed, target = _listed_record(dashboard, scope, page, params)
    query = {"scope": scope, "lang": "en", **params}
    recorded = dashboard.get(f"/dashboard/{page}", params=query)
    assert recorded.status_code == 200, recorded.text
    assert listed in recorded.text, "the page must list a saved record for this check to mean anything"
    assert CATALOGS["en"]["outcomes_selected"] not in recorded.text

    retain_run(
        dashboard, scope, "listed-run", operation=operation, target=target, status="succeeded", outcome="no_change"
    )
    response = dashboard.get(f"/dashboard/{page}", params=query)
    section = outcome_section(response)
    drawer = drawer_section(response)
    expected = CATALOGS["en"]["outcomes_window"].format(
        operation=CATALOGS["en"][f"processing_operation_{operation}"], count=1
    )
    assert expected in section and expected in drawer
    assert CATALOGS["en"]["outcomes_selected"] not in section
    assert CATALOGS["en"]["outcomes_selected"] not in drawer
    assert CATALOGS["en"]["outcomes_saved_generation"] not in drawer
    assert CATALOGS["en"]["outcomes_handoff_origin"] not in drawer
    assert 'data-association="scope"' in run_section(response, "listed-run")


def test_skill_pages_choose_the_matching_default_operation(dashboard):
    scope = create_scope(dashboard, "Skill operation defaults")["scope_id"]
    content = {
        "name": "operation-defaults",
        "description": "Inspect the default Dream operation",
        "instructions": "Run the operation tests.",
        "validation": ["Operation tests pass"],
    }
    created = dashboard.post(f"/v1/scopes/{scope}/artifacts", json={"family": "skill", "content": content})
    assert created.status_code == 201, created.text
    skill_id = created.json()["artifact_id"]
    target = {"family": "skill", "artifact_id": skill_id, "revision": 1}
    retain_run(dashboard, scope, "skill-revision", operation="revise_skill", target=target, status="succeeded", order=1)
    retain_run(dashboard, scope, "skill-derivation", operation="derive_skill", status="succeeded", order=2)

    listed = dashboard.get("/dashboard/methods", params={"scope": scope, "kind": "skill", "lang": "en"})
    assert 'data-operation-run="skill-derivation"' in listed.text
    assert 'data-operation-run="skill-revision"' not in listed.text

    detail = {"scope": scope, "artifact": skill_id, "revision": 1, "lang": "en"}
    opened = dashboard.get("/dashboard/skill", params=detail)
    assert 'data-association="target"' in run_section(opened, "skill-revision")
    assert 'data-operation-run="skill-derivation"' not in opened.text
    assert CATALOGS["en"]["outcomes_selected"] in outcome_section(opened)

    switched = dashboard.get("/dashboard/skill", params={**detail, "outcome_operation": "derive_skill"})
    assert 'data-operation-run="skill-derivation"' in switched.text
    assert 'data-operation-run="skill-revision"' not in switched.text
    assert 'data-association="scope"' in run_section(switched, "skill-derivation")
    assert CATALOGS["en"]["outcomes_exact_unavailable"] in outcome_section(switched)


@pytest.mark.parametrize("headers", [{}, {"HX-Request": "true"}])
def test_opened_topic_revision_exposes_its_operation_evidence(dashboard, headers):
    scope = create_scope(dashboard, "Topic revision operation evidence")["scope_id"]
    created = dashboard.post(
        f"/v1/scopes/{scope}/artifacts",
        json={
            "family": "topic-memory",
            "content": {"title": "Opened topic", "summary": "Retained summary", "detail": "Retained topic body"},
        },
    )
    assert created.status_code == 201, created.text
    artifact_id = created.json()["artifact_id"]
    selected = {"family": "topic-memory", "artifact_id": artifact_id, "revision": 1}
    retain_run(dashboard, scope, "topic-revision-run", operation="revise_topic_memory", target=selected)
    response = dashboard.get(
        "/dashboard/topics",
        params={"scope": scope, "topic_artifact": artifact_id, "topic_revision": 1, "lang": "en"},
        headers=headers,
    )
    assert CATALOGS["en"]["outcomes_selected"] in outcome_section(response)
    assert f"topic-memory/{artifact_id}@1" in outcome_section(response)
    assert 'data-association="target"' in run_section(response, "topic-revision-run")
    assert "Retained topic body" in response.text


@pytest.mark.parametrize("mutation", ["missing_identity", "missing_status", "unaddressable_result"])
def test_unusable_candidate_keeps_the_base_run(dashboard, monkeypatch, mutation):
    scope = create_scope(dashboard, "Structural candidate")["scope_id"]
    candidate = propose(dashboard, scope, "Structural proposal")
    retain_run(
        dashboard,
        scope,
        "structural-run",
        candidate={"candidate_id": candidate["candidate_id"], "version": 1},
        status="succeeded",
        outcome="proposed",
    )
    read = DashboardAPI.read

    async def unusable(self, path, payload=None):
        value = await read(self, path, payload)
        if path != "/v1/candidates/get":
            return value
        if mutation == "missing_identity":
            return {"candidate_id": value["candidate_id"]}
        if mutation == "missing_status":
            return {key: item for key, item in value.items() if key != "status"}
        return {**value, "result_artifact": {"family": "experience", "artifact_id": "output", "revision": 0}}

    monkeypatch.setattr(DashboardAPI, "read", unusable)
    response = dashboard.get("/dashboard/methods", params={"scope": scope, "lang": "en"})
    item = run_section(response, "structural-run")
    assert response.status_code == 200, response.text
    assert "Succeeded" in item
    assert 'data-association="scope"' in item
    assert CATALOGS["en"]["outcomes_candidate_unavailable"] in item
    assert CATALOGS["en"]["outcomes_no_records"] not in response.text


def test_absent_nullable_candidate_fields_render_as_absent(dashboard, monkeypatch):
    scope = create_scope(dashboard, "Absent nullable fields")["scope_id"]
    candidate = propose(dashboard, scope, "Proposal without saved output")
    retain_run(
        dashboard,
        scope,
        "sparse-candidate",
        candidate={"candidate_id": candidate["candidate_id"], "version": 1},
        status="succeeded",
        outcome="proposed",
    )
    read = DashboardAPI.read

    async def sparse(self, path, payload=None):
        value = await read(self, path, payload)
        if path == "/v1/candidates/get":
            return {key: item for key, item in value.items() if key not in {"result_artifact", "result"}}
        return value

    monkeypatch.setattr(DashboardAPI, "read", sparse)
    response = dashboard.get("/dashboard/methods", params={"scope": scope, "lang": "en"})
    assert response.status_code == 200, response.text
    item = run_section(response, "sparse-candidate")
    assert CATALOGS["en"]["outcomes_current_candidate"] in item
    assert CATALOGS["en"]["outcomes_published"] not in item
    assert CATALOGS["en"]["outcomes_candidate_unavailable"] not in item


@pytest.mark.parametrize("status", ["approved", "pending", "rejected"])
def test_candidate_result_must_agree_with_its_decision(dashboard, monkeypatch, status):
    scope = create_scope(dashboard, "Publication evidence must be consistent")["scope_id"]
    candidate = propose(dashboard, scope, "Preserve the recorded publication")
    if status != "pending":
        response = dashboard.post(
            f"/v1/candidates/{'approve' if status == 'approved' else 'reject'}",
            json={
                "scope_id": scope,
                "candidate_id": candidate["candidate_id"],
                "expected_version": 1,
                **({"reason": "Insufficient evidence"} if status == "rejected" else {}),
            },
        )
        assert response.status_code == 200, response.text
    retain_run(
        dashboard,
        scope,
        "inconsistent-decision",
        candidate={"candidate_id": candidate["candidate_id"], "version": 1},
        status="succeeded",
        outcome="proposed",
    )
    read = DashboardAPI.read

    async def inconsistent(self, path, payload=None):
        value = await read(self, path, payload)
        if path == "/v1/candidates/get":
            if status == "approved":
                value.pop("result_artifact")
            else:
                value["result_artifact"] = {"family": "experience", "artifact_id": "unpublished", "revision": 1}
        return value

    monkeypatch.setattr(DashboardAPI, "read", inconsistent)
    response = dashboard.get("/dashboard/methods", params={"scope": scope, "lang": "en"})
    item = run_section(response, "inconsistent-decision")
    assert "Succeeded" in item
    assert CATALOGS["en"]["outcomes_candidate_unavailable"] in item
    assert CATALOGS["en"]["outcomes_not_published"] not in item


def test_candidate_target_change_is_normalized_to_an_invalid_response(dashboard, monkeypatch):
    scope = create_scope(dashboard, "Candidate target change")["scope_id"]
    candidate = propose(dashboard, scope, "Target moved after the run")
    retain_run(
        dashboard,
        scope,
        "moved-target",
        target={"family": "experience", "artifact_id": "recorded-baseline", "revision": 1},
        candidate={"candidate_id": candidate["candidate_id"], "version": 1},
        status="succeeded",
        outcome="proposed",
    )
    read = DashboardAPI.read

    async def moved(self, path, payload=None):
        value = await read(self, path, payload)
        if path == "/v1/candidates/get":
            return {**value, "target": {"family": "experience", "artifact_id": "moved-baseline", "revision": 1}}
        return value

    monkeypatch.setattr(DashboardAPI, "read", moved)
    response = dashboard.get("/dashboard/methods", params={"scope": scope, "lang": "en"})
    assert response.status_code == 200, response.text
    item = run_section(response, "moved-target")
    assert 'data-association="scope"' in item
    assert CATALOGS["en"]["outcomes_candidate_unavailable"] in item


def test_inspection_deadline_before_list_result_keeps_saved_content(dashboard, monkeypatch, expire_inspection):
    scope = create_scope(dashboard, "List read timeout")["scope_id"]
    source = capture_source(dashboard, scope, "Saved content remains readable during inspection.")
    create_experience(dashboard, scope, source, "Unavailable history", "Keep the saved lesson readable")
    retain_run(dashboard, scope, "unread-run", status="succeeded", outcome="no_change")
    read = DashboardAPI.read

    async def unavailable_list(self, path, payload=None):
        if path.startswith(f"/v1/scopes/{scope}/dream?"):
            await expire_inspection()
        return await read(self, path, payload)

    monkeypatch.setattr(DashboardAPI, "read", unavailable_list)
    response = dashboard.get("/dashboard/methods", params={"scope": scope, "lang": "en"})
    section = outcome_section(response)
    assert "Keep the saved lesson readable" in response.text
    assert CATALOGS["en"]["outcomes_read_unavailable"] in section
    assert CATALOGS["en"]["outcomes_no_records"] not in section
    assert 'data-operation-run="' not in section


def test_inspection_deadline_retains_base_records(dashboard, monkeypatch, expire_inspection):
    scope = create_scope(dashboard, "Deadline keeps facts")["scope_id"]
    candidate = propose(dashboard, scope, "Slow decision")
    retain_run(
        dashboard,
        scope,
        "slow-run",
        candidate={"candidate_id": candidate["candidate_id"], "version": 1},
        status="succeeded",
        outcome="proposed",
    )
    read = DashboardAPI.read

    async def slow(self, path, payload=None):
        if path == "/v1/candidates/get":
            await expire_inspection()
        return await read(self, path, payload)

    monkeypatch.setattr(DashboardAPI, "read", slow)
    response = dashboard.get("/dashboard/methods", params={"scope": scope, "lang": "en"})
    section = outcome_section(response)
    assert CATALOGS["en"]["outcomes_read_unavailable"] in section
    item = run_section(response, "slow-run")
    assert "Succeeded" in item and 'data-association="scope"' in item


def test_deadline_keeps_approved_result_reference_when_body_is_unavailable(dashboard, monkeypatch, expire_inspection):
    scope = create_scope(dashboard, "Published result timeout")["scope_id"]
    candidate = propose(dashboard, scope, "Retained approval")
    approved = dashboard.post(
        "/v1/candidates/approve",
        json={"scope_id": scope, "candidate_id": candidate["candidate_id"], "expected_version": 1},
    )
    assert approved.status_code == 200, approved.text
    result = approved.json()["result_artifact"]
    retain_run(
        dashboard,
        scope,
        "slow-published-result",
        candidate={"candidate_id": candidate["candidate_id"], "version": 1},
        status="succeeded",
        outcome="proposed",
    )
    revision = DashboardAPI.artifact_revision

    async def slow(self, *args):
        await expire_inspection()
        return await revision(self, *args)

    monkeypatch.setattr(DashboardAPI, "artifact_revision", slow)
    response = dashboard.get("/dashboard/methods", params={"scope": scope, "lang": "en"})
    item = run_section(response, "slow-published-result")
    assert f"{result['family']}/{result['artifact_id']}@{result['revision']}" in item
    assert CATALOGS["en"]["outcomes_result_unavailable"] in item
    assert CATALOGS["en"]["outcomes_not_published"] not in item


def test_deadline_keeps_association_from_the_base_record(dashboard, monkeypatch, expire_inspection):
    scope = create_scope(dashboard, "Deadline keeps association")["scope_id"]
    selected = saved_memory(dashboard, scope)
    candidate = propose(dashboard, scope, "Slow associated decision")
    # One more run than the detail concurrency, so the last one waits outside every slot.
    names = [f"slow-associated-{index}" for index in range(DETAIL_CONCURRENCY + 1)]
    for name in names:
        retain_run(
            dashboard,
            scope,
            name,
            operation="revise_memory",
            target=selected,
            candidate={"candidate_id": candidate["candidate_id"], "version": 1},
            status="succeeded",
            outcome="proposed",
        )
    read = DashboardAPI.read

    async def slow(self, path, payload=None):
        if path == "/v1/candidates/get":
            await expire_inspection(after_entries=DETAIL_CONCURRENCY)
        return await read(self, path, payload)

    monkeypatch.setattr(DashboardAPI, "read", slow)
    response = dashboard.get(
        "/dashboard/notes",
        params={"scope": scope, "artifact": selected["artifact_id"], "revision": selected["revision"], "lang": "en"},
    )
    section = outcome_section(response)
    assert CATALOGS["en"]["outcomes_associated"].format(count=len(names)) in section
    for name in names:
        assert 'data-association="target"' in run_section(response, name)
        assert CATALOGS["en"]["outcomes_candidate_unavailable"] in run_section(response, name)


def test_unlabeled_generation_metadata_is_not_rendered(dashboard, monkeypatch):
    scope = create_scope(dashboard, "Generation guard")["scope_id"]
    record = commit_handoff(dashboard, scope)
    read = DashboardAPI.read

    async def malformed(self, path, payload=None):
        value = await read(self, path, payload)
        if "/artifacts/handoff/" in path and "/revisions/" in path:
            content = {
                **value["content"],
                "generation": {"selection": "built_in", "artifact": None, "edit_status": "unlabelled"},
            }
            return {**value, "content": content}
        return value

    monkeypatch.setattr(DashboardAPI, "read", malformed)
    response = dashboard.get(
        "/dashboard/handoff-detail",
        params={"scope": scope, "artifact": record["artifact_id"], "revision": record["revision"], "lang": "en"},
    )
    assert response.status_code == 200, response.text
    drawer = drawer_section(response)
    assert CATALOGS["en"]["outcomes_handoff_origin"] not in drawer
    assert CATALOGS["en"]["outcomes_edit_edited"] not in drawer


def test_partial_failure_keeps_card_and_drawer_consistent(dashboard, monkeypatch):
    scope = create_scope(dashboard, "Consistent summary")["scope_id"]
    selected = saved_memory(dashboard, scope)
    candidate = propose(dashboard, scope, "Decision that cannot be read")
    retain_run(
        dashboard,
        scope,
        "associated-run",
        operation="revise_memory",
        target=selected,
        order=1,
        status="succeeded",
        outcome="no_change",
    )
    retain_run(
        dashboard,
        scope,
        "unreadable-run",
        operation="revise_memory",
        target={**selected, "revision": selected["revision"] + 1},
        candidate={"candidate_id": candidate["candidate_id"], "version": 1},
        order=2,
        status="succeeded",
        outcome="proposed",
    )
    read = DashboardAPI.read

    async def fail(self, path, payload=None):
        if path == "/v1/candidates/get":
            raise ReadError(503, "service_unavailable")
        return await read(self, path, payload)

    monkeypatch.setattr(DashboardAPI, "read", fail)
    response = dashboard.get(
        "/dashboard/notes",
        params={"scope": scope, "artifact": selected["artifact_id"], "revision": selected["revision"], "lang": "en"},
    )
    section = outcome_section(response)
    assert CATALOGS["en"]["outcomes_associated"].format(count=1) in section
    assert 'data-association="target"' in run_section(response, "associated-run")
    unreadable = run_section(response, "unreadable-run")
    assert 'data-association="scope"' in unreadable
    assert CATALOGS["en"]["outcomes_candidate_unavailable"] in unreadable
