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

"""Read proposals, make versioned decisions, and follow exact saved results."""

import json
import re
from html import unescape
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.catalog_changes.models import CatalogChangeProposal
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.tags import ArtifactTagTarget
from powercontext.server.dashboard.api import DashboardAPI
from powercontext.server.dashboard.errors import ReadError
from powercontext.server.dashboard.routes import LABELS
from powercontext.server.factory import create_server_app
from powercontext.server.settings import DashboardConfig, McpConfig, ServerSettings
from powercontext.sources import SourceRef
from tests.test_dashboard import create_scope
from tests.test_dashboard import dashboard as dashboard


def experience(lesson):
    return {
        "situation": "Delivery failed",
        "action": "Check the address",
        "outcome": "Address checked",
        "lesson": lesson,
    }


def propose(client, scope, lesson="Verify the destination", *, target=None):
    source = client.post(f"/v1/scopes/{scope}/sources", json={"content": "Original delivery evidence."}).json()
    response = client.post(
        "/v1/experience/propose",
        json={
            "scope_id": scope,
            "proposal": experience(lesson),
            "source_refs": [{"name": "content", "source_id": source["source_id"]}],
            "artifact_refs": [target] if target else [],
            "target": target,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def reading(client, scope, candidate, **params):
    return client.get(
        "/dashboard/review",
        params={
            "scope": scope,
            "candidate_id": candidate["candidate_id"],
            "candidate_kind": candidate["candidate_kind"],
            **params,
        },
    )


def decision(client, scope, candidate, action, **values):
    return client.post(
        f"/dashboard/review/decision?scope={scope}&candidate_id={candidate['candidate_id']}&lang=en&period=30d",
        data={
            "scope": scope,
            "candidate_id": candidate["candidate_id"],
            "candidate_kind": candidate["candidate_kind"],
            "expected_version": candidate["version"],
            "action": action,
            **values,
        },
        headers={"Origin": "http://testserver"},
        follow_redirects=False,
    )


def hrefs(html, path):
    return [unescape(url) for url in re.findall(r'href="([^"]+)"', html) if urlsplit(unescape(url)).path == path]


@pytest.mark.parametrize("public_reads", [False, True])
def test_review_content_approval_and_exact_result(dashboard, monkeypatch, public_reads):
    scope = create_scope(dashboard, "Readable review")["scope_id"]
    candidate = propose(dashboard, scope, "Verify <destination> & preserve evidence.")
    if public_reads:
        monkeypatch.setattr("powercontext.server.dashboard.api._builtin_runtime", lambda request: None)
    page = reading(dashboard, scope, candidate, lang="en")
    assert page.status_code == 200, page.text
    assert 'data-review-field="lesson"' in page.text
    assert "Verify &lt;destination&gt; &amp; preserve evidence." in page.text
    assert "Original delivery evidence." not in page.text
    evidence_url = next(unescape(url) for url in re.findall(r'hx-get="([^"]+)"', page.text) if "evidence=0" in url)
    assert "Original delivery evidence." in dashboard.get(evidence_url).text
    approved = decision(dashboard, scope, candidate, "approve")
    assert approved.status_code == 303, approved.text
    query = parse_qs(urlsplit(approved.headers["location"]).query)
    assert query.items() >= {"review_status": ["approved"], "lang": ["en"], "period": ["30d"], "scope": [scope]}.items()
    saved = dashboard.get(approved.headers["location"])
    assert saved.status_code == 200, saved.text
    assert 'aria-label="Saved review result"' in saved.text
    result_url = next(url for url in hrefs(saved.text, "/dashboard/experience") if "revision=1" in url)
    assert dashboard.get(result_url).status_code == 200
    assert 'name="action" value="approve"' not in saved.text
    stored = dashboard.post(
        "/v1/candidates/get", json={"scope_id": scope, "candidate_id": candidate["candidate_id"]}
    ).json()
    result = stored["result_artifact"]
    path = f"/v1/scopes/{scope}/artifacts/experience/{result['artifact_id']}"
    head = dashboard.get(path)
    changed = dashboard.put(
        path, headers={"If-Match": head.headers["etag"]}, json={"content": experience("A later unrelated revision.")}
    )
    assert changed.status_code == 200, changed.text
    exact = dashboard.get(result_url)
    assert "Verify &lt;destination&gt; &amp; preserve evidence." in exact.text
    assert "A later unrelated revision." not in exact.text


def test_review_compares_recorded_target_and_preserves_conflict(dashboard):
    scope = create_scope(dashboard, "Recorded target")["scope_id"]
    created = dashboard.post(
        f"/v1/scopes/{scope}/artifacts", json={"family": "experience", "content": experience("Original base revision.")}
    ).json()
    ref = {key: created[key] for key in ("family", "artifact_id", "revision")}
    candidate = propose(dashboard, scope, "Proposed correction.", target=ref)
    path = f"/v1/scopes/{scope}/artifacts/experience/{ref['artifact_id']}"
    head = dashboard.get(path)
    changed = dashboard.put(
        path,
        headers={"If-Match": head.headers["etag"]},
        json={"content": experience("Another writer's newer revision.")},
    )
    assert changed.status_code == 200, changed.text
    page = reading(dashboard, scope, candidate)
    assert page.status_code == 200, page.text
    row = page.text.split('data-review-field="lesson"', 1)[1].split("</section>", 1)[0]
    assert "Original base revision." in row and "Proposed correction." in row
    assert "Another writer" not in row
    assert decision(dashboard, scope, candidate, "approve").status_code == 409
    assert dashboard.get(path).json()["content"]["lesson"] == "Another writer's newer revision."


def test_review_missing_baseline_is_unknown_instead_of_changed(dashboard, monkeypatch):
    scope = create_scope(dashboard, "Unknown baseline")["scope_id"]
    created = dashboard.post(
        f"/v1/scopes/{scope}/artifacts", json={"family": "experience", "content": experience("Unavailable base.")}
    ).json()
    candidate = propose(
        dashboard,
        scope,
        "Readable proposal.",
        target={key: created[key] for key in ("family", "artifact_id", "revision")},
    )

    async def unavailable(self, scope, family, artifact, revision):
        raise ReadError(503, "service_unavailable")

    monkeypatch.setattr(DashboardAPI, "artifact_revision", unavailable)
    page = reading(dashboard, scope, candidate)
    assert page.status_code == 200, page.text
    row = page.text.split('data-review-field="lesson"', 1)[1].split("</section>", 1)[0]
    assert LABELS["review_comparison_unknown"] in row
    assert LABELS["review_field_changed"] not in row
    assert LABELS["review_field_unchanged"] not in row
    assert "Readable proposal." in row


def test_review_revise_and_reject_keep_version_guards(dashboard):
    scope = create_scope(dashboard, "Revised review")["scope_id"]
    candidate = propose(dashboard, scope)
    replacement = {
        "proposal": experience("A revised candidate."),
        "source_refs": candidate["source_refs"],
        "artifact_refs": candidate["artifact_refs"],
        "target": candidate["target"],
        "reason": "Corrected evidence.",
    }
    revised = decision(dashboard, scope, candidate, "revise", replacement=json.dumps(replacement))
    assert revised.status_code == 303, revised.text
    current = dashboard.post(
        "/v1/candidates/get", json={"scope_id": scope, "candidate_id": candidate["candidate_id"]}
    ).json()
    assert current["version"] == 2 and current["status"] == "pending"
    assert "A revised candidate." in dashboard.get(revised.headers["location"]).text
    assert decision(dashboard, scope, candidate, "approve").status_code == 409
    rejected = decision(dashboard, scope, current, "reject", reason="Evidence remains incomplete.")
    assert rejected.status_code == 303, rejected.text
    page = dashboard.get(rejected.headers["location"])
    assert page.status_code == 200, page.text
    assert "Evidence remains incomplete." in page.text
    assert "review_status=rejected" in str(page.url)
    assert 'name="action" value="approve"' not in page.text


def test_review_directory_failure_and_scope_isolation(dashboard, monkeypatch):
    scope = create_scope(dashboard, "Review scope")["scope_id"]
    other = create_scope(dashboard, "Separate review scope")["scope_id"]
    candidate = propose(dashboard, scope, "Private candidate text.")
    crossed = reading(dashboard, other, candidate)
    assert crossed.status_code == 404
    assert "Private candidate text." not in crossed.text

    async def unavailable(self, payload):
        raise ReadError(503, "service_unavailable")

    monkeypatch.setattr(DashboardAPI, "candidates", unavailable)
    page = reading(dashboard, scope, candidate)
    assert page.status_code == 200, page.text
    assert LABELS["retry"] in page.text
    assert LABELS["review_empty"] not in page.text
    assert "Private candidate text." in page.text


@pytest.mark.parametrize("proposed_tags", [(), ("verified",)])
def test_tag_review_records_tags_without_creating_content_revision(tmp_path, proposed_tags):
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path}/tag.db"),
            dashboard=DashboardConfig(enabled=True),
            mcp=McpConfig(enabled=False),
        ),
    )
    with TestClient(app) as client:
        scope = create_scope(client, "Tag review")["scope_id"]
        created = client.post(
            f"/v1/scopes/{scope}/artifacts", json={"family": "experience", "content": experience("Tag basis.")}
        ).json()
        ref = {key: created[key] for key in ("family", "artifact_id", "revision")}
        source = client.post(f"/v1/scopes/{scope}/sources", json={"content": "Verified category."}).json()
        tags = client.get(f"/v1/scopes/{scope}/artifacts/experience/{ref['artifact_id']}/tags")

        async def propose_tags():
            service = app.state.application.catalog_changes.for_scope(scope)
            return await service.propose(
                CatalogChangeProposal(
                    target=ArtifactTagTarget(family="experience", artifact_id=ref["artifact_id"]),
                    basis_ref=ArtifactRef.model_validate(ref),
                    expected_etag=tags.headers["etag"],
                    before_tags=(),
                    after_tags=proposed_tags,
                ),
                sources=(SourceRef(source_type="content", source_id=source["source_id"]),),
                reason="Verified category.",
            )

        assert client.portal is not None
        stored = client.portal.call(propose_tags)
        candidate = client.post(
            "/v1/candidates/get", json={"scope_id": scope, "candidate_id": stored.candidate_id}
        ).json()
        pending = reading(client, scope, candidate)
        assert pending.status_code == 200, pending.text
        assert 'data-review-field="tags"' in pending.text
        approved = decision(client, scope, candidate, "approve")
        assert approved.status_code == 303, approved.text
        page = client.get(approved.headers["location"])
        assert page.status_code == 200, page.text
        assert "These tags were saved on approval." in page.text
        if proposed_tags:
            assert "verified" in page.text
        else:
            assert "No tags" in page.text
        assert client.get(f"/v1/scopes/{scope}/artifacts/experience/{ref['artifact_id']}").json()["revision"] == 1
