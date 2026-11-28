# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Verify Dashboard viewing, editing, conflict recovery, and access control for profile.generate."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, cast
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from powercontext.builtin.artifacts.profile.generation import (
    PROFILE_INSTRUCTIONS,
    PROFILE_INSTRUCTIONS_VERSION,
)
from powercontext.builtin.artifacts.profile.service import ProfileGenerationInput, ProfileGenerationOutput
from powercontext.builtin.artifacts.prompt import PROMPT_KEYS, PromptDemonstration, PromptRegistry
from powercontext.builtin.artifacts.prompt.builtin import builtin_prompt_definitions
from powercontext.builtin.authz import PrincipalRef
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.server.dashboard.api import DashboardAPI
from powercontext.server.dashboard.errors import ReadError
from powercontext.server.dashboard.preferences import CATALOGS
from powercontext.server.factory import create_server_app
from powercontext.server.settings import DashboardConfig, McpConfig, ServerSettings
from tests.test_dashboard import create_scope
from tests.test_dashboard import dashboard as dashboard
from tests.test_dashboard_scope_selection import _grant, _revoke

OTHER_DASHBOARD_PROMPT_KEYS = (
    "atomic_memory.extract",
    "atomic_memory.reconcile",
    "memory.rerank",
    "experience.incubate",
    "experience.generate",
    "skill.generate",
    "handoff.generate",
)


@dataclass
class _ParsedEditorForm:
    action: str
    data_scope: str | None
    inputs: dict[str, str] = field(default_factory=dict)
    mode_inputs: list[tuple[str, bool]] = field(default_factory=list)
    mode_options: list[tuple[str, bool]] = field(default_factory=list)
    instructions: str | None = None
    cancel_hrefs: list[str] = field(default_factory=list)

    @property
    def selected_mode(self) -> str | None:
        for value, selected in self.mode_inputs:
            if selected:
                return value
        for value, selected in self.mode_options:
            if selected:
                return value
        return self.inputs.get("mode")


class _EditorFormParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.forms: list[_ParsedEditorForm] = []
        self._current_form: _ParsedEditorForm | None = None
        self._in_select_mode = False
        self._in_textarea = False
        self._textarea_chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = dict(attrs)
        if tag == "form" and "data-profile-prompt-editor" in attr_map:
            self._current_form = _ParsedEditorForm(
                action=attr_map.get("action") or "",
                data_scope=attr_map.get("data-scope"),
            )
            return
        if self._current_form is None:
            return
        if tag == "input":
            self._handle_input(self._current_form, attr_map)
        elif tag == "select" and attr_map.get("name") == "mode":
            self._in_select_mode = True
        elif tag == "option" and self._in_select_mode:
            self._current_form.mode_options.append((attr_map.get("value") or "", "selected" in attr_map))
        elif tag == "textarea" and attr_map.get("name") == "instructions":
            self._in_textarea = True
            self._textarea_chunks = []
        elif tag == "a" and "data-prompt-cancel" in attr_map and attr_map.get("href"):
            self._current_form.cancel_hrefs.append(attr_map["href"] or "")

    @staticmethod
    def _handle_input(form: _ParsedEditorForm, attr_map: dict[str, str | None]) -> None:
        name = attr_map.get("name")
        value = attr_map.get("value") or ""
        if name == "mode":
            input_type = (attr_map.get("type") or "text").lower()
            if input_type == "radio":
                form.mode_inputs.append((value, "checked" in attr_map))
            else:
                form.inputs["mode"] = value
        elif name is not None:
            form.inputs[name] = value

    def handle_endtag(self, tag: str) -> None:
        if self._current_form is None:
            return
        if tag == "select":
            self._in_select_mode = False
        elif tag == "textarea" and self._in_textarea:
            raw = "".join(self._textarea_chunks)
            # HTML5 ignores a single leading newline immediately after <textarea>.
            if raw.startswith("\n"):
                raw = raw[1:]
            self._current_form.instructions = raw
            self._in_textarea = False
        elif tag == "form":
            self.forms.append(self._current_form)
            self._current_form = None

    def handle_data(self, data: str) -> None:
        if self._in_textarea:
            self._textarea_chunks.append(data)


def _extract_editor_form(html: str) -> _ParsedEditorForm | None:
    parser = _EditorFormParser()
    parser.feed(html)
    parser.close()
    return parser.forms[0] if parser.forms else None


def _prompt_card(html: str, prompt_key: str) -> str:
    return next(card for card in html.split("</article>") if f"<code>{prompt_key}</code>" in card)


def _links_for_path(html: str, path: str) -> list[str]:
    return [unescape(url) for url in re.findall(r'href="([^"]+)"', html) if urlsplit(unescape(url)).path == path]


def _set_prompt_registry(
    client: TestClient,
    *,
    supported: frozenset[str] = frozenset(),
    injected: frozenset[str] = frozenset(),
    disabled: frozenset[str] = frozenset(),
) -> PromptRegistry:
    registry = PromptRegistry(
        builtin_prompt_definitions(),
        supported=supported,
        injected=injected,
        disabled=disabled,
    )
    app = cast(FastAPI, client.app)
    runtime = app.state.application
    writer = runtime._records()._family_writers.get("prompt")
    writer._registry = registry
    if runtime._prompt_service is not None:
        runtime._prompt_service.registry = registry
    return registry


def _profile_prompt_demonstration() -> dict[str, Any]:
    sample_input = ProfileGenerationInput(
        previous_content="# Profile\n\n- Uses Python.",
        sources=('{"speaker":"user","text":"Prefers Chinese."}',),
    )
    sample_output = ProfileGenerationOutput(content="# Profile\n\n- Uses Python.\n- Prefers Chinese.")
    demo = PromptDemonstration(
        input=sample_input.model_dump(mode="json"),
        expected_output=sample_output.model_dump(mode="json"),
    )
    return demo.model_dump(mode="json")


def _post_profile_prompt(
    client: TestClient,
    *,
    scope: str,
    mode: str,
    instructions: str,
    expected_etag: str,
    lang: str | None = "en",
    origin: str = "http://testserver",
    follow_redirects: bool = False,
    headers: dict[str, str] | None = None,
):
    query = f"?lang={lang}" if lang else ""
    req_headers = {"Origin": origin, **(headers or {})}
    return client.post(
        f"/dashboard/prompts/profile-generate{query}",
        data={
            "scope": scope,
            "mode": mode,
            "instructions": instructions,
            "expected_etag": expected_etag,
        },
        headers=req_headers,
        follow_redirects=follow_redirects,
    )


