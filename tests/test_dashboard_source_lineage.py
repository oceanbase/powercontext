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

"""Keep existing Dashboard navigation while showing authoritative Source lineage."""

import re

import pytest

from tests.test_dashboard import create_experience, create_scope, create_skill
from tests.test_dashboard import dashboard as dashboard


def test_source_lineage_types_and_empty_states(dashboard):
    scope = create_scope(dashboard, "Source lineage")["scope_id"]
    for text in ("Unprocessed source", "Source with Artifact"):
        response = dashboard.post("/v1/sources/content", json={"scope_id": scope, "source_id": text, "content": text})
        assert response.status_code == 202, response.text
    source_id = response.json()["source"]["source_id"]
    create_experience(dashboard, scope, response.json()["source"], "Source with Artifact", "Saved output")
    create_skill(dashboard, scope, response.json()["source"], "source-skill")
    page = dashboard.get("/dashboard/processing", params={"scope": scope, "record_type": "sources", "lang": "en"})
    assert page.status_code == 200, page.text
    rows = re.findall(r'<tr class="processing-source-row">(.*?)</tr>', page.text, re.S)
    assert len(rows) >= 2
    cited = next(row for row in rows if "Source with Artifact" in row)
    uncited = next(row for row in rows if "Unprocessed source" in row)
    assert re.findall(r'data-artifact-family="([^"]+)"', cited) == ["experience", "skill"]
    assert "Experience" in cited and "Skill" in cited
    assert "data-artifact-family" not in uncited
    assert '<span class="text-secondary">None</span>' in uncited
    detail = dashboard.get(
        "/dashboard/processing", params={"scope": scope, "record_type": "sources", "source_id": source_id, "lang": "en"}
    )
    assert detail.status_code == 200, detail.text
    assert "Saved output" in detail.text
    for family, label in (("experience", "Experience"), ("skill", "Skill")):
        assert f'id="artifact-{family}"' in detail.text
        assert f'<h2 class="processing-section-title">{label}</h2>' in detail.text
    assert detail.text.index('id="artifact-experience"') < detail.text.index("Saved output")
    assert "Review the report" in detail.text and "Keep the documented limits." in detail.text
    for language in ("zh", "en"):
        home = dashboard.get("/dashboard/home", params={"scope": scope, "lang": language})
        assert home.status_code == 200, home.text
        panel = home.text.split('id="recent-processing"', 1)[1].split("</section>", 1)[0]
        rows = re.findall(r"<tr>(.*?)</tr>", panel, re.S)
        empty = next(row for row in rows if "Unprocessed source" in row)
        linked = next(row for row in rows if "Source with Artifact" in row)
        assert '<span class="text-secondary">None</span>' in empty
        assert "Experience" in linked and "Skill" in linked


@pytest.mark.parametrize("public_reads", [False, True])
def test_source_detail_preserves_the_complete_structured_original(dashboard, monkeypatch, public_reads):
    scope = create_scope(dashboard, "Structured original")["scope_id"]
    if public_reads:
        monkeypatch.setattr("powercontext.server.dashboard.api._builtin_runtime", lambda request: None)
    original = {"title": "Short heading", "body": "Full original body", "extra": {"count": 3}}
    saved = dashboard.post(f"/v1/scopes/{scope}/sources", json={"content": original})
    assert saved.status_code == 201, saved.text
    detail = dashboard.get("/dashboard/processing", params={"scope": scope, "source_id": saved.json()["source_id"]})
    assert detail.status_code == 200, detail.text
    assert "Short heading" in detail.text and "Full original body" in detail.text
    assert "count" in detail.text and "3" in detail.text


def test_source_result_tags_and_pagination_reach_every_type(dashboard):
    from html import unescape

    scope = create_scope(dashboard, "Many linked results")["scope_id"]
    saved = dashboard.post(
        "/v1/sources/content", json={"scope_id": scope, "source_id": "shared", "content": "Shared evidence"}
    )
    source = saved.json()["source"]
    for index in range(20):
        create_experience(dashboard, scope, source, f"Situation {index}", f"Lesson {index}")
    create_skill(dashboard, scope, source, "linked-skill")
    params = {"scope": scope, "record_type": "sources", "lang": "en"}
    listing = dashboard.get("/dashboard/processing", params=params)
    skill_link = re.search(r'data-artifact-family="skill" href="([^"]+)"', listing.text)
    assert skill_link is not None
    opened = dashboard.get(unescape(skill_link[1]))
    assert opened.status_code == 200, opened.text
    assert 'id="artifact-skill"' in opened.text and "Keep the documented limits." in opened.text
    detail = dashboard.get("/dashboard/processing", params={**params, "source_id": "shared"})
    assert detail.status_code == 200, detail.text
    assert detail.text.count('class="processing-output-entry"') == 20
    following = re.search(r'href="([^"]*processing_artifacts_cursor=[^"]+)"', detail.text)
    assert following is not None
    next_page = dashboard.get(unescape(following[1]))
    assert next_page.status_code == 200, next_page.text
    assert 'id="artifact-skill"' in next_page.text
    assert "Keep the documented limits." in next_page.text
    # A cursor for the complete result collection cannot be reused for one selected type.
    from urllib.parse import parse_qs, urlsplit

    cursor = parse_qs(urlsplit(unescape(following[1])).query)["processing_artifacts_cursor"][0]
    crossed = dashboard.get(
        "/dashboard/processing",
        params={**params, "source_id": "shared", "artifact_family": "skill", "processing_artifacts_cursor": cursor},
    )
    assert crossed.status_code == 400, crossed.text
