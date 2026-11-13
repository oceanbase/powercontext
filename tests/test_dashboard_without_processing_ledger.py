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

from tests.test_dashboard import create_experience, create_scope, create_skill
from tests.test_dashboard import dashboard as dashboard


def test_source_lineage_tag_and_original_navigation(dashboard):
    scope = create_scope(dashboard, "Source lineage")["scope_id"]
    for text in ("Unprocessed source", "Source with Artifact"):
        response = dashboard.post("/v1/sources/content", json={"scope_id": scope, "source_id": text, "content": text})
        assert response.status_code == 202, response.text
    source_id = response.json()["source"]["source_id"]
    create_experience(dashboard, scope, response.json()["source"], "Source with Artifact", "Saved output")
    create_skill(dashboard, scope, response.json()["source"], "source-skill")
    create_experience(dashboard, scope, response.json()["source"], "Another situation", "Second saved output")
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
    assert "Artifact formed" not in cited
    nav = page.text.split('id="dashboard-navigation"', 1)[1].split("</aside>", 1)[0]
    for target in ("runtime", "observability", "methods", "review", "prompts"):
        assert "/dashboard/" + target in nav
    assert "nav-section" in nav and "nav-symbol" in nav
    assert "view=records" not in nav
    detail = dashboard.get(
        "/dashboard/processing", params={"scope": scope, "record_type": "sources", "source_id": source_id, "lang": "en"}
    )
    assert detail.status_code == 200, detail.text
    assert "Saved output" in detail.text
    assert "Second saved output" in detail.text
    for family, label in (("experience", "Experience"), ("skill", "Skill")):
        assert f'id="artifact-{family}"' in detail.text
        assert f'<h2 class="processing-section-title">{label}</h2>' in detail.text
    assert detail.text.index('id="artifact-experience"') < detail.text.index("Saved output")
    assert "Review the report" in detail.text and "Keep the documented limits." in detail.text


def test_removed_processing_and_feedback_routes(dashboard):
    scope = create_scope(dashboard, "Removed ledger")["scope_id"]
    for suffix in (
        "processing",
        "processing/summary",
        "sources/content/example/processing",
        "artifacts/atomic-memory/example/revisions/1/processing",
    ):
        assert dashboard.get(f"/v1/scopes/{scope}/{suffix}").status_code == 404
    assert dashboard.post("/dashboard/usage/feedback", json={}).status_code == 404