def _create_profile(client: TestClient, scope: str, body: str = "# Profile\n\n- Prefers Chinese.") -> dict[str, Any]:
    response = client.post(
        f"/v1/scopes/{scope}/artifacts",
        json={"family": "profile", "content": {"content": body}},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_label_catalogs_have_identical_keys_and_non_empty_copy() -> None:
    root = Path(__file__).resolve().parents[1] / "src" / "powercontext" / "server" / "dashboard"
    zh_labels = json.loads((root / "labels.json").read_text(encoding="utf-8"))
    en_labels = json.loads((root / "labels.en.json").read_text(encoding="utf-8"))
    assert set(zh_labels) == set(en_labels)
    assert set(CATALOGS["zh"]) == set(CATALOGS["en"]) == set(en_labels)
    assert "profile_generation_prompt" in en_labels
    assert "prompt_builtin_matches_effective" in en_labels
    assert zh_labels["profile_generation_prompt"] == "画像生成提示词"
    assert zh_labels["prompt_editor_title"] == "编辑画像提示词"
    for key, value in zh_labels.items():
        assert isinstance(value, str) and value.strip(), f"Empty zh label for {key}"
    for key, value in en_labels.items():
        assert isinstance(value, str) and value.strip(), f"Empty en label for {key}"


@pytest.mark.parametrize("language", ["en", "zh"])
def test_profile_links_to_generation_prompt_and_prompts_view_before_and_after_revision(
    dashboard: TestClient, language: str
) -> None:
    labels = CATALOGS[language]
    scope_info = create_scope(dashboard, f"Profile prompt nav {language}")
    scope = scope_info["scope_id"]

    # Empty Profile state still offers the entry link to profile.generate while keeping generation form separate.
    empty_profile = dashboard.get("/dashboard/profile", params={"scope": scope, "lang": language})
    assert empty_profile.status_code == 200, empty_profile.text
    assert "/dashboard/profile/generate" in empty_profile.text
    assert 'class="btn-list justify-content-md-end"' in empty_profile.text
    assert labels["profile_generation_prompt"] in empty_profile.text
    assert empty_profile.text.index(labels["profile_generation_prompt"]) < empty_profile.text.index(
        labels["profile_generate"]
    )
    prompt_links = [
        url
        for url in _links_for_path(empty_profile.text, "/dashboard/prompts")
        if parse_qs(urlsplit(url).query).get("prompt_key") == ["profile.generate"]
    ]
    assert prompt_links, empty_profile.text
    entry_query = parse_qs(urlsplit(prompt_links[0]).query)
    assert entry_query["scope"] == [scope]
    assert entry_query["lang"] == [language]

    # Pre-revision Prompts page opens profile.generate, displays Scope, defaults, status, and reason.
    opened = dashboard.get(prompt_links[0])
    assert opened.status_code == 200, opened.text
    assert scope_info["title"] in opened.text
    assert f"<code>{scope}</code>" in opened.text
    for key in (*OTHER_DASHBOARD_PROMPT_KEYS, "profile.generate"):
        assert f"<code>{key}</code>" in opened.text
    profile_card = _prompt_card(opened.text, "profile.generate")
    assert labels["profile.generate"] in profile_card
    assert re.search(r'<details\b[^>]*class="[^"]*prompt-record[^"]*"[^>]*\bopen\b', profile_card)
    assert f"{labels['prompt_mode']}: {labels['prompt_auto']}" in profile_card
    assert labels["prompt_state_disabled"] in profile_card
    assert "provider_not_configured" in profile_card
    assert PROFILE_INSTRUCTIONS_VERSION in profile_card
    assert PROFILE_INSTRUCTIONS.strip() in unescape(profile_card)
    assert _extract_editor_form(opened.text) is None
    for key in OTHER_DASHBOARD_PROMPT_KEYS:
        card = _prompt_card(opened.text, key)
        assert "/dashboard/prompts/profile-generate" not in card

    # Populated Profile state keeps the same entry link and generation button separate.
    _create_profile(dashboard, scope)
    populated_profile = dashboard.get("/dashboard/profile", params={"scope": scope, "lang": language})
    assert populated_profile.status_code == 200, populated_profile.text
    assert "/dashboard/profile/generate" in populated_profile.text
    assert any(
        parse_qs(urlsplit(url).query).get("prompt_key") == ["profile.generate"]
        for url in _links_for_path(populated_profile.text, "/dashboard/prompts")
    )

    # Supported Auto mode clarifies that the displayed effective instructions match the built-in template.
    _set_prompt_registry(dashboard, supported=frozenset(PROMPT_KEYS))
    auto_supported = dashboard.get(
        "/dashboard/prompts", params={"scope": scope, "prompt_key": "profile.generate", "lang": language}
    )
    assert auto_supported.status_code == 200, auto_supported.text
    assert labels["prompt_builtin_matches_effective"] in _prompt_card(auto_supported.text, "profile.generate")

    # After creating the first Prompt revision, effective custom instructions and defaults both render,
    # while the other 7 keys remain strictly read-only.
    created = dashboard.post(
        f"/v1/scopes/{scope}/artifacts",
        json={
            "family": "prompt",
            "prompt_key": "profile.generate",
            "content": {
                "schema_version": "powercontext.prompt.v1",
                "mode": "custom",
                "instructions": "Keep only durable engineering preferences.",
                "demonstrations": [],
            },
        },
    )
    assert created.status_code == 201, created.text
    after_prompt = dashboard.get(
        "/dashboard/prompts", params={"scope": scope, "prompt_key": "profile.generate", "lang": language}
    )
    assert after_prompt.status_code == 200, after_prompt.text
    card_after = _prompt_card(after_prompt.text, "profile.generate")
    assert f"{labels['prompt_mode']}: {labels['prompt_custom']}" in card_after
    assert f"{labels['prompt_revision']} 1" in card_after
    assert labels["prompt_builtin_template"] in card_after
    assert labels["prompt_builtin_matches_effective"] not in card_after
    assert "Keep only durable engineering preferences." in card_after
    assert PROFILE_INSTRUCTIONS_VERSION in card_after
    assert PROFILE_INSTRUCTIONS.strip() in unescape(card_after)

    other_opened = dashboard.get(
        "/dashboard/prompts", params={"scope": scope, "prompt_key": "experience.generate", "lang": language}
    )
    assert other_opened.status_code == 200, other_opened.text
    experience_card = _prompt_card(other_opened.text, "experience.generate")
    assert re.search(r'<details\b[^>]*class="[^"]*prompt-record[^"]*"[^>]*\bopen\b', experience_card)
    assert "/dashboard/prompts/profile-generate" not in experience_card
    assert _extract_editor_form(experience_card) is None

    # Direct /dashboard/prompts access without prompt_key still exposes the profile.generate editor
    # inside its details block while keeping all sibling prompts read-only, and Scope switching preserves prompt_key.
    direct_prompts = dashboard.get("/dashboard/prompts", params={"scope": scope, "lang": language})
    assert direct_prompts.status_code == 200, direct_prompts.text
    direct_form = _extract_editor_form(direct_prompts.text)
    assert direct_form is not None and direct_form.data_scope == scope
    for key in OTHER_DASHBOARD_PROMPT_KEYS:
        assert "/dashboard/prompts/profile-generate" not in _prompt_card(direct_prompts.text, key)
    assert '<input type="hidden" name="prompt_key" value="profile.generate">' in after_prompt.text


@pytest.mark.parametrize("language", ["en", "zh"])
@pytest.mark.parametrize("public_reads", [False, True])
def test_injected_profile_prompt_shows_unavailable_without_editing(
    dashboard: TestClient, monkeypatch: pytest.MonkeyPatch, language: str, public_reads: bool
) -> None:
    labels = CATALOGS[language]
    _set_prompt_registry(dashboard, injected=frozenset({"profile.generate"}))
    scope = create_scope(dashboard, f"Injected prompt {language} {public_reads}")["scope_id"]
    if public_reads:
        monkeypatch.setattr("powercontext.server.dashboard.api._builtin_runtime", lambda request: None)

    page = dashboard.get(
        "/dashboard/prompts", params={"scope": scope, "prompt_key": "profile.generate", "lang": language}
    )
    assert page.status_code == 200, page.text
    card = _prompt_card(page.text, "profile.generate")
    assert re.search(r'<details\b[^>]*class="[^"]*prompt-record[^"]*"[^>]*\bopen\b', card)
    assert "injected_component" in card
    assert labels["prompt_unsupported"] in card or labels.get("prompt_state_unsupported", "") in card
    assert "unavailable" in card.lower() or "不可用" in card
    assert PROFILE_INSTRUCTIONS.strip() not in unescape(card)
    assert _extract_editor_form(page.text) is None


def test_starting_custom_prefills_defaults_cancel_writes_nothing_and_invalid_422_preserves_draft(
    dashboard: TestClient,
) -> None:
    _set_prompt_registry(dashboard, supported=frozenset({"profile.generate"}))
    scope = create_scope(dashboard, "Start custom defaults")["scope_id"]

    page = dashboard.get("/dashboard/prompts", params={"scope": scope, "prompt_key": "profile.generate", "lang": "en"})
    assert page.status_code == 200, page.text
    form = _extract_editor_form(page.text)
    assert form is not None
    assert form.data_scope == scope
    assert form.inputs.get("scope") == scope
    assert form.inputs.get("expected_etag") == ""
    assert form.instructions is not None
    assert form.instructions.strip() == PROFILE_INSTRUCTIONS.strip()
    assert form.cancel_hrefs
    assert "beforeunload" in page.text
    assert "htmx:confirm" in page.text
    assert "data-profile-prompt-editor" in page.text

    # Cancel link must never write a revision.
    cancelled = dashboard.get(form.cancel_hrefs[0])
    assert cancelled.status_code == 200, cancelled.text
    assert dashboard.get(f"/v1/scopes/{scope}/artifacts/prompt").json()["items"] == []
    config = dashboard.get(f"/v1/scopes/{scope}/prompts/profile.generate").json()
    assert config["mode"] == "auto" and config["artifact"] is None

    # Blank Custom instructions return 422 HTML, write nothing, and preserve draft.
    blank_draft = "   \n\t  "
    blank_resp = _post_profile_prompt(dashboard, scope=scope, mode="custom", instructions=blank_draft, expected_etag="")
    assert blank_resp.status_code == 422, blank_resp.text
    assert blank_resp.headers.get("X-Dashboard-HTML") == "1"
    assert 'role="alert"' in blank_resp.text
    blank_form = _extract_editor_form(blank_resp.text)
    assert blank_form is not None
    assert blank_form.selected_mode == "custom"
    assert blank_form.instructions == blank_draft
    assert blank_form.inputs.get("expected_etag") == ""
    assert dashboard.get(f"/v1/scopes/{scope}/artifacts/prompt").json()["items"] == []

    # Oversized Custom instructions (> 32_768 chars, under 256_000 bytes) return 422 preserving draft.
    too_long = "a" * 32_769
    long_resp = _post_profile_prompt(dashboard, scope=scope, mode="custom", instructions=too_long, expected_etag="")
    assert long_resp.status_code == 422, long_resp.text
    assert long_resp.headers.get("X-Dashboard-HTML") == "1"
    long_form = _extract_editor_form(long_resp.text)
    assert long_form is not None
    assert long_form.selected_mode == "custom"
    assert long_form.instructions == too_long
    assert dashboard.get(f"/v1/scopes/{scope}/artifacts/prompt").json()["items"] == []

    # Invalid mode is rejected with 422 and writes nothing.
    invalid_mode = _post_profile_prompt(
        dashboard, scope=scope, mode="invalid-mode", instructions="Draft text", expected_etag=""
    )
    assert invalid_mode.status_code == 422, invalid_mode.text
    assert dashboard.get(f"/v1/scopes/{scope}/artifacts/prompt").json()["items"] == []


def test_first_save_verifies_readback_without_inference_enabling_or_reprocessing(
    dashboard: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_prompt_registry(dashboard, supported=frozenset({"profile.generate"}))
    scope = create_scope(dashboard, "First save no side effects")["scope_id"]

    # Seed historical Source without enabling profile generation.
    source_resp = dashboard.post(f"/v1/scopes/{scope}/sources", json={"content": "I prefer concise changelogs."})
    assert source_resp.status_code == 201, source_resp.text

    inference_calls: list[Any] = []

    class ForbiddenGenerator:
        async def generate(self, value: ProfileGenerationInput) -> str | None:
            inference_calls.append(value)
            pytest.fail("Prompt viewing or saving must never invoke Profile inference")

    async def forbidden_open_model(*args: Any, **kwargs: Any) -> Any:
        inference_calls.append((args, kwargs))
        pytest.fail("Prompt viewing or saving must never open an inference model")

    cast(Any, dashboard.app).state.application.profiles.generator = ForbiddenGenerator()
    monkeypatch.setattr("powercontext.builtin.runtime.composition._open_pydantic_ai_model", forbidden_open_model)

    saved_instructions = "Keep enduring engineering preferences and release style notes."
    response = _post_profile_prompt(
        dashboard, scope=scope, mode="custom", instructions=saved_instructions, expected_etag=""
    )
    assert response.status_code == 303, response.text
    location = response.headers["location"]
    parsed_location = urlsplit(location)
    assert parsed_location.path == "/dashboard/prompts"
    location_query = parse_qs(parsed_location.query)
    assert location_query["scope"] == [scope]
    assert location_query["prompt_key"] == ["profile.generate"]
    assert location_query["prompt_saved"] == ["1"]
    assert location_query["lang"] == ["en"]

    # Successful readback shows saved banner, rev 1, custom instructions, and updated ETag.
    readback = dashboard.get(location)
    assert readback.status_code == 200, readback.text
    assert 'role="status"' in readback.text or "alert-success" in readback.text
    card = _prompt_card(readback.text, "profile.generate")
    assert saved_instructions in card
    assert f"{CATALOGS['en']['prompt_revision']} 1" in card
    form = _extract_editor_form(readback.text)
    assert form is not None
    assert form.inputs.get("expected_etag") == '"revision:1"'
    assert form.instructions == saved_instructions

    # Backend verifies persisted prompt, no inference, no profile policy enablement, no generated profile.
    stored = dashboard.get(f"/v1/scopes/{scope}/prompts/profile.generate").json()
    assert stored["mode"] == "custom"
    assert stored["artifact"]["revision"] == 1
    assert stored["effective"]["instructions"] == saved_instructions
    assert inference_calls == []
    assert dashboard.get(f"/v1/scopes/{scope}/artifacts/profile").json()["items"] == []
    assert dashboard.get(f"/v1/scopes/{scope}/profile-policy").status_code == 404

    # Readback failure never claims success even when prompt_saved=1 is in the query string.
    fresh_scope = create_scope(dashboard, "Unsaved readback")["scope_id"]
    unpersisted = dashboard.get(
        "/dashboard/prompts",
        params={"scope": fresh_scope, "prompt_key": "profile.generate", "prompt_saved": "1", "lang": "en"},
    )
    assert unpersisted.status_code == 200, unpersisted.text
    assert "alert-success" not in unpersisted.text
    assert 'role="status"' not in _prompt_card(unpersisted.text, "profile.generate")

    async def failing_prompt_configs(api: DashboardAPI, selected_scope: str, keys: tuple[str, ...]):
        return [ReadError(503, "service_unavailable") for _ in keys]

    monkeypatch.setattr(DashboardAPI, "prompt_configurations", failing_prompt_configs)
    failed_readback = dashboard.get(location)
    assert failed_readback.status_code == 200, failed_readback.text
    assert "alert-success" not in failed_readback.text
    assert CATALOGS["en"]["error_503"] in failed_readback.text


def test_replacing_custom_head_and_existing_auto_head_uses_if_match(dashboard: TestClient) -> None:
    _set_prompt_registry(dashboard, supported=frozenset({"profile.generate"}))
    scope = create_scope(dashboard, "Replace custom and auto heads")["scope_id"]

    # Revision 1: initial Custom head via POST.
    r1 = _post_profile_prompt(dashboard, scope=scope, mode="custom", instructions="Custom v1", expected_etag="")
    assert r1.status_code == 303, r1.text

    # Revision 2: replace Custom head via PUT with If-Match: "revision:1".
    page1 = dashboard.get("/dashboard/prompts", params={"scope": scope, "prompt_key": "profile.generate", "lang": "en"})
    form1 = _extract_editor_form(page1.text)
    assert form1 is not None and form1.inputs.get("expected_etag") == '"revision:1"'
    r2 = _post_profile_prompt(
        dashboard, scope=scope, mode="custom", instructions="Custom v2", expected_etag='"revision:1"'
    )
    assert r2.status_code == 303, r2.text
    rev2 = dashboard.get(f"/v1/scopes/{scope}/prompts/profile.generate").json()
    assert rev2["artifact"]["revision"] == 2
    assert rev2["effective"]["instructions"] == "Custom v2"

    # Revision 3: switch explicitly to Auto head via PUT with If-Match: "revision:2".
    r3 = _post_profile_prompt(
        dashboard, scope=scope, mode="auto", instructions="Custom v2", expected_etag='"revision:2"'
    )
    assert r3.status_code == 303, r3.text
    rev3 = dashboard.get(f"/v1/scopes/{scope}/prompts/profile.generate").json()
    assert rev3["mode"] == "auto"
    assert rev3["artifact"]["revision"] == 3
    assert rev3["artifact_etag"] == '"revision:3"'

    # Revision 4: replace existing Auto head (artifact is NOT None!) via PUT with If-Match: "revision:3".
    page3 = dashboard.get("/dashboard/prompts", params={"scope": scope, "prompt_key": "profile.generate", "lang": "en"})
    form3 = _extract_editor_form(page3.text)
    assert form3 is not None
    assert form3.inputs.get("expected_etag") == '"revision:3"'
    assert form3.instructions is not None and form3.instructions.strip() == PROFILE_INSTRUCTIONS.strip()
    r4 = _post_profile_prompt(
        dashboard,
        scope=scope,
        mode="custom",
        instructions="Custom after Auto head",
        expected_etag='"revision:3"',
    )
    assert r4.status_code == 303, r4.text
    rev4 = dashboard.get(f"/v1/scopes/{scope}/prompts/profile.generate").json()
    assert rev4["mode"] == "custom"
    assert rev4["artifact"]["revision"] == 4
    assert rev4["effective"]["instructions"] == "Custom after Auto head"


def test_concurrent_creation_409_and_revision_412_preserve_draft_and_expose_latest_etag(
    dashboard: TestClient,
) -> None:
    _set_prompt_registry(dashboard, supported=frozenset({"profile.generate"}))
    scope = create_scope(dashboard, "Concurrent conflicts")["scope_id"]

    # Concurrent editor B creates revision 1 before editor A's first save lands.
    editor_b_1 = dashboard.post(
        f"/v1/scopes/{scope}/artifacts",
        json={
            "family": "prompt",
            "prompt_key": "profile.generate",
            "content": {
                "schema_version": "powercontext.prompt.v1",
                "mode": "custom",
                "instructions": "Editor B saved rev 1.",
                "demonstrations": [],
            },
        },
    )
    assert editor_b_1.status_code == 201, editor_b_1.text

    conflict_409 = _post_profile_prompt(
        dashboard,
        scope=scope,
        mode="custom",
        instructions="Editor A unsaved creation draft.",
        expected_etag="",
    )
    assert conflict_409.status_code == 409, conflict_409.text
    assert conflict_409.headers.get("X-Dashboard-HTML") == "1"
    assert 'role="alert"' in conflict_409.text
    assert "Editor B saved rev 1." in conflict_409.text
    assert any(
        parse_qs(urlsplit(url).query).get("prompt_key") == ["profile.generate"]
        for url in _links_for_path(conflict_409.text, "/dashboard/prompts")
    )
    # Did not overwrite Editor B's revision.
    current_after_409 = dashboard.get(f"/v1/scopes/{scope}/prompts/profile.generate").json()
    assert current_after_409["artifact"]["revision"] == 1
    assert current_after_409["effective"]["instructions"] == "Editor B saved rev 1."
    # Preserved Editor A's draft and updated expected_etag to reconcile_etag '"revision:1"'.
    form_409 = _extract_editor_form(conflict_409.text)
    assert form_409 is not None
    assert form_409.selected_mode == "custom"
    assert form_409.instructions == "Editor A unsaved creation draft."
    assert form_409.inputs.get("expected_etag") == '"revision:1"'

    # Reconciling with the refreshed ETag succeeds and writes revision 2.
    reconciled_from_409 = _post_profile_prompt(
        dashboard,
        scope=scope,
        mode="custom",
        instructions="Editor A reconciled rev 2.",
        expected_etag='"revision:1"',
    )
    assert reconciled_from_409.status_code == 303, reconciled_from_409.text

    # Now Editor B advances to revision 3 while Editor A holds stale ETag '"revision:2"'.
    editor_b_3 = dashboard.put(
        f"/v1/scopes/{scope}/artifacts/prompt/profile.generate",
        headers={"If-Match": '"revision:2"'},
        json={
            "content": {
                "schema_version": "powercontext.prompt.v1",
                "mode": "custom",
                "instructions": "Editor B saved rev 3.",
                "demonstrations": [],
            }
        },
    )
    assert editor_b_3.status_code == 200, editor_b_3.text

    conflict_412 = _post_profile_prompt(
        dashboard,
        scope=scope,
        mode="custom",
        instructions="Editor A stale rev 2 draft.",
        expected_etag='"revision:2"',
    )
    assert conflict_412.status_code == 412, conflict_412.text
    assert conflict_412.headers.get("X-Dashboard-HTML") == "1"
    assert 'role="alert"' in conflict_412.text
    assert "Editor B saved rev 3." in conflict_412.text
    assert any(
        parse_qs(urlsplit(url).query).get("prompt_key") == ["profile.generate"]
        for url in _links_for_path(conflict_412.text, "/dashboard/prompts")
    )
    current_after_412 = dashboard.get(f"/v1/scopes/{scope}/prompts/profile.generate").json()
    assert current_after_412["artifact"]["revision"] == 3
    assert current_after_412["effective"]["instructions"] == "Editor B saved rev 3."
    form_412 = _extract_editor_form(conflict_412.text)
    assert form_412 is not None
    assert form_412.selected_mode == "custom"
    assert form_412.instructions == "Editor A stale rev 2 draft."
    assert form_412.inputs.get("expected_etag") == '"revision:3"'


def test_dashboard_preserves_existing_demonstrations_across_edits_and_clears_on_auto(
    dashboard: TestClient,
) -> None:
    _set_prompt_registry(dashboard, supported=frozenset({"profile.generate"}))
    scope = create_scope(dashboard, "Preserve demonstrations")["scope_id"]
    demo = _profile_prompt_demonstration()

    created = dashboard.post(
        f"/v1/scopes/{scope}/artifacts",
        json={
            "family": "prompt",
            "prompt_key": "profile.generate",
            "content": {
                "schema_version": "powercontext.prompt.v1",
                "mode": "custom",
                "instructions": "Initial custom guidance.",
                "demonstrations": [demo],
            },
        },
    )
    assert created.status_code == 201, created.text

    opened = dashboard.get(
        "/dashboard/prompts", params={"scope": scope, "prompt_key": "profile.generate", "lang": "en"}
    )
    assert opened.status_code == 200, opened.text
    assert f"{CATALOGS['en']['prompt_demonstrations']} 1" in _prompt_card(opened.text, "profile.generate")

    edited = _post_profile_prompt(
        dashboard,
        scope=scope,
        mode="custom",
        instructions="Updated guidance keeping demonstrations.",
        expected_etag='"revision:1"',
    )
    assert edited.status_code == 303, edited.text

    rev2_artifact = dashboard.get(f"/v1/scopes/{scope}/artifacts/prompt/profile.generate").json()
    assert rev2_artifact["revision"] == 2
    assert rev2_artifact["content"] == {
        "schema_version": "powercontext.prompt.v1",
        "mode": "custom",
        "instructions": "Updated guidance keeping demonstrations.",
        "demonstrations": [demo],
    }

    returned_auto = _post_profile_prompt(
        dashboard,
        scope=scope,
        mode="auto",
        instructions="Unsaved text cleared on return to auto.",
        expected_etag='"revision:2"',
    )
    assert returned_auto.status_code == 303, returned_auto.text

    rev3_artifact = dashboard.get(f"/v1/scopes/{scope}/artifacts/prompt/profile.generate").json()
    assert rev3_artifact["revision"] == 3
    assert rev3_artifact["content"] == {
        "schema_version": "powercontext.prompt.v1",
        "mode": "auto",
        "instructions": "",
        "demonstrations": [],
    }


def test_return_to_auto_and_immutable_history_rollback_create_new_revision(dashboard: TestClient) -> None:
    _set_prompt_registry(dashboard, supported=frozenset({"profile.generate"}))
    scope = create_scope(dashboard, "Prompt history and rollback")["scope_id"]
    demo = _profile_prompt_demonstration()

    rev1 = dashboard.post(
        f"/v1/scopes/{scope}/artifacts",
        json={
            "family": "prompt",
            "prompt_key": "profile.generate",
            "content": {
                "schema_version": "powercontext.prompt.v1",
                "mode": "custom",
                "instructions": "Approved v1 profile instructions.",
                "demonstrations": [demo],
            },
        },
    )
    assert rev1.status_code == 201, rev1.text

    # Returning to Auto saves empty instructions & demonstrations while preserving rev 1 in history.
    to_auto = _post_profile_prompt(
        dashboard,
        scope=scope,
        mode="auto",
        instructions="Leftover text ignored by Auto",
        expected_etag='"revision:1"',
    )
    assert to_auto.status_code == 303, to_auto.text
    rev2 = dashboard.get(f"/v1/scopes/{scope}/artifacts/prompt/profile.generate/revisions/2").json()
    assert rev2["content"] == {
        "schema_version": "powercontext.prompt.v1",
        "mode": "auto",
        "instructions": "",
        "demonstrations": [],
    }
    config_auto = dashboard.get(f"/v1/scopes/{scope}/prompts/profile.generate").json()
    assert config_auto["mode"] == "auto"
    assert config_auto["effective"]["instructions"] == PROFILE_INSTRUCTIONS

    # Opening historical rev 1 shows immutable rev 1 details without the live head editor,
    # supports comparison, and offers rollback.
    historical = dashboard.get(
        "/dashboard/prompts",
        params={"scope": scope, "prompt_key": "profile.generate", "prompt_revision": "1", "compare": "2", "lang": "en"},
    )
    assert historical.status_code == 200, historical.text
    assert _extract_editor_form(historical.text) is None
    assert "Approved v1 profile instructions." in historical.text
    assert 'action="/dashboard/rollback"' in historical.text
    assert 'name="family" value="prompt"' in historical.text
    assert 'name="artifact_id" value="profile.generate"' in historical.text

    # Stale rollback redirects back to /dashboard/prompts with conflict error without writing rev 3.
    stale_rollback = dashboard.post(
        "/dashboard/rollback",
        data={
            "scope": scope,
            "family": "prompt",
            "artifact_id": "profile.generate",
            "source_revision": "1",
            "expected_revision": "1",
            "reason": "Stale attempt",
        },
        headers={"Origin": "http://testserver"},
        follow_redirects=False,
    )
    assert stale_rollback.status_code == 303, stale_rollback.text
    stale_query = parse_qs(urlsplit(stale_rollback.headers["location"]).query)
    assert urlsplit(stale_rollback.headers["location"]).path == "/dashboard/prompts"
    assert stale_query["prompt_key"] == ["profile.generate"]
    assert stale_query["rollback_error"] == ["revision_conflict"]
    assert dashboard.get(f"/v1/scopes/{scope}/artifacts/prompt/profile.generate").json()["revision"] == 2

    # Valid rollback creates revision 3 matching revision 1 while keeping rev 1 and rev 2 immutable.
    rolled_back = dashboard.post(
        "/dashboard/rollback",
        data={
            "scope": scope,
            "family": "prompt",
            "artifact_id": "profile.generate",
            "source_revision": "1",
            "expected_revision": "2",
            "reason": "Restore approved v1 profile prompt",
        },
        headers={"Origin": "http://testserver"},
        follow_redirects=False,
    )
    assert rolled_back.status_code == 303, rolled_back.text
    rb_url = rolled_back.headers["location"]
    assert urlsplit(rb_url).path == "/dashboard/prompts"
    rb_query = parse_qs(urlsplit(rb_url).query)
    assert rb_query["scope"] == [scope]
    assert rb_query["prompt_key"] == ["profile.generate"]
    assert rb_query["prompt_revision"] == ["3"]
    assert "rollback_error" not in rb_query

    followed = dashboard.get(rb_url)
    assert followed.status_code == 200, followed.text
    assert "Approved v1 profile instructions." in followed.text

    head = dashboard.get(f"/v1/scopes/{scope}/artifacts/prompt/profile.generate").json()
    assert head["revision"] == 3
    assert head["restored_from_revision"] == 1
    assert head["reason"] == "Restore approved v1 profile prompt"
    rev1_content = dashboard.get(f"/v1/scopes/{scope}/artifacts/prompt/profile.generate/revisions/1").json()["content"]
    assert head["content"] == rev1_content
    assert (
        dashboard.get(f"/v1/scopes/{scope}/artifacts/prompt/profile.generate/revisions/1").json()["content"]
        == rev1_content
    )
    assert (
        dashboard.get(f"/v1/scopes/{scope}/artifacts/prompt/profile.generate/revisions/2").json()["content"]
        == rev2["content"]
    )


@pytest.mark.parametrize(
    ("disabled_set", "expected_reason"),
    [
        (frozenset(), "provider_not_configured"),
        (frozenset({"profile.generate"}), "operation_disabled"),
    ],
)
def test_disabled_capability_is_readable_rejects_custom_preserving_draft_and_allows_return_to_auto(
    dashboard: TestClient, disabled_set: frozenset[str], expected_reason: str
) -> None:
    _set_prompt_registry(dashboard, disabled=disabled_set)
    scope = create_scope(dashboard, f"Disabled prompt {expected_reason}")["scope_id"]

    # Readable in Auto mode with status & reason, without a Custom creation editor.
    opened = dashboard.get(
        "/dashboard/prompts", params={"scope": scope, "prompt_key": "profile.generate", "lang": "en"}
    )
    assert opened.status_code == 200, opened.text
    card = _prompt_card(opened.text, "profile.generate")
    assert CATALOGS["en"]["prompt_state_disabled"] in card
    assert expected_reason in card
    assert PROFILE_INSTRUCTIONS.strip() in unescape(card)
    assert _extract_editor_form(opened.text) is None

    # Direct POST in Custom mode is rejected with 422, writes nothing, and preserves draft.
    blocked_draft = "Custom draft submitted while capability is disabled."
    rejected = _post_profile_prompt(dashboard, scope=scope, mode="custom", instructions=blocked_draft, expected_etag="")
    assert rejected.status_code == 422, rejected.text
    assert rejected.headers.get("X-Dashboard-HTML") == "1"
    assert 'role="alert"' in rejected.text
    rejected_form = _extract_editor_form(rejected.text)
    assert rejected_form is not None
    assert rejected_form.selected_mode == "custom"
    assert rejected_form.instructions == blocked_draft
    assert dashboard.get(f"/v1/scopes/{scope}/artifacts/prompt").json()["items"] == []

    # When an existing Custom head exists while capability is disabled, returning to Auto still works.
    _set_prompt_registry(dashboard, supported=frozenset({"profile.generate"}))
    created = dashboard.post(
        f"/v1/scopes/{scope}/artifacts",
        json={
            "family": "prompt",
            "prompt_key": "profile.generate",
            "content": {
                "schema_version": "powercontext.prompt.v1",
                "mode": "custom",
                "instructions": "Pre-existing custom instructions.",
                "demonstrations": [],
            },
        },
    )
    assert created.status_code == 201, created.text
    _set_prompt_registry(dashboard, disabled=disabled_set)

    custom_while_disabled = dashboard.get(
        "/dashboard/prompts", params={"scope": scope, "prompt_key": "profile.generate", "lang": "en"}
    )
    assert custom_while_disabled.status_code == 200, custom_while_disabled.text
    assert _extract_editor_form(custom_while_disabled.text) is not None

    blocked_custom_replace = _post_profile_prompt(
        dashboard,
        scope=scope,
        mode="custom",
        instructions="Cannot replace custom while disabled.",
        expected_etag='"revision:1"',
    )
    assert blocked_custom_replace.status_code == 422, blocked_custom_replace.text
    assert CATALOGS["en"]["prompt_write_error_capability_disabled"] in blocked_custom_replace.text
    assert CATALOGS["en"]["prompt_write_error_invalid_request"] not in blocked_custom_replace.text
    form_blocked_replace = _extract_editor_form(blocked_custom_replace.text)
    assert form_blocked_replace is not None
    assert form_blocked_replace.instructions == "Cannot replace custom while disabled."
    assert form_blocked_replace.inputs.get("expected_etag") == '"revision:1"'

    returned_auto = _post_profile_prompt(
        dashboard,
        scope=scope,
        mode="auto",
        instructions="",
        expected_etag='"revision:1"',
    )
    assert returned_auto.status_code == 303, returned_auto.text
    assert dashboard.get(f"/v1/scopes/{scope}/prompts/profile.generate").json()["mode"] == "auto"


def test_enforced_permissions_require_current_scope_admin_even_for_prompt_owner(
    dashboard: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_prompt_registry(dashboard, supported=frozenset({"profile.generate"}))
    scope = create_scope(dashboard, "Enforced prompt admin")["scope_id"]
    authentication = cast(FastAPI, dashboard.app).state.authentication_provider

    # Cross-origin POST is rejected with 403 and writes nothing.
    cross_origin = _post_profile_prompt(
        dashboard,
        scope=scope,
        mode="custom",
        instructions="Cross origin attempt",
        expected_etag="",
        origin="https://foreign.example",
    )
    assert cross_origin.status_code == 403, cross_origin.text
    assert 'role="alert"' in cross_origin.text
    assert CATALOGS["en"]["error_403"] in cross_origin.text
    assert dashboard.get(f"/v1/scopes/{scope}/artifacts/prompt").json()["items"] == []

    # scope.viewer and scope.contributor can read prompts without editor and are rejected with 403 preserving draft.
    for role in ("scope.viewer", "scope.contributor"):
        principal = PrincipalRef(type="user", id=f"prompt-{role}")
        _grant(
            dashboard,
            {"type": principal.type, "id": principal.id},
            {"type": "scope", "scope_id": scope},
            role,
        )
        with monkeypatch.context() as patch:
            patch.setattr(authentication, "_principal", principal)
            read_page = dashboard.get(
                "/dashboard/prompts", params={"scope": scope, "prompt_key": "profile.generate", "lang": "en"}
            )
            assert read_page.status_code == 200, read_page.text
            assert _extract_editor_form(read_page.text) is None

            draft = f"Unauthorized draft from {role}"
            write_resp = _post_profile_prompt(
                dashboard, scope=scope, mode="custom", instructions=draft, expected_etag=""
            )
            assert write_resp.status_code == 403, write_resp.text
            assert write_resp.headers.get("X-Dashboard-HTML") == "1"
            assert 'role="alert"' in write_resp.text
            preserved = _extract_editor_form(write_resp.text)
            assert preserved is not None
            assert preserved.selected_mode == "custom"
            assert preserved.instructions == draft
        assert dashboard.get(f"/v1/scopes/{scope}/artifacts/prompt").json()["items"] == []

    # A former scope.admin who created the Prompt artifact (and retains artifact ownership)
    # loses write access once scope.admin is revoked.
    former_admin = PrincipalRef(type="user", id="former-prompt-admin")
    admin_binding_resp = dashboard.post(
        "/v1/access/bindings/create",
        json={
            "subject": {"type": former_admin.type, "id": former_admin.id},
            "resource": {"type": "scope", "scope_id": scope},
            "role": "scope.admin",
            "idempotency_key": f"former-admin-scope.admin-{scope}",
        },
    )
    assert admin_binding_resp.status_code == 201, admin_binding_resp.text
    admin_binding = admin_binding_resp.json()
    viewer_binding_resp = dashboard.post(
        "/v1/access/bindings/create",
        json={
            "subject": {"type": former_admin.type, "id": former_admin.id},
            "resource": {"type": "scope", "scope_id": scope},
            "role": "scope.viewer",
            "idempotency_key": f"former-admin-scope.viewer-{scope}",
        },
    )
    assert viewer_binding_resp.status_code == 201, viewer_binding_resp.text
    viewer_binding = viewer_binding_resp.json()
    with monkeypatch.context() as patch:
        patch.setattr(authentication, "_principal", former_admin)
        created = dashboard.post(
            f"/v1/scopes/{scope}/artifacts",
            json={
                "family": "prompt",
                "prompt_key": "profile.generate",
                "content": {
                    "schema_version": "powercontext.prompt.v1",
                    "mode": "custom",
                    "instructions": "Created while holding scope.admin.",
                    "demonstrations": [],
                },
            },
        )
        assert created.status_code == 201, created.text

    # Revoke scope.admin while keeping scope.viewer; the creator stays the Prompt's creator.
    _revoke(dashboard, admin_binding)
    with monkeypatch.context() as patch:
        patch.setattr(authentication, "_principal", former_admin)
        read_after_revoke = dashboard.get(
            "/dashboard/prompts", params={"scope": scope, "prompt_key": "profile.generate", "lang": "en"}
        )
        assert read_after_revoke.status_code == 200, read_after_revoke.text
        assert _extract_editor_form(read_after_revoke.text) is None

        revoked_draft = "Draft after scope.admin revocation."
        denied = _post_profile_prompt(
            dashboard,
            scope=scope,
            mode="custom",
            instructions=revoked_draft,
            expected_etag='"revision:1"',
        )
        assert denied.status_code == 403, denied.text
        assert denied.headers.get("X-Dashboard-HTML") == "1"
        preserved = _extract_editor_form(denied.text)
        assert preserved is not None
        assert preserved.selected_mode == "custom"
        assert preserved.instructions == revoked_draft

    # Also verify revocation with no remaining scope role at all (artifact.owner only) still rejects with 403.
    _revoke(dashboard, viewer_binding)
    with monkeypatch.context() as patch:
        patch.setattr(authentication, "_principal", former_admin)
        denied_no_scope_role = _post_profile_prompt(
            dashboard,
            scope=scope,
            mode="custom",
            instructions="Owner-only draft",
            expected_etag='"revision:1"',
        )
        assert denied_no_scope_role.status_code == 403, denied_no_scope_role.text

    assert dashboard.get(f"/v1/scopes/{scope}/artifacts/prompt/profile.generate").json()["revision"] == 1

    # Unauthenticated POST redirects/renders 401 login without exposing saved content.
    token = dashboard.headers.pop("Authorization").removeprefix("Bearer ")
    try:
        unauth = _post_profile_prompt(
            dashboard,
            scope=scope,
            mode="custom",
            instructions="Unauthenticated draft",
            expected_etag='"revision:1"',
        )
        assert unauth.status_code == 401, unauth.text
    finally:
        dashboard.headers["Authorization"] = f"Bearer {token}"


def test_disabled_access_mode_allows_profile_prompt_editing(tmp_path: Path) -> None:
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path}/open-prompts.db"),
            dashboard=DashboardConfig(enabled=True),
            mcp=McpConfig(enabled=False),
        ),
    )
    with TestClient(app) as client:
        _set_prompt_registry(client, supported=frozenset({"profile.generate"}))
        scope = create_scope(client, "Open access prompt")["scope_id"]
        opened = client.get(
            "/dashboard/prompts", params={"scope": scope, "prompt_key": "profile.generate", "lang": "en"}
        )
        assert opened.status_code == 200, opened.text
        assert _extract_editor_form(opened.text) is not None
        saved = _post_profile_prompt(
            client,
            scope=scope,
            mode="custom",
            instructions="Saved in disabled access mode.",
            expected_etag="",
        )
        assert saved.status_code == 303, saved.text
        assert client.get(f"/v1/scopes/{scope}/prompts/profile.generate").json()["mode"] == "custom"


