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

"""Read and download exact artifacts through the authenticated Dashboard."""

import re
from html import unescape
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
from fastapi.testclient import TestClient
from markdown_it import MarkdownIt

from powercontext.server.dashboard.api import DashboardAPI
from tests.test_dashboard import commit_handoff, create_scope
from tests.test_dashboard import dashboard as dashboard


def _links(html: str, path: str) -> list[str]:
    return [unescape(url) for url in re.findall(r'href="([^"]+)"', html) if urlsplit(unescape(url)).path == path]


def _profile(client: TestClient, scope: str, body: str) -> dict[str, Any]:
    response = client.post(f"/v1/scopes/{scope}/artifacts", json={"family": "profile", "content": {"content": body}})
    assert response.status_code == 201, response.text
    return response.json()


def test_profile_history_is_exact_and_returns_to_its_page(dashboard):
    scope = create_scope(dashboard, "Profile history")["scope_id"]
    _profile(dashboard, scope, "# First profile\n\nOriginal preference.")
    path = f"/v1/scopes/{scope}/artifacts/profile/profile"
    for revision in range(2, 9):
        previous = dashboard.get(path)
        changed = dashboard.put(
            path,
            json={"content": {"content": f"# Profile {revision}\n\nUpdated preference."}},
            headers={"If-Match": previous.headers["etag"]},
        )
        assert changed.status_code == 200, changed.text
    current = dashboard.get("/dashboard/profile", params={"scope": scope, "lang": "en"})
    assert current.status_code == 200
    assert "<h1>Profile 8</h1>" in current.text
    first = dashboard.get("/dashboard/profile", params={"scope": scope, "view": "history", "lang": "en"})
    next_url = next(url for url in _links(first.text, "/dashboard/profile") if "profile_cursor=" in url)
    second = dashboard.get(next_url)
    detail_url = next(
        url
        for url in _links(second.text, "/dashboard/profile")
        if parse_qs(urlsplit(url).query).get("revision") == ["1"]
    )
    detail = dashboard.get(detail_url)
    assert detail.status_code == 200
    assert "Historical profile" in detail.text
    assert "<h1>First profile</h1>" in detail.text
    assert "Updated preference" not in detail.text
    target = parse_qs(urlsplit(detail_url).query)["return_to"][0]
    assert target in _links(detail.text, "/dashboard/profile")
    assert dashboard.get(target).status_code == 200
    # Reloads preserve the URL's history position and never mutate the head.
    assert dashboard.get(detail_url).status_code == 200
    assert dashboard.get(path).json()["revision"] == 8


def test_profile_safe_markdown_and_scope_isolation(dashboard):
    scope = create_scope(dashboard, "Safe profile")["scope_id"]
    other = create_scope(dashboard, "Empty profile")["scope_id"]
    _profile(
        dashboard,
        scope,
        "# Chinese 中文\n\n<script>alert(1)</script>\n\n![tracking](https://example.com/pixel)\n\n[unsafe](javascript:alert(1))\n\n[safe](https://example.com)",
    )
    page = dashboard.get("/dashboard/profile", params={"scope": scope})
    assert page.status_code == 200
    assert "Chinese 中文" in page.text
    assert "<script>alert(1)</script>" not in page.text
    assert 'src="https://example.com/pixel"' not in page.text
    assert 'href="javascript:' not in page.text
    assert 'href="https://example.com" rel="noopener noreferrer" target="_blank"' in page.text
    empty = dashboard.get("/dashboard/profile", params={"scope": other, "lang": "en"})
    assert "No profile in this scope" in empty.text
    assert "Chinese 中文" not in empty.text
    for params in ({"revision": "0"}, {"revision": "no"}, {"view": "history", "revision": "1"}):
        assert dashboard.get("/dashboard/profile", params={"scope": scope, **params}).status_code == 422


