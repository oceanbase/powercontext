# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Selected contract generation must ignore unrelated OpenAPI changes and detect drift."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml


def generate(repository: Path, *, check: bool = False) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, str(repository / "integrations/dify/generate_contract.py")]
    if check:
        command.append("--check")
    return subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    project = Path(__file__).resolve().parents[1]
    for relative in ("generate_contract.py", "catalog.py", "plugin/powercontext_dify/policy.py"):
        destination = tmp_path / "integrations/dify" / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(project / relative, destination)
    (tmp_path / "openapi").mkdir()
    shutil.copyfile(project.parents[1] / "openapi/powercontext.yaml", tmp_path / "openapi/powercontext.yaml")
    result = generate(tmp_path)
    assert result.returncode == 0, result.stderr
    return tmp_path


def generated_files(repository: Path) -> dict[str, bytes]:
    plugin = repository / "integrations/dify/plugin"
    paths = [plugin / "powercontext_dify/contract.json", *sorted((plugin / "tools").iterdir())]
    return {str(path.relative_to(plugin)): path.read_bytes() for path in paths}


@pytest.mark.parametrize("change", ["comment", "operation", "schema"])
def test_unrelated_openapi_changes_leave_all_dify_outputs_unchanged(repository: Path, change: str) -> None:
    before = generated_files(repository)
    contract = repository / "openapi/powercontext.yaml"
    if change == "comment":
        contract.write_text(
            contract.read_text(encoding="utf-8") + "\n# Unrelated documentation edit.\n", encoding="utf-8"
        )
    else:
        spec = yaml.safe_load(contract.read_text(encoding="utf-8"))
        if change == "operation":
            spec["paths"]["/v1/unrelated-generator-regression"] = {
                "get": {"operationId": "unrelated_generator_regression", "responses": {"204": {"description": "Empty"}}}
            }
        else:
            spec["components"]["schemas"]["UnrelatedGeneratorRegression"] = {"type": "string", "maxLength": 42}
        contract.write_text(yaml.safe_dump(spec, allow_unicode=True, sort_keys=False), encoding="utf-8")
    checked = generate(repository, check=True)
    assert checked.returncode == 0, checked.stderr
    regenerated = generate(repository)
    assert regenerated.returncode == 0, regenerated.stderr
    assert generated_files(repository) == before


def test_consumed_schema_changes_fail_stale_checks_and_update_outputs(repository: Path) -> None:
    before = generated_files(repository)
    contract = repository / "openapi/powercontext.yaml"
    spec = yaml.safe_load(contract.read_text(encoding="utf-8"))
    record = spec["components"]["schemas"]["AtomicMemoryRecord"]
    record["properties"]["text"]["description"] = "Updated retained evidence."
    contract.write_text(yaml.safe_dump(spec, allow_unicode=True, sort_keys=False), encoding="utf-8")

    stale = generate(repository, check=True)
    assert stale.returncode != 0
    assert "contract/declarations require regeneration" in stale.stderr
    assert generated_files(repository) == before

    regenerated = generate(repository)
    assert regenerated.returncode == 0, regenerated.stderr
    after = generated_files(repository)
    assert after["powercontext_dify/contract.json"] != before["powercontext_dify/contract.json"]
    assert after["tools/pc_search.yaml"] != before["tools/pc_search.yaml"]
    spec = json.loads(after["powercontext_dify/contract.json"])
    assert (
        spec["components"]["schemas"]["AtomicMemoryRecord"]["properties"]["text"]["description"]
        == "Updated retained evidence."
    )
    checked = generate(repository, check=True)
    assert checked.returncode == 0, checked.stderr


@pytest.mark.parametrize("artifact", ["powercontext_dify/contract.json", "tools/pc_search.yaml", "tools/pc_search.py"])
def test_check_rejects_drift_in_contract_and_tool_declarations(repository: Path, artifact: str) -> None:
    path = repository / "integrations/dify/plugin" / artifact
    path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    stale = generate(repository, check=True)
    assert stale.returncode != 0
    assert artifact in stale.stderr
