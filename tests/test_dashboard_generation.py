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

"""Dashboard generation preserves exact evidence and the review boundary."""

import json
import re
from html import unescape
from urllib.parse import parse_qs, urlsplit

import pytest

from powercontext.builtin.artifacts.experience import ExperienceContent
from powercontext.builtin.artifacts.skill import SkillContent
from powercontext.server.authz import PrincipalRef
from tests.test_dashboard import create_scope
from tests.test_dashboard import dashboard as dashboard
from tests.test_dashboard_scope_selection import _grant


class Generator:
    def __init__(self, family, *, no_op=False):
        self.family = family
        self.no_op = no_op
        self.inputs = []

    async def generate(self, value, /):
        self.inputs.append(value)
        if self.no_op:
            return None
        if self.family == "experience":
            return ExperienceContent(
                situation="A contract changed.",
                action="Run checks.",
                outcome="Checks passed.",
                lesson="Check the contract before release.",
            )
        return SkillContent(
            name="check-contract",
            description="Check contracts before release.",
            instructions="Run the contract checks and inspect the output.",
            validation=("Contract checks pass",),
        )


def configure(dashboard, monkeypatch, family, *, no_op=False):
    runtime = dashboard.app.state.application
    factory = runtime._generation_service
    generator = Generator(family, no_op=no_op)

    def service(scope):
        selected = factory(scope)
        setattr(selected, f"_{family}_generator", generator)
        return selected

    monkeypatch.setattr(runtime, "_generation_service", service)
    return generator


def captured(dashboard, scope, text="The contract checks passed."):
    response = dashboard.post(f"/v1/scopes/{scope}/sources", json={"content": text})
    assert response.status_code == 201, response.text
    return {"name": "content", "source_id": response.json()["source_id"]}


def submit(dashboard, scope, family, refs, *, path="/dashboard/methods/generate?lang=en", **kwargs):
    return dashboard.post(
        path,
        data={
            "scope": scope,
            "kind": family,
            "evidence_type": "source",
            "evidence": [json.dumps(ref) for ref in refs],
            "reason": "Review this suggestion.",
        },
        headers={"Origin": "http://testserver"},
        follow_redirects=False,
        **kwargs,
    )


@pytest.mark.parametrize("family", ["experience", "skill"])
@pytest.mark.parametrize("public_reads", [False, True])
def test_generate_selected_sources_redirects_to_pending_review(dashboard, monkeypatch, family, public_reads):
    scope = create_scope(dashboard, "Generate reviewed content")["scope_id"]
    ref = captured(dashboard, scope)
    if public_reads:
        monkeypatch.setattr("powercontext.server.dashboard.api._builtin_runtime", lambda request: None)
    generator = configure(dashboard, monkeypatch, family)
    panel = dashboard.get(
        "/dashboard/methods",
        params={"scope": scope, "kind": family, "generate": "1", "generation_mode": "direct", "lang": "en"},
    )
    assert panel.status_code == 200, panel.text
    assert "The contract checks passed." in panel.text
    assert 'name="evidence"' in panel.text
    html_match = re.search(r'action="([^"]*/methods/generate[^"]*)"', panel.text)
    assert html_match is not None
    action = unescape(html_match[1])
    result = submit(dashboard, scope, family, [ref], path=action)
    assert result.status_code == 303, result.text
    destination = result.headers["location"]
    assert urlsplit(destination).path == "/dashboard/review"
    candidate_id = parse_qs(urlsplit(destination).query)["candidate_id"][0]
    candidate = dashboard.post("/v1/candidates/get", json={"scope_id": scope, "candidate_id": candidate_id}).json()
    assert candidate["status"] == "pending"
    assert candidate["source_refs"] == [ref]
    assert len(generator.inputs) == 1
    assert "The contract checks passed." in generator.inputs[0].evidence[0].content
    assert dashboard.get(destination).status_code == 200


def test_generation_no_op_is_explicit(dashboard, monkeypatch):
    scope = create_scope(dashboard, "No useful candidate")["scope_id"]
    ref = captured(dashboard, scope)
    configure(dashboard, monkeypatch, "experience", no_op=True)
    result = submit(dashboard, scope, "experience", [ref])
    assert result.status_code == 303, result.text
    opened = dashboard.get(result.headers["location"])
    assert "No candidate was generated." in opened.text


