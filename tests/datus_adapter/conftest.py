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


"""Shared synthetic evaluator inputs for Datus component tests."""

import json
import sqlite3

import pytest
from gateway_fixture import TABLE
from powercontext_datus.freeze import digest_json
from powercontext_datus.paired import file_hash


@pytest.fixture
def plan(tmp_path):
    for arm in ("native", "enhanced"):
        (tmp_path / arm).mkdir()
    skill = tmp_path / "enhanced/fixture"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: fixture\ndescription: Synthetic instruction.\n---\nUse read-only SQL.\n"
    )
    common = tmp_path / "common.txt"
    common.write_text("Synthetic component schema: sample(value integer). Not learning or benchmark evidence.")
    db = sqlite3.connect(tmp_path / "database.sqlite")
    db.executescript("CREATE TABLE sample(value INTEGER); INSERT INTO sample VALUES (7),(7),(NULL);")
    db.close()
    tasks = [{"task_id": "fixture-1", "question": "List the sample values."}]
    oracle_file = tmp_path / "oracle.json"
    oracle_file.write_text(json.dumps({"fixture-1": {"expected": TABLE, "declared_answer": TABLE, "ordered": True}}))
    admission_file = tmp_path / "admission.json"
    admission_file.write_text(
        json.dumps({
            "evidence_kind": "component_fixture",
            "roster_sha256": digest_json(tasks),
            "common_sha256": file_hash(common),
            "oracle_sha256": file_hash(oracle_file),
        })
    )
    return {
        "evidence_kind": "component_fixture",
        "common_file": str(common),
        "oracle_file": str(oracle_file),
        "admission_file": str(admission_file),
        "database_file": str(tmp_path / "database.sqlite"),
        "tasks": tasks,
        "timeout_seconds": 90,
        "public": {
            "model": {"type": "openai", "model": "gpt-4o-mini", "base_url": "", "temperature": 0},
            "max_turns": 6,
            "current_date": "2026-01-01",
            "database": {"type": "sqlite", "name": "fixture"},
        },
        "arms": {
            "native": {"skill_root": str(tmp_path / "native"), "skill_names": []},
            "enhanced": {"skill_root": str(tmp_path / "enhanced"), "skill_names": ["fixture"]},
        },
    }