def test_profile_evidence_is_bound_to_the_exact_revision(dashboard):
    scope = create_scope(dashboard, "Profile evidence")["scope_id"]
    response = dashboard.put(
        f"/v1/scopes/{scope}/profile-policy",
        json={"generation_enabled": True, "activation_mode": "automatic", "expected_version": 0},
    )
    assert response.status_code == 200

    class Generator:
        async def generate(self, value):
            return "# Saved preference\n\nUse Chinese."

    dashboard.app.state.application.profiles.generator = Generator()
    source = dashboard.post(f"/v1/scopes/{scope}/sources", json={"content": "I prefer Chinese."}).json()
    generated = dashboard.post("/v1/profile/flush", json={"scope_id": scope})
    assert generated.status_code == 200, generated.text
    profile = dashboard.get("/dashboard/profile", params={"scope": scope})
    evidence_url = next(url for url in _links(profile.text, f"/dashboard/evidence/{source['source_id']}"))
    assert parse_qs(urlsplit(evidence_url).query)["revision"] == ["1"]
    assert "I prefer Chinese." in dashboard.get(evidence_url).text
    unrelated = dashboard.post(f"/v1/scopes/{scope}/sources", json={"content": "Unrelated secret"}).json()
    bad = evidence_url.replace(source["source_id"], unrelated["source_id"])
    rejected = dashboard.get(bad)
    assert rejected.status_code == 404
    assert "Unrelated secret" not in rejected.text


def test_profile_evidence_authentication_recovers_exact_revision(dashboard):
    scope = create_scope(dashboard, "Profile evidence recovery")["scope_id"]
    response = dashboard.put(
        f"/v1/scopes/{scope}/profile-policy",
        json={"generation_enabled": True, "activation_mode": "automatic", "expected_version": 0},
    )
    assert response.status_code == 200

    class Generator:
        async def generate(self, value):
            return (
                "# First profile\n\nUse Chinese."
                if len(value.sources) == 1
                else "# Current profile\n\nKeep the latest preferences."
            )

    dashboard.app.state.application.profiles.generator = Generator()
    source = dashboard.post(f"/v1/scopes/{scope}/sources", json={"content": "I prefer Chinese."}).json()
    generated = dashboard.post("/v1/profile/flush", json={"scope_id": scope})
    assert generated.status_code == 200, generated.text
    second_source = dashboard.post(f"/v1/scopes/{scope}/sources", json={"content": "Keep this history."}).json()
    generated = dashboard.post("/v1/profile/flush", json={"scope_id": scope})
    assert generated.status_code == 200, generated.text

    history = dashboard.get("/dashboard/profile", params={"scope": scope, "view": "history", "lang": "en"})
    profile_url = next(
        url
        for url in _links(history.text, "/dashboard/profile")
        if parse_qs(urlsplit(url).query).get("revision") == ["1"]
    )
    profile = dashboard.get(profile_url)
    evidence_url = next(url for url in _links(profile.text, f"/dashboard/evidence/{source['source_id']}"))
    assert "revision=1" in evidence_url
    token = dashboard.headers.pop("Authorization").removeprefix("Bearer ")
    login = dashboard.get(evidence_url)
    assert login.status_code == 401
    next_field = re.search(r'name="next" value="([^"]+)"', login.text)
    assert next_field is not None
    next_url = unescape(next_field[1])
    assert urlsplit(next_url).path == "/dashboard/profile"
    assert parse_qs(urlsplit(next_url).query)["revision"] == ["1"]
    assert parse_qs(urlsplit(next_url).query)["return_to"][0].startswith("/dashboard/profile?")
    resumed = dashboard.post(
        "/dashboard/session",
        data={"token": token, "next": next_url},
        headers={"Origin": "http://testserver"},
        follow_redirects=False,
    )
    assert resumed.status_code == 303
    assert resumed.headers["location"] == next_url
    resumed_page = dashboard.get(next_url)
    assert "<h1>First profile</h1>" in resumed_page.text
    assert "Current profile" not in resumed_page.text
    assert second_source["source_id"] not in next_url


