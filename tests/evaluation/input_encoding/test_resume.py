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

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from evaluation.performance.input_encoding import runner
from evaluation.performance.input_encoding.identity import ConfigurationIdentityError


class RuntimeBoundaryReached(RuntimeError):
    """Stop a new local fixture after its real manifest is saved, before services."""


@pytest.mark.parametrize("rotation", ["database", "forbidden", "credential", "legacy_manifest"])
def test_run_rejects_changed_private_identity_before_creating_execution_artifacts(tmp_path, monkeypatch, rotation):
    environment = tmp_path / "fixture.env"
    environment.write_text(
        "POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL=openai-chat:example\nOPENAI_API_KEY=provider-before\n"
    )
    database = tmp_path / "database.json"
    database.write_text(
        json.dumps({
            "kind": "oceanbase",
            "url": "mysql+aoceanbase://user:database-before@localhost:2881/example?charset=utf8mb4",
        })
    )
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps({"forbidden_values": ["private-account-before"]}))
    cases = tmp_path / "cases.json"
    cases.write_text(json.dumps(["fixture-case"]))
    args = SimpleNamespace(
        dataset=tmp_path / "unused-dataset",
        case_ids=cases,
        limit=1,
        repeats=1,
        seed=2086,
        output=tmp_path / "run",
        env_file=environment,
        database_config=database,
        protection_config=policy,
        max_output_tokens=4096,
        cohort="resume-fixture",
        concurrency=1,
        timeout=10,
    )

    @asynccontextmanager
    async def stop_before_services(*args, **kwargs):
        raise RuntimeBoundaryReached
        yield  # pragma: no cover - this fixture never opens a database or provider

    monkeypatch.setattr(runner, "load_catalog", lambda path: SimpleNamespace(require=lambda name: object()))
    monkeypatch.setattr(runner, "open_builtin_runtime", stop_before_services)
    with pytest.raises(RuntimeBoundaryReached):
        asyncio.run(runner.run(args))
    manifest = args.output / "manifest.json"
    saved = json.loads(manifest.read_text())
    assert saved["configuration_identity"]["private_config_digest"]

    if rotation == "database":
        database.write_text(database.read_text().replace("database-before", "database-after"))
    elif rotation == "forbidden":
        policy.write_text(json.dumps({"forbidden_values": ["private-account-after"]}))
    elif rotation == "credential":
        environment.write_text(environment.read_text().replace("provider-before", "provider-after"))
    else:
        del saved["configuration_identity"]
        manifest.write_text(json.dumps(saved))
    before = {
        str(path.relative_to(args.output)): path.read_bytes() for path in args.output.rglob("*") if path.is_file()
    }

    with pytest.raises(ConfigurationIdentityError) as captured:
        asyncio.run(runner.run(args))
    assert "-after" not in str(captured.value)
    assert {
        str(path.relative_to(args.output)): path.read_bytes() for path in args.output.rglob("*") if path.is_file()
    } == before
    assert not (args.output / "attempts").exists()
