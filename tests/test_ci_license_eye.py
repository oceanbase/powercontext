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

import os
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parents[1]
ACTION = yaml.safe_load((ROOT / ".github/actions/setup-license-eye/action.yml").read_text())
STEPS = ACTION["runs"]["steps"]
BY_ID = {step["id"]: step for step in STEPS if "id" in step}


def test_warm_cache_skips_source_download_toolchain_setup_and_compilation():
    for name in ("source", "go", "build"):
        assert BY_ID[name]["if"] == "steps.cache.outputs.cache-hit != 'true'"
    key = BY_ID["cache"]["with"]["key"]
    assert BY_ID["source"]["env"]["LICENSE_EYE_REVISION"] in key
    assert BY_ID["go"]["with"]["go-version"] in key
    assert "${{ runner.os }}" in key and "${{ runner.arch }}" in key
    assert "restore-keys" not in BY_ID["cache"]["with"]


def test_cold_cache_uses_the_fetched_repository_root_for_go_dependencies():
    assert STEPS.index(BY_ID["source"]) < STEPS.index(BY_ID["go"])
    assert BY_ID["go"]["with"]["cache-dependency-path"] == "${{ runner.temp }}/license-eye/source/go.sum"
    assert 'test -f "$source_dir/go.sum"' in BY_ID["source"]["run"]
    assert "180s" in BY_ID["source"]["run"]


@pytest.mark.parametrize("exit_code", [0, 7])
def test_cached_checker_must_be_executable_before_it_can_be_saved(tmp_path, exit_code):
    binary = tmp_path / "license-eye/bin/license-eye"
    binary.parent.mkdir(parents=True)
    binary.write_text(f"#!/bin/sh\nexit {exit_code}\n")
    binary.chmod(0o755)
    result = subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", BY_ID["verify"]["run"]],  # noqa: S607
        env={**os.environ, "RUNNER_TEMP": str(tmp_path)},
        check=False,
    )
    assert result.returncode == exit_code
    save = next(step for step in STEPS if step.get("uses", "").startswith("actions/cache/save@"))
    assert STEPS.index(BY_ID["verify"]) < STEPS.index(save)
    assert "always()" not in save["if"]
    assert save["with"]["path"] == BY_ID["cache"]["with"]["path"]
    assert save["with"]["key"] == "${{ steps.cache.outputs.cache-primary-key }}"
    for step in STEPS:
        assert not step.get("continue-on-error")