def test_handoff_download_stays_exact_and_retains_all_citations(dashboard):
    scope = create_scope(dashboard, "Exact downloads")["scope_id"]
    ref = commit_handoff(dashboard, scope)
    path = f"/v1/scopes/{scope}/artifacts/handoff/{ref['artifact_id']}"
    first = dashboard.get(path)
    content = first.json()["content"]
    content["objective"] = "A newer objective"
    for statement in [*content["state"], content["next_action"]]:
        for citation in statement["citations"]:
            reference = citation["source_ref"]
            reference["name"] = reference.pop("source_type")
    replaced = dashboard.put(path, headers={"If-Match": first.headers["etag"]}, json={"content": content})
    assert replaced.status_code == 200, replaced.text
    query = {"scope": scope, "artifact": ref["artifact_id"], "revision": ref["revision"], "lang": "en"}
    detail = dashboard.get("/dashboard/handoff-detail", params=query)
    download_url = _links(detail.text, "/dashboard/handoff-download")[0]
    result = dashboard.get(download_url)
    assert result.status_code == 200
    assert result.headers["content-disposition"] == 'attachment; filename="handoff-r1.md"'
    assert result.headers["content-type"].startswith("text/markdown")
    assert result.headers["cache-control"] == "no-store"
    assert result.headers["x-content-type-options"] == "nosniff"
    decoded = unescape(result.text)
    rendered = unescape(MarkdownIt("commonmark", {"html": False}).render(result.text))
    assert "Review remains in progress." in rendered
    assert "Continue the review." in rendered
    assert "A newer objective" not in rendered
    assert first.json()["content_digest"] in decoded
    assert ref["artifact_id"] in decoded
    assert '"source_id": "review"' in decoded
    assert dashboard.get(download_url).content == result.content
    assert dashboard.get(path).json()["revision"] == 2
    for language in ("zh", "en"):
        assert dashboard.get("/dashboard/handoff-download", params={**query, "lang": language}).status_code == 200
    assert "## 目标" in dashboard.get("/dashboard/handoff-download", params={**query, "lang": "zh"}).text


def test_handoff_directory_return_survives_refresh_and_login(dashboard, monkeypatch):
    scope = create_scope(dashboard, "Download recovery")["scope_id"]
    commit_handoff(dashboard, scope)
    # Supply two public collection pages while keeping exact record reads and
    # authorization real. New Handoff writes use the default singleton identity.
    read = DashboardAPI.read

    async def paged(api, path, payload=None):
        if path.startswith(f"/v1/scopes/{scope}/artifacts/handoff?"):
            result = await read(api, f"/v1/scopes/{scope}/artifacts/handoff?limit=1")
            result["next_cursor"] = None if "cursor=" in path else "saved-page-position"
            return result
        return await read(api, path, payload)

    monkeypatch.setattr(DashboardAPI, "read", paged)
    first = dashboard.get("/dashboard/handoff", params={"scope": scope, "lang": "en"})
    next_page = next(url for url in _links(first.text, "/dashboard/handoff") if "cursor=" in url)
    second = dashboard.get(next_page)
    detail_url = _links(second.text, "/dashboard/handoff-detail")[0]
    return_to = parse_qs(urlsplit(detail_url).query)["return_to"][0]
    assert parse_qs(urlsplit(return_to).query)["cursor"] == parse_qs(urlsplit(next_page).query)["cursor"]
    page = dashboard.get(detail_url)
    assert return_to in _links(page.text, "/dashboard/handoff")
    url = _links(page.text, "/dashboard/handoff-download")[0]
    token = dashboard.headers.pop("Authorization").removeprefix("Bearer ")
    login = dashboard.get(url)
    assert login.status_code == 401
    assert "content-disposition" not in login.headers
    next_field = re.search(r'name="next" value="([^"]+)"', login.text)
    assert next_field is not None
    next_url = unescape(next_field[1])
    assert urlsplit(next_url).path == "/dashboard/handoff-detail"
    assert parse_qs(urlsplit(next_url).query)["revision"] == ["1"]
    resumed = dashboard.post(
        "/dashboard/session", data={"token": token, "next": next_url}, headers={"Origin": "http://testserver"}
    )
    assert resumed.status_code == 200
    assert resumed.url.path == "/dashboard/handoff-detail"
    assert return_to in _links(resumed.text, "/dashboard/handoff")
    assert token not in resumed.text
    assert dashboard.get(_links(resumed.text, "/dashboard/handoff-download")[0]).status_code == 200