def test_generation_rejects_empty_and_foreign_evidence_before_model(dashboard, monkeypatch):
    scope = create_scope(dashboard, "Generation boundary")["scope_id"]
    foreign = create_scope(dashboard, "Foreign scope")["scope_id"]
    ref = captured(dashboard, foreign, "Foreign original text.")
    generator = configure(dashboard, monkeypatch, "experience")
    assert submit(dashboard, scope, "experience", []).status_code == 422
    failed = submit(dashboard, scope, "experience", [ref])
    assert failed.status_code in {403, 422}
    assert "Foreign original text." not in failed.text
    assert generator.inputs == []


def test_generation_rejects_cross_origin_and_viewer_write(dashboard, monkeypatch):
    scope = create_scope(dashboard, "Generation permission")["scope_id"]
    ref = captured(dashboard, scope)
    generator = configure(dashboard, monkeypatch, "experience")
    data = {"scope": scope, "kind": "experience", "evidence_type": "source", "evidence": json.dumps(ref)}
    assert (
        dashboard.post(
            "/dashboard/methods/generate", data=data, headers={"Origin": "https://foreign.example"}
        ).status_code
        == 403
    )
    reader = {"type": "user", "id": "generation-reader"}
    _grant(dashboard, reader, {"type": "scope", "scope_id": scope}, "scope.viewer")
    monkeypatch.setattr(dashboard.app.state.authentication_provider, "_principal", PrincipalRef(**reader))
    assert submit(dashboard, scope, "experience", [ref]).status_code == 403
    assert generator.inputs == []