@pytest.mark.parametrize("language", ["en", "zh"])
def test_scope_switching_isolates_configs_and_drafts_and_workspace_swaps_412(
    dashboard: TestClient, language: str
) -> None:
    labels = CATALOGS[language]
    _set_prompt_registry(dashboard, supported=frozenset({"profile.generate"}))
    alpha_info = create_scope(dashboard, f"Scope Alpha {language}")
    beta_info = create_scope(dashboard, f"Scope Beta {language}")
    alpha = alpha_info["scope_id"]
    beta = beta_info["scope_id"]

    # Customize Scope Alpha; leave Scope Beta in Auto.
    save_alpha = _post_profile_prompt(
        dashboard,
        scope=alpha,
        mode="custom",
        instructions="Alpha only durable instructions.",
        expected_etag="",
        lang=language,
    )
    assert save_alpha.status_code == 303, save_alpha.text
    assert parse_qs(urlsplit(save_alpha.headers["location"]).query)["lang"] == [language]

    alpha_page = dashboard.get(
        "/dashboard/prompts", params={"scope": alpha, "prompt_key": "profile.generate", "lang": language}
    )
    assert alpha_page.status_code == 200, alpha_page.text
    assert alpha_info["title"] in alpha_page.text
    assert f"<code>{alpha}</code>" in alpha_page.text
    assert "Alpha only durable instructions." in alpha_page.text
    assert f"{labels['prompt_mode']}: {labels['prompt_custom']}" in _prompt_card(alpha_page.text, "profile.generate")
    alpha_form = _extract_editor_form(alpha_page.text)
    assert alpha_form is not None
    assert alpha_form.data_scope == alpha
    assert alpha_form.inputs.get("scope") == alpha
    assert alpha_form.inputs.get("expected_etag") == '"revision:1"'

    # Switching to Scope Beta must isolate config and form identity completely.
    beta_page = dashboard.get(
        "/dashboard/prompts", params={"scope": beta, "prompt_key": "profile.generate", "lang": language}
    )
    assert beta_page.status_code == 200, beta_page.text
    assert beta_info["title"] in beta_page.text
    assert f"<code>{beta}</code>" in beta_page.text
    assert "Alpha only durable instructions." not in beta_page.text
    assert f"{labels['prompt_mode']}: {labels['prompt_auto']}" in _prompt_card(beta_page.text, "profile.generate")
    beta_form = _extract_editor_form(beta_page.text)
    assert beta_form is not None
    assert beta_form.data_scope == beta
    assert beta_form.inputs.get("scope") == beta
    assert beta_form.inputs.get("expected_etag") == ""
    assert beta_form.instructions is not None and beta_form.instructions.strip() == PROFILE_INSTRUCTIONS.strip()

    # A failing draft on Scope Beta stays scoped to Beta and never contaminates Scope Alpha.
    beta_error = _post_profile_prompt(
        dashboard,
        scope=beta,
        mode="custom",
        instructions="   ",
        expected_etag="",
        lang=language,
    )
    assert beta_error.status_code == 422, beta_error.text
    beta_err_form = _extract_editor_form(beta_error.text)
    assert beta_err_form is not None and beta_err_form.data_scope == beta
    assert "Alpha only durable instructions." not in beta_error.text

    alpha_reloaded = dashboard.get(
        "/dashboard/prompts", params={"scope": alpha, "prompt_key": "profile.generate", "lang": language}
    )
    alpha_reloaded_form = _extract_editor_form(alpha_reloaded.text)
    assert alpha_reloaded_form is not None
    assert alpha_reloaded_form.instructions == "Alpha only durable instructions."

    # workspace.html swaps HTML on 412 as well as 409/422.
    swap_match = re.search(r"\[([0-9,\s]+)\]\.includes\(event\.detail\.xhr\.status\)", alpha_page.text)
    assert swap_match is not None
    swap_codes = {int(code.strip()) for code in swap_match.group(1).split(",") if code.strip()}
    assert {409, 412, 422} <= swap_codes


