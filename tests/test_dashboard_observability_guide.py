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

"""Keep the monitoring guide usable without models, saved content or a running stack."""

import re
from html import unescape

import pytest

from tests.test_dashboard import dashboard as dashboard


@pytest.mark.parametrize("language,title", [("zh", "云原生监控"), ("en", "Cloud-native monitoring")])
def test_monitoring_guide_has_navigation_and_actionable_commands(dashboard, language, title):
    response = dashboard.get("/dashboard/observability", params={"lang": language})
    assert response.status_code == 200, response.text
    assert f"<title>{title} · PowerContext</title>" in response.text
    assert 'aria-current="page"' in response.text
    assert "/dashboard/observability" in response.text
    article = re.search(r"<article\b[^>]*data-observability-guide[^>]*>(.*?)</article>", response.text, re.S)
    assert article is not None
    text = unescape(article[1])
    for command in (
        "powercontext observability up --server-env /path/to/existing.env",
        "powercontext server run --env-file /path/to/existing.env --observability",
        "powercontext observability status",
        "powercontext observability check --exercise",
        "powercontext observability down",
        "powercontext observability connect",
    ):
        assert command in text
    for component in ("Grafana", "Prometheus", "Tempo", "Loki", "Phoenix", "Docker Compose"):
        assert component in text
    partial = dashboard.get("/dashboard/observability", params={"lang": language}, headers={"HX-Request": "true"})
    assert partial.status_code == 200
    assert "data-observability-guide" in partial.text
    assert "<html" not in partial.text


def test_monitoring_guide_keeps_dashboard_authentication(dashboard):
    dashboard.headers.pop("Authorization")
    response = dashboard.get("/dashboard/observability")
    assert response.status_code == 401
    assert "data-observability-guide" not in response.text