def test_memory_evidence_uses_exact_revision_and_skill_rejects_memory_mode(dashboard, monkeypatch):
    scope = create_scope(dashboard, "Exact generation evidence")["scope_id"]
    saved = dashboard.post("/v1/memory/remember", json={"scope_id": scope, "kind": "fact", "text": "Saved fact."})
    ref = saved.json()["records"][0]["artifact"]
    generator = configure(dashboard, monkeypatch, "experience")
    response = dashboard.post(
        "/dashboard/methods/generate",
        data={"scope": scope, "kind": "experience", "evidence_type": "atomic-memory", "evidence": json.dumps(ref)},
        headers={"Origin": "http://testserver"},
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text
    assert str(ref["revision"]) in generator.inputs[0].evidence[0].evidence_id
    assert "Saved fact." in generator.inputs[0].evidence[0].content
    invalid = dashboard.get(
        "/dashboard/methods",
        params={
            "scope": scope,
            "kind": "skill",
            "generate": "1",
            "generation_mode": "direct",
            "evidence_type": "atomic-memory",
        },
    )
    assert invalid.status_code == 422


def test_skill_can_generate_from_an_approved_experience(dashboard, monkeypatch):
    scope = create_scope(dashboard, "Experience to Skill")["scope_id"]
    ref = captured(dashboard, scope)
    configure(dashboard, monkeypatch, "experience")
    response = submit(dashboard, scope, "experience", [ref])
    candidate_id = parse_qs(urlsplit(response.headers["location"]).query)["candidate_id"][0]
    approved = dashboard.post(
        "/v1/candidates/approve", json={"scope_id": scope, "candidate_id": candidate_id, "expected_version": 1}
    )
    assert approved.status_code == 200, approved.text
    artifact = approved.json()["result_artifact"]
    generator = configure(dashboard, monkeypatch, "skill")
    panel = dashboard.get(
        "/dashboard/methods",
        params={
            "scope": scope,
            "kind": "skill",
            "generate": "1",
            "generation_mode": "direct",
            "evidence_type": "experience",
            "lang": "en",
        },
    )
    assert panel.status_code == 200, panel.text
    assert "Check the contract before release." in panel.text
    generated = dashboard.post(
        "/dashboard/methods/generate",
        data={"scope": scope, "kind": "skill", "evidence_type": "experience", "evidence": json.dumps(artifact)},
        headers={"Origin": "http://testserver"},
        follow_redirects=False,
    )
    assert generated.status_code == 303, generated.text
    assert "Check the contract before release." in generator.inputs[0].evidence[0].content


def test_unconfigured_generation_reports_failure_without_claiming_success(dashboard):
    scope = create_scope(dashboard, "Generator not configured")["scope_id"]
    ref = captured(dashboard, scope)
    response = submit(dashboard, scope, "experience", [ref])
    assert response.status_code == 503
    assert "location" not in response.headers
    assert "pending candidate" not in response.text.lower()


@pytest.mark.parametrize("evidence", ["not-json", "[]", '{"family":"skill","artifact_id":"x","revision":1}'])
def test_malformed_evidence_is_rejected_without_generation(dashboard, monkeypatch, evidence):
    scope = create_scope(dashboard, "Invalid selection")["scope_id"]
    generator = configure(dashboard, monkeypatch, "experience")
    response = dashboard.post(
        "/dashboard/methods/generate",
        data={"scope": scope, "kind": "experience", "evidence_type": "source", "evidence": evidence},
        headers={"Origin": "http://testserver"},
    )
    assert response.status_code == 422
    assert generator.inputs == []


@pytest.mark.parametrize("family", ["experience", "skill"])
def test_generation_evidence_pagination_keeps_family_and_panel(dashboard, family):
    scope = create_scope(dashboard, "Evidence pages")["scope_id"]
    for index in range(7):
        captured(dashboard, scope, f"Evidence source {index}.")
    first = dashboard.get(
        "/dashboard/methods",
        params={"scope": scope, "kind": family, "generate": "1", "generation_mode": "direct", "lang": "en"},
    )
    assert first.status_code == 200
    next_url = next(unescape(url) for url in re.findall(r'href="([^"]+)"', first.text) if "generation_cursor=" in url)
    query = parse_qs(urlsplit(next_url).query)
    assert query["kind"] == [family]
    assert query["generate"] == ["1"]
    assert query["generation_mode"] == ["direct"]
    second = dashboard.get(next_url)
    assert second.status_code == 200, second.text
    assert 'name="evidence"' in second.text
    assert "Evidence source 0." in second.text


@pytest.mark.parametrize("mode", ["automatic", "review", "disabled", "noop"])
def test_profile_generation_uses_policy_and_reports_result(dashboard, mode):
    scope = create_scope(dashboard, "Generate profile from page")["scope_id"]
    panel = dashboard.get("/dashboard/profile", params={"scope": scope, "lang": "en"})
    assert panel.status_code == 200
    assert "Generate profile" in panel.text
    html_match = re.search(r'action="([^"]*/profile/generate[^"]*)"', panel.text)
    assert html_match is not None
    action = unescape(html_match[1])
    if mode != "disabled":
        policy = dashboard.put(
            f"/v1/scopes/{scope}/profile-policy",
            json={
                "generation_enabled": True,
                "activation_mode": "review_required" if mode == "review" else "automatic",
                "expected_version": 0,
            },
        )
        assert policy.status_code == 200, policy.text

    class ProfileGenerator:
        async def generate(self, value):
            return "# Preferences\n\nUse Chinese."

    dashboard.app.state.application.profiles.generator = ProfileGenerator()
    if mode != "noop":
        captured(dashboard, scope, "I prefer Chinese.")
    result = dashboard.post(
        action, data={"scope": scope}, headers={"Origin": "http://testserver"}, follow_redirects=False
    )
    assert result.status_code == 303, result.text
    opened = dashboard.get(result.headers["location"])
    assert opened.status_code == 200, opened.text
    if mode == "review":
        assert urlsplit(result.headers["location"]).path == "/dashboard/review"
        assert "candidate_id=" in result.headers["location"]
    elif mode == "automatic":
        assert "Profile updated." in opened.text
        assert "Use Chinese." in opened.text
        history = dashboard.get("/dashboard/profile", params={"scope": scope, "revision": 1, "lang": "en"})
        assert "/profile/generate" not in history.text
    else:
        assert ("Profile generation is disabled." if mode == "disabled" else "No new profile changes.") in opened.text


def test_profile_generation_rejects_foreign_origin_and_viewer(dashboard, monkeypatch):
    scope = create_scope(dashboard, "Profile generation permission")["scope_id"]
    assert (
        dashboard.post(
            "/dashboard/profile/generate", data={"scope": scope}, headers={"Origin": "https://foreign.example"}
        ).status_code
        == 403
    )
    reader = {"type": "user", "id": "profile-generation-reader"}
    _grant(dashboard, reader, {"type": "scope", "scope_id": scope}, "scope.viewer")
    monkeypatch.setattr(dashboard.app.state.authentication_provider, "_principal", PrincipalRef(**reader))
    assert (
        dashboard.post(
            "/dashboard/profile/generate", data={"scope": scope}, headers={"Origin": "http://testserver"}
        ).status_code
        == 403
    )