@pytest.mark.parametrize(
    "destination",
    [
        "https://evil.example/dashboard/profile?scope=S",
        "//evil.example/dashboard/profile?scope=S",
        "/dashboard/profile?scope=S&scope=X",
        "/dashboard/profile?scope=S&next=https://evil.example",
        "/dashboard/handoff-download?scope=S&artifact=A&revision=1",
        "/dashboard/profile?scope=S%0d%0aLocation:evil",
        "/dashboard/profile?scope=" + "S" * 17000,
    ],
)
def test_sign_in_rejects_unsafe_return_targets(dashboard, destination):
    token = dashboard.headers["Authorization"].removeprefix("Bearer ")
    response = dashboard.post(
        "/dashboard/session",
        data={"token": token, "next": destination},
        headers={"Origin": "http://testserver"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/dashboard/home"


def test_sign_in_preserves_theme_and_language_in_reading_return(dashboard):
    scope = create_scope(dashboard, "Reading preferences")["scope_id"]
    ref = commit_handoff(dashboard, scope)
    target = "/dashboard/handoff-detail?" + urlencode({
        "scope": scope,
        "artifact": ref["artifact_id"],
        "revision": ref["revision"],
        "lang": "en",
        "theme": "dark",
    })
    token = dashboard.headers.pop("Authorization").removeprefix("Bearer ")
    login = dashboard.get(target)
    assert login.status_code == 401
    next_field = re.search(r'name="next" value="([^"]+)"', login.text)
    assert next_field is not None
    next_url = unescape(next_field[1])
    next_query = parse_qs(urlsplit(next_url).query)
    assert next_query["lang"] == ["en"]
    assert next_query["theme"] == ["dark"]
    resumed = dashboard.post(
        "/dashboard/session",
        data={"token": token, "next": next_url},
        headers={"Origin": "http://testserver"},
    )
    assert resumed.status_code == 200
    assert resumed.url.path == "/dashboard/handoff-detail"
    assert resumed.url.query.decode() == urlsplit(next_url).query


def test_invalid_and_missing_downloads_never_become_attachments(dashboard):
    scope = create_scope(dashboard, "Download errors")["scope_id"]
    ref = commit_handoff(dashboard, scope)
    base = {"scope": scope, "artifact": ref["artifact_id"], "revision": "1"}
    for params in ({}, {**base, "revision": "0"}, {**base, "scope": ""}, {**base, "revision": "bad"}):
        result = dashboard.get("/dashboard/handoff-download", params=params)
        assert result.status_code == 422
        assert "content-disposition" not in result.headers
    missing = dashboard.get("/dashboard/handoff-download", params={**base, "revision": "999"})
    assert missing.status_code == 404
    assert "content-disposition" not in missing.headers
    assert "handoff-detail" in missing.text


def test_collection_download_links_use_each_records_revision(dashboard):
    scope = create_scope(dashboard, "Download list")["scope_id"]
    ref = commit_handoff(dashboard, scope)
    page = dashboard.get("/dashboard/handoff", params={"scope": scope, "lang": "en"})
    url = _links(page.text, "/dashboard/handoff-download")[0]
    assert parse_qs(urlsplit(url).query)["artifact"] == [ref["artifact_id"]]
    assert parse_qs(urlsplit(url).query)["revision"] == [str(ref["revision"])]
    assert dashboard.get(url).status_code == 200


def test_login_body_limit_and_invalid_directory_return(dashboard):
    response = dashboard.post(
        "/dashboard/session", content=b"token=" + b"x" * 32768, headers={"Origin": "http://testserver"}
    )
    assert response.status_code == 413
    scope = create_scope(dashboard, "Return boundary")["scope_id"]
    ref = commit_handoff(dashboard, scope)
    for target in (
        "https://evil.example/",
        "/dashboard/handoff?scope=another",
        "/dashboard/handoff?scope=" + scope + "&return_to=/",
    ):
        page = dashboard.get(
            "/dashboard/handoff-detail",
            params={"scope": scope, "artifact": ref["artifact_id"], "revision": "1", "return_to": target},
        )
        assert page.status_code == 200
        download = _links(page.text, "/dashboard/handoff-download")[0]
        assert "return_to" not in parse_qs(urlsplit(download).query)
        assert dashboard.get(download).status_code == 200