def test_oversized_body_413_and_write_503_preserve_draft_and_reject_writes(
    dashboard: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import httpx

    _set_prompt_registry(dashboard, supported=frozenset({"profile.generate"}))
    scope = create_scope(dashboard, "Body limit and 503")["scope_id"]

    oversized = dashboard.post(
        "/dashboard/prompts/profile-generate",
        content=f"scope={scope}&mode=custom&expected_etag=&instructions=".encode() + b"x" * 520_000,
        headers={"Origin": "http://testserver", "Content-Type": "application/x-www-form-urlencoded"},
    )
    assert oversized.status_code == 413, oversized.text
    assert oversized.headers.get("X-Dashboard-HTML") == "1"
    assert 'role="alert"' in oversized.text
    assert CATALOGS["zh"]["prompt_write_error_instructions_too_long"] in oversized.text
    assert dashboard.get(f"/v1/scopes/{scope}/artifacts/prompt").json()["items"] == []

    original_send = DashboardAPI.send

    async def fail_write(
        api: DashboardAPI,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        if method in {"POST", "PUT"} and "/artifacts" in path:
            return httpx.Response(503, json={"error": {"code": "service_unavailable"}})
        return await original_send(api, method, path, payload, headers=headers)

    with monkeypatch.context() as patch:
        patch.setattr(DashboardAPI, "send", fail_write)
        draft = "Draft preserved across transient 503 write error."
        failed = _post_profile_prompt(
            dashboard,
            scope=scope,
            mode="custom",
            instructions=draft,
            expected_etag="",
        )
    assert failed.status_code == 503, failed.text
    assert failed.headers.get("X-Dashboard-HTML") == "1"
    assert 'role="alert"' in failed.text
    preserved = _extract_editor_form(failed.text)
    assert preserved is not None
    assert preserved.selected_mode == "custom"
    assert preserved.instructions == draft
    assert dashboard.get(f"/v1/scopes/{scope}/artifacts/prompt").json()["items"] == []


def test_public_read_mode_supports_profile_prompt_edit_and_rollback(
    dashboard: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_prompt_registry(dashboard, supported=frozenset({"profile.generate"}))
    scope = create_scope(dashboard, "Public read profile prompt")["scope_id"]
    monkeypatch.setattr("powercontext.server.dashboard.api._builtin_runtime", lambda request: None)

    saved_1 = _post_profile_prompt(
        dashboard, scope=scope, mode="custom", instructions="Public mode rev 1.", expected_etag=""
    )
    assert saved_1.status_code == 303, saved_1.text
    saved_2 = _post_profile_prompt(
        dashboard, scope=scope, mode="custom", instructions="Public mode rev 2.", expected_etag='"revision:1"'
    )
    assert saved_2.status_code == 303, saved_2.text

    page = dashboard.get(saved_2.headers["location"])
    assert page.status_code == 200, page.text
    assert "Public mode rev 2." in page.text

    rolled_back = dashboard.post(
        "/dashboard/rollback",
        data={
            "scope": scope,
            "family": "prompt",
            "artifact_id": "profile.generate",
            "source_revision": "1",
            "expected_revision": "2",
            "reason": "Public read rollback",
        },
        headers={"Origin": "http://testserver"},
        follow_redirects=False,
    )
    assert rolled_back.status_code == 303, rolled_back.text
    restored_page = dashboard.get(rolled_back.headers["location"])
    assert restored_page.status_code == 200, restored_page.text
    assert "Public mode rev 1." in restored_page.text


def test_cjk_30k_prompt_saves_via_dashboard_and_outage_503_still_preserves_draft(
    dashboard: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_prompt_registry(dashboard, supported=frozenset({"profile.generate"}))
    scope = create_scope(dashboard, "CJK and 503 draft")["scope_id"]

    # 30,000 CJK characters exceed 256 KB when form-urlencoded (~270 KB) but fit MAX_PROMPT_INSTRUCTIONS (32,768).
    long_cjk = "中" * 30_000
    saved = _post_profile_prompt(dashboard, scope=scope, mode="custom", instructions=long_cjk, expected_etag="")
    assert saved.status_code == 303, saved.text
    assert dashboard.get(f"/v1/scopes/{scope}/prompts/profile.generate").json()["effective"]["instructions"] == long_cjk

    # Total read+write outage raising ReadError(503) must still render prompts.html with the preserved draft form.
    async def fail_send(api: DashboardAPI, method: str, path: str, payload=None, headers=None):
        raise ReadError(503, "service_unavailable")

    async def fail_configs(api: DashboardAPI, scope_id: str, keys: tuple[str, ...]):
        return [ReadError(503, "service_unavailable")]

    with monkeypatch.context() as patch:
        patch.setattr(DashboardAPI, "send", fail_send)
        patch.setattr(DashboardAPI, "prompt_configurations", fail_configs)
        draft = "Keep this draft even if both precheck and reload fail with 503."
        failed = _post_profile_prompt(
            dashboard,
            scope=scope,
            mode="custom",
            instructions=draft,
            expected_etag='"revision:1"',
            lang="zh",
        )
    assert failed.status_code == 503, failed.text
    assert failed.headers.get("X-Dashboard-HTML") == "1"
    assert CATALOGS["zh"]["prompt_write_error_service_unavailable"] in failed.text
    preserved = _extract_editor_form(failed.text)
    assert preserved is not None
    assert preserved.data_scope == scope
    assert preserved.selected_mode == "custom"
    assert preserved.instructions == draft
    assert preserved.inputs.get("expected_etag") == '"revision:1"'


def test_race_capability_error_code_translates_to_actionable_message(
    dashboard: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import httpx

    _set_prompt_registry(dashboard, supported=frozenset({"profile.generate"}))
    scope = create_scope(dashboard, "Capability race error")["scope_id"]

    original_send = DashboardAPI.send

    async def race_capability(
        api: DashboardAPI,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        if method == "POST" and "/artifacts" in path:
            return httpx.Response(422, json={"error": {"code": "prompt_customization_unavailable"}})
        return await original_send(api, method, path, payload, headers=headers)

    with monkeypatch.context() as patch:
        patch.setattr(DashboardAPI, "send", race_capability)
        rejected = _post_profile_prompt(
            dashboard,
            scope=scope,
            mode="custom",
            instructions="Raced capability disable.",
            expected_etag="",
        )
    assert rejected.status_code == 422, rejected.text
    assert CATALOGS["en"]["prompt_write_error_capability_disabled"] in rejected.text
    assert CATALOGS["en"]["prompt_write_error_invalid_request"] not in rejected.text


def _launch_playwright_browser(playwright_api: Any, runtime: Any) -> Any:
    import os

    channel = os.environ.get("POWERCONTEXT_TEST_BROWSER_CHANNEL")
    try:
        return runtime.chromium.launch(channel=channel)
    except playwright_api.Error as error:
        if "Executable doesn't exist" in str(error) and not channel:
            for fallback in ("chrome", "msedge"):
                try:
                    return runtime.chromium.launch(channel=fallback)
                except playwright_api.Error:
                    continue
        if "Executable doesn't exist" in str(error):
            pytest.skip("Install Playwright Chromium or set POWERCONTEXT_TEST_BROWSER_CHANNEL")
        raise


def _route_dashboard_browser(page: Any, dashboard: TestClient) -> None:
    def serve(route: Any) -> None:
        request = route.request
        req_headers = {
            key: value for key, value in request.headers.items() if key.lower() not in {"host", "content-length"}
        }
        response = dashboard.request(
            request.method,
            request.url,
            content=request.post_data_buffer,
            headers=req_headers,
        )
        headers = {
            key: value
            for key, value in response.headers.items()
            if key.lower() not in {"content-length", "content-encoding", "transfer-encoding"}
        }
        route.fulfill(status=response.status_code, headers=headers, body=response.content)

    page.route("http://testserver/**", serve)


def test_browser_back_forward_preserves_scope_specific_draft(dashboard: TestClient) -> None:
    """Verify unsaved Custom instructions survive HTMX Back/Forward without leaking across Scopes."""
    playwright = pytest.importorskip("playwright.sync_api")
    _set_prompt_registry(dashboard, supported=frozenset({"profile.generate"}))
    scope_a = create_scope(dashboard, "History draft Alpha")["scope_id"]
    scope_b = create_scope(dashboard, "History draft Beta")["scope_id"]

    with playwright.sync_playwright() as runtime:
        browser = _launch_playwright_browser(playwright, runtime)
        try:
            page = browser.new_page()
            errors: list[Any] = []
            page.on("pageerror", lambda error: errors.append(error))
            _route_dashboard_browser(page, dashboard)

            # 1. Open Home, follow the Prompts navigation link, expand Profile generation, edit Custom.
            page.goto(f"http://testserver/dashboard/home?scope={scope_a}&lang=en", wait_until="domcontentloaded")
            page.locator('a[href*="/dashboard/prompts"]').first.click()
            page.wait_for_url("**/dashboard/prompts**")
            profile_group = page.locator("details.prompt-group", has_text="profile.generate").first
            profile_group.locator(":scope > summary").click()
            profile_details = profile_group.locator("details.prompt-record", has_text="profile.generate").first
            profile_details.locator(":scope > summary").click()

            form = profile_details.locator("form[data-profile-prompt-editor]")
            form.locator('label.form-selectgroup-item:has(input[value="custom"])').click()
            textarea = form.locator('textarea[name="instructions"]')
            assert textarea.is_visible()
            draft_a = "Preserve these Scope A instructions across Back and Forward."
            textarea.fill(draft_a)

            # 2. Browser Back -> Forward restores the draft and re-opens the Profile generation card.
            page.go_back(wait_until="domcontentloaded")
            page.wait_for_url("**/dashboard/home**")
            page.go_forward(wait_until="domcontentloaded")
            page.wait_for_url("**/dashboard/prompts**")

            restored_details = page.locator("details.prompt-record", has_text="profile.generate").first
            assert restored_details.evaluate("el => el.open") is True
            restored_form = restored_details.locator("form[data-profile-prompt-editor]")
            assert restored_form.locator('input[name="mode"][value="custom"]').is_checked()
            assert restored_form.locator('textarea[name="instructions"]').is_visible()
            assert restored_form.locator('textarea[name="instructions"]').input_value() == draft_a
            assert restored_form.locator("[data-prompt-save]").is_enabled()

            # 3. Scope B stays isolated and does not receive Scope A's draft.
            page.goto(
                f"http://testserver/dashboard/prompts?scope={scope_b}&prompt_key=profile.generate&lang=en",
                wait_until="domcontentloaded",
            )
            scope_b_form = page.locator("form[data-profile-prompt-editor]")
            assert scope_b_form.locator('input[name="mode"][value="auto"]').is_checked()
            assert draft_a not in scope_b_form.locator('textarea[name="instructions"]').input_value()

            # 4. Returning to Scope A still has draft_a; Cancel clears it so Back/Forward won't resurrect it.
            page.goto(
                f"http://testserver/dashboard/prompts?scope={scope_a}&prompt_key=profile.generate&lang=en",
                wait_until="domcontentloaded",
            )
            scope_a_form = page.locator("form[data-profile-prompt-editor]")
            assert scope_a_form.locator('textarea[name="instructions"]').input_value() == draft_a
            scope_a_form.locator("[data-prompt-cancel]").click()
            page.wait_for_load_state("domcontentloaded")
            assert page.locator('form[data-profile-prompt-editor] input[name="mode"][value="auto"]').is_checked()

            # 5. Saving clears stored draft so subsequent Back/Forward reflects the saved revision.
            page.locator(
                'form[data-profile-prompt-editor] label.form-selectgroup-item:has(input[value="custom"])'
            ).click()
            saved_text = "Committed Scope A custom instructions."
            page.locator('form[data-profile-prompt-editor] textarea[name="instructions"]').fill(saved_text)
            page.locator("form[data-profile-prompt-editor] [data-prompt-save]").click()
            page.locator(".alert-success").wait_for(state="visible")
            assert page.locator("form[data-profile-prompt-editor]").get_attribute("data-prompt-saved") == "1"
            assert (
                page.evaluate(
                    "scope => window.sessionStorage.getItem(`powercontext:profile-prompt-draft:${scope}`)",
                    scope_a,
                )
                is None
            )
            assert not errors
        finally:
            browser.close()


def test_save_compare_and_revision_preserve_scroll_position(dashboard: TestClient) -> None:
    """Verify Save, Compare, and Revision clicks do not reset viewport scroll to the page top."""
    playwright = pytest.importorskip("playwright.sync_api")
    _set_prompt_registry(dashboard, supported=frozenset({"profile.generate"}))
    scope = create_scope(dashboard, "Scroll preservation")["scope_id"]
    first = _post_profile_prompt(
        dashboard,
        scope=scope,
        mode="custom",
        instructions="Revision one instructions.",
        expected_etag="",
        lang="en",
    )
    assert first.status_code == 303

    with playwright.sync_playwright() as runtime:
        browser = _launch_playwright_browser(playwright, runtime)
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 800})
            errors: list[Any] = []
            page.on("pageerror", lambda error: errors.append(error))
            _route_dashboard_browser(page, dashboard)

            page.goto(
                f"http://testserver/dashboard/prompts?scope={scope}&prompt_key=profile.generate&lang=en",
                wait_until="domcontentloaded",
            )
            form = page.locator("form[data-profile-prompt-editor]")
            form.locator('textarea[name="instructions"]').fill("Revision two instructions.")
            form.locator("[data-prompt-save]").scroll_into_view_if_needed()
            assert page.evaluate("window.scrollY") > 400
            form.locator("[data-prompt-save]").click()
            page.locator("#prompt-record-profile-generate .alert-success").wait_for(state="visible")
            assert page.evaluate("window.scrollY") > 400

            history = page.locator("#revision-history")
            history.locator(":scope > summary").click()
            compare_link = history.locator("a.btn", has_text="Compare").first
            compare_link.scroll_into_view_if_needed()
            y_before_compare = page.evaluate("window.scrollY")
            assert y_before_compare > 400
            compare_link.click()
            diff_pre = page.locator("#revision-history pre.page-body-pre").first
            diff_pre.wait_for(state="visible")
            y_after_compare = page.evaluate("window.scrollY")
            assert y_after_compare > 400
            compare_box = history.evaluate("el => el.getBoundingClientRect()")
            assert compare_box["top"] < 800
            assert compare_box["bottom"] > 0

            rev_link = page.locator("#revision-history a.btn", has_text="Revision 1").first
            rev_link.scroll_into_view_if_needed()
            y_before_rev = page.evaluate("window.scrollY")
            rev_link.click()
            page.wait_for_url("**prompt_revision=1**")
            page.wait_for_timeout(150)
            y_after_rev = page.evaluate("window.scrollY")
            assert y_after_rev > 200
            assert abs(y_after_rev - y_before_rev) < 600
            assert page.locator("#revision-history").evaluate("el => el.open") is True
            assert CATALOGS["en"]["revision_viewing"] in page.locator("#revision-history").inner_text()
            preview = page.locator("#revision-history [data-revision-preview]")
            assert preview.is_visible()
            assert "Revision one instructions." in preview.inner_text()
            rev_box = page.locator("#revision-history").evaluate("el => el.getBoundingClientRect()")
            assert rev_box["top"] < 800
            assert rev_box["bottom"] > 0
            assert "compare=" not in page.url
            assert "#" not in page.url

            # Clicking Back to editor (or current head link) restores the editable form.
            back_btn = page.locator("#revision-history a.btn", has_text=CATALOGS["en"]["prompt_return_editor"])
            assert back_btn.is_visible()
            back_btn.click()
            page.locator("form[data-profile-prompt-editor]").wait_for(state="visible")
            assert "prompt_revision=" not in page.url
            assert not errors
        finally:
            browser.close()


def test_readonly_prompt_history_uses_back_to_current_label(dashboard: TestClient) -> None:
    """Verify read-only prompt revisions label the return link 'Back to current' rather than 'Back to editor'."""
    _set_prompt_registry(dashboard, supported=frozenset({"profile.generate", "experience.incubate"}))
    scope = create_scope(dashboard, "Readonly prompt label")["scope_id"]
    created = dashboard.post(
        f"/v1/scopes/{scope}/artifacts",
        json={
            "family": "prompt",
            "prompt_key": "experience.incubate",
            "content": {
                "schema_version": "powercontext.prompt.v1",
                "mode": "custom",
                "instructions": "Custom experience incubate instructions.",
                "demonstrations": [],
            },
        },
    )
    assert created.status_code in (200, 201), created.text
    for lang in ("en", "zh"):
        labels = CATALOGS[lang]
        page = dashboard.get(
            "/dashboard/prompts",
            params={"scope": scope, "prompt_key": "experience.incubate", "prompt_revision": "1", "lang": lang},
        )
        assert page.status_code == 200, page.text
        assert labels["prompt_return_current"] in page.text
        assert labels["prompt_return_editor"] not in page.text
        assert "Custom experience incubate instructions." in page.text

    profile_created = dashboard.post(
        f"/v1/scopes/{scope}/artifacts",
        json={
            "family": "prompt",
            "prompt_key": "profile.generate",
            "content": {
                "schema_version": "powercontext.prompt.v1",
                "mode": "custom",
                "instructions": "Custom profile generate instructions.",
                "demonstrations": [],
            },
        },
    )
    assert profile_created.status_code in (200, 201), profile_created.text
    for lang in ("en", "zh"):
        labels = CATALOGS[lang]
        page = dashboard.get(
            "/dashboard/prompts",
            params={"scope": scope, "prompt_key": "profile.generate", "prompt_revision": "1", "lang": lang},
        )
        assert page.status_code == 200, page.text
        assert labels["prompt_return_editor"] in page.text
        assert labels["prompt_return_current"] not in page.text


def test_rejected_save_sets_hx_push_url_header(dashboard: TestClient) -> None:
    """Verify rejected saves emit a safe GET HX-Push-Url (or false on missing scope) instead of the POST path."""
    _set_prompt_registry(dashboard, supported=frozenset({"profile.generate"}))
    scope = create_scope(dashboard, "Rejected save HX-Push-Url")["scope_id"]

    rejected = _post_profile_prompt(
        dashboard,
        scope=scope,
        mode="custom",
        instructions="   ",
        expected_etag="",
        lang="en",
        headers={"HX-Request": "true"},
    )
    assert rejected.status_code == 422
    push_url = rejected.headers.get("hx-push-url", "")
    assert push_url.startswith("/dashboard/prompts?")
    push_query = parse_qs(urlsplit(push_url).query)
    assert push_query["scope"] == [scope]
    assert push_query["prompt_key"] == ["profile.generate"]
    assert push_query["lang"] == ["en"]
    assert 'href="/dashboard/prompts/profile-generate' not in rejected.text
    pref_links = re.findall(r'class="dropdown-item[^"]*"\s+href="(/dashboard/prompts\?[^"]*)"', rejected.text)
    assert len(pref_links) == 4
    for link in pref_links:
        link_query = parse_qs(urlsplit(unescape(link)).query)
        assert link_query["scope"] == [scope]
        assert link_query["prompt_key"] == ["profile.generate"]
    assert dashboard.get(unescape(pref_links[0])).status_code == 200

    cross_origin = _post_profile_prompt(
        dashboard,
        scope=scope,
        mode="custom",
        instructions="Cross origin",
        expected_etag="",
        origin="https://evil.example",
    )
    assert cross_origin.status_code == 403
    assert cross_origin.headers.get("hx-push-url") == "false"
    assert 'href="/dashboard/prompts/profile-generate' not in cross_origin.text


def test_browser_saved_revisit_and_rejected_save_preserve_draft_and_url(dashboard: TestClient) -> None:
    """Verify revisiting a saved URL keeps newer drafts and rejected saves keep a GET-safe URL across reload/back."""
    playwright = pytest.importorskip("playwright.sync_api")
    _set_prompt_registry(dashboard, supported=frozenset({"profile.generate"}))
    scope = create_scope(dashboard, "Saved revisit and rejection recovery")["scope_id"]

    with playwright.sync_playwright() as runtime:
        browser = _launch_playwright_browser(playwright, runtime)
        try:
            page = browser.new_page()
            errors: list[Any] = []
            page.on("pageerror", lambda error: errors.append(error))
            _route_dashboard_browser(page, dashboard)

            # 1. Start at Home, follow Prompts link, save revision 1, and verify stripped saved query params.
            page.goto(f"http://testserver/dashboard/home?scope={scope}&lang=en", wait_until="domcontentloaded")
            page.locator('a[href*="/dashboard/prompts"]').first.click()
            page.wait_for_url("**/dashboard/prompts**")
            profile_group = page.locator("details.prompt-group", has_text="profile.generate").first
            profile_group.locator(":scope > summary").click()
            profile_details = profile_group.locator("details.prompt-record", has_text="profile.generate").first
            profile_details.locator(":scope > summary").click()

            form = profile_details.locator("form[data-profile-prompt-editor]")
            form.locator('label.form-selectgroup-item:has(input[value="custom"])').click()
            form.locator('textarea[name="instructions"]').fill("Revision one saved text.")
            # Verify submit + htmx:beforeSend locks editing controls while a save is in flight.
            page.evaluate(
                """() => {
                    window.__pendingSaveLock = null;
                    document.body.addEventListener('htmx:beforeSend', event => {
                        const editor = event.target.closest('form[data-profile-prompt-editor]');
                        if (!editor) return;
                        window.__pendingSaveLock = {
                            readOnly: editor.querySelector('textarea[name="instructions"]').readOnly,
                            saveDisabled: editor.querySelector('[data-prompt-save]').disabled,
                            radiosDisabled: Array.from(editor.querySelectorAll('input[name="mode"]')).every(r => r.disabled),
                        };
                    }, { once: true });
                }"""
            )
            form.locator("[data-prompt-save]").click()
            page.locator("#prompt-record-profile-generate .alert-success").wait_for(state="visible")
            assert page.evaluate("window.__pendingSaveLock") == {
                "readOnly": True,
                "saveDisabled": True,
                "radiosDisabled": True,
            }
            assert "prompt_saved=" not in page.url

            # 2. Edit a newer draft after save, press Back then Forward, and revisit raw saved query directly.
            newer_draft = "Newer draft written after saving revision 1."
            page.locator('form[data-profile-prompt-editor] textarea[name="instructions"]').fill(newer_draft)
            page.go_back(wait_until="domcontentloaded")
            page.go_forward(wait_until="domcontentloaded")
            page.wait_for_url("**/dashboard/prompts**")
            assert (
                page.locator('form[data-profile-prompt-editor] textarea[name="instructions"]').input_value()
                == newer_draft
            )

            # Even visiting the raw prompt_saved=1&prompt_saved_revision=1 URL does not delete newer_draft once consumed.
            page.goto(
                f"http://testserver/dashboard/prompts?scope={scope}&prompt_key=profile.generate&prompt_saved=1&prompt_saved_revision=1&lang=en",
                wait_until="domcontentloaded",
            )
            assert (
                page.locator('form[data-profile-prompt-editor] textarea[name="instructions"]').input_value()
                == newer_draft
            )

            # Save revision 2, edit a third unsaved draft, and verify revisiting older revision-1 save URL
            # preserves the third draft, keeps ETag "revision:2", and never regresses saved ack from "2" to "1".
            page.locator("form[data-profile-prompt-editor] [data-prompt-save]").click()
            page.locator("#prompt-record-profile-generate .alert-success").wait_for(state="visible")
            third_draft = "Third unsaved draft written after saving revision 2."
            page.locator('form[data-profile-prompt-editor] textarea[name="instructions"]').fill(third_draft)
            page.goto(
                f"http://testserver/dashboard/prompts?scope={scope}&prompt_key=profile.generate&prompt_saved=1&prompt_saved_revision=1&lang=en",
                wait_until="domcontentloaded",
            )
            assert (
                page.locator('form[data-profile-prompt-editor] textarea[name="instructions"]').input_value()
                == third_draft
            )
            assert (
                page.locator('form[data-profile-prompt-editor] input[name="expected_etag"]').input_value()
                == '"revision:2"'
            )
            assert (
                page.evaluate(
                    "scope => window.sessionStorage.getItem(`powercontext:profile-prompt-saved:${scope}`)",
                    scope,
                )
                == "2"
            )

            # 3. Validation rejection keeps a GET-safe URL so page.reload() succeeds instead of hitting 405.
            page.locator('form[data-profile-prompt-editor] textarea[name="instructions"]').fill("")
            page.locator("form[data-profile-prompt-editor] [data-prompt-save]").click()
            page.locator("#prompt-record-profile-generate .alert-danger").wait_for(state="visible")
            assert "/dashboard/prompts/profile-generate" not in page.url
            assert "/dashboard/prompts?" in page.url
            page.once("dialog", lambda dialog: dialog.accept())
            page.reload(wait_until="domcontentloaded")
            page.locator('form[data-profile-prompt-editor] textarea[name="instructions"]').wait_for(state="visible")
            assert page.locator('form[data-profile-prompt-editor] textarea[name="instructions"]').input_value() == ""

            # 4. Concurrent conflict rejection keeps GET-safe URL and preserves draft + reconciled ETag across Back/Forward.
            conflict_draft = "Conflict draft to retry after navigating back."
            conflict_form = page.locator("form[data-profile-prompt-editor]")
            conflict_form.locator('textarea[name="instructions"]').fill(conflict_draft)
            concurrent = _post_profile_prompt(
                dashboard,
                scope=scope,
                mode="custom",
                instructions="Concurrent revision two.",
                expected_etag=conflict_form.locator('input[name="expected_etag"]').input_value(),
                lang="en",
            )
            assert concurrent.status_code == 303
            conflict_form.locator("[data-prompt-save]").click()
            page.locator("#prompt-record-profile-generate .alert-warning").wait_for(state="visible")
            assert "/dashboard/prompts/profile-generate" not in page.url

            page.go_back(wait_until="domcontentloaded")
            page.go_forward(wait_until="domcontentloaded")
            page.wait_for_url("**/dashboard/prompts**")
            retry_form = page.locator("form[data-profile-prompt-editor]")
            assert retry_form.locator('textarea[name="instructions"]').input_value() == conflict_draft
            retry_form.locator("[data-prompt-save]").click()
            page.locator("#prompt-record-profile-generate .alert-success").wait_for(state="visible")
            assert not errors
        finally:
            browser.close()


def test_injected_custom_prompt_exposes_auto_only_reset_form(dashboard: TestClient) -> None:
    """Verify an injected_component Custom head still offers an Auto-only reset without exposing the Custom editor."""
    _set_prompt_registry(dashboard, supported=frozenset({"profile.generate"}))
    scope = create_scope(dashboard, "Injected custom reset")["scope_id"]

    created = _post_profile_prompt(
        dashboard,
        scope=scope,
        mode="custom",
        instructions="Previously saved custom instructions.",
        expected_etag="",
        lang="en",
    )
    assert created.status_code == 303

    _set_prompt_registry(dashboard, injected=frozenset({"profile.generate"}))
    page = dashboard.get("/dashboard/prompts", params={"scope": scope, "prompt_key": "profile.generate", "lang": "en"})
    assert page.status_code == 200, page.text
    card = _prompt_card(page.text, "profile.generate")
    assert _extract_editor_form(card) is None
    assert "data-profile-prompt-reset" in card
    assert 'name="return_to_auto"' in card
    reset_match = re.search(
        r'<form[^>]*action="(/dashboard/prompts/profile-generate[^"]*)"[^>]*data-profile-prompt-reset', card
    )
    assert reset_match is not None

    reset = dashboard.post(
        unescape(reset_match.group(1)),
        data={"scope": scope, "expected_etag": '"revision:1"', "return_to_auto": "1"},
        headers={"Origin": "http://testserver"},
        follow_redirects=False,
    )
    assert reset.status_code == 303, reset.text
    after = dashboard.get(reset.headers["location"])
    assert after.status_code == 200, after.text
    after_card = _prompt_card(after.text, "profile.generate")
    assert "data-profile-prompt-reset" not in after_card
    assert f"{CATALOGS['en']['prompt_mode']}: {CATALOGS['en']['prompt_auto']}" in after_card


def test_prompt_history_pins_head_revision_and_oversized_413_preserves_scope(dashboard: TestClient) -> None:
    """Verify numbered history links always pin prompt_revision and 413 keeps the edited non-default Scope."""
    _set_prompt_registry(dashboard, supported=frozenset({"profile.generate"}))
    scope_default = create_scope(dashboard, "Default scope for 413")["scope_id"]
    scope_other = create_scope(dashboard, "Secondary scope for 413")["scope_id"]

    first = _post_profile_prompt(
        dashboard,
        scope=scope_other,
        mode="custom",
        instructions="Rev 1 instructions.",
        expected_etag="",
        lang="en",
    )
    assert first.status_code == 303
    second = _post_profile_prompt(
        dashboard,
        scope=scope_other,
        mode="custom",
        instructions="Rev 2 instructions.",
        expected_etag='"revision:1"',
        lang="en",
    )
    assert second.status_code == 303

    # History row for current head (Revision 2) still pins prompt_revision=2 in its numbered link.
    history_page = dashboard.get(
        "/dashboard/prompts",
        params={"scope": scope_other, "prompt_key": "profile.generate", "prompt_revision": "1", "lang": "en"},
    )
    assert history_page.status_code == 200, history_page.text
    rev2_links = re.findall(r'href="(/dashboard/prompts\?[^"]*)"[^>]*>Revision 2</a>', history_page.text)
    assert rev2_links
    assert parse_qs(urlsplit(unescape(rev2_links[0])).query).get("prompt_revision") == ["2"]

    # Editor form action carries the edited non-default Scope so 413 rejection never falls back to default Scope.
    editor_page = dashboard.get(
        "/dashboard/prompts",
        params={"scope": scope_other, "prompt_key": "profile.generate", "lang": "en"},
    )
    assert editor_page.status_code == 200, editor_page.text
    action_match = re.search(r'<form[^>]*action="(/dashboard/prompts/profile-generate[^"]*)"', editor_page.text)
    assert action_match is not None
    form_action = unescape(action_match.group(1))
    assert parse_qs(urlsplit(form_action).query).get("scope") == [scope_other]

    oversized = dashboard.post(
        form_action,
        content=f"scope={scope_other}&mode=custom&expected_etag=&instructions=".encode() + b"x" * 520_000,
        headers={
            "Origin": "http://testserver",
            "Content-Type": "application/x-www-form-urlencoded",
            "HX-Request": "true",
        },
    )
    assert oversized.status_code == 413, oversized.text
    push_url = oversized.headers.get("hx-push-url", "")
    assert parse_qs(urlsplit(push_url).query).get("scope") == [scope_other]
    assert scope_default not in push_url


def test_invalid_prompt_saved_revision_query_returns_200_without_success_banner(dashboard: TestClient) -> None:
    """Verify non-ASCII digits and oversized decimal prompt_saved_revision values return 200 instead of 500."""
    _set_prompt_registry(dashboard, supported=frozenset({"profile.generate"}))
    scope = create_scope(dashboard, "Bounded saved revision query")["scope_id"]
    created = _post_profile_prompt(
        dashboard,
        scope=scope,
        mode="custom",
        instructions="Saved custom instructions.",
        expected_etag="",
        lang="en",
    )
    assert created.status_code == 303

    valid = dashboard.get(
        "/dashboard/prompts",
        params={
            "scope": scope,
            "prompt_key": "profile.generate",
            "prompt_saved": "1",
            "prompt_saved_revision": "1",
            "lang": "en",
        },
    )
    assert valid.status_code == 200, valid.text
    assert "alert-success" in valid.text

    for bad_revision in ("\u00b2", "9" * 5000, "0", "-1", "abc"):
        page = dashboard.get(
            "/dashboard/prompts",
            params={
                "scope": scope,
                "prompt_key": "profile.generate",
                "prompt_saved": "1",
                "prompt_saved_revision": bad_revision,
                "lang": "en",
            },
        )
        assert page.status_code == 200, f"failed for {bad_revision[:12]!r}: {page.text}"
        assert "alert-success" not in page.text
