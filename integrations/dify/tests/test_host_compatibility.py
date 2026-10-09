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

"""Official daemon serialization and host helpers before SDK/HTTP; not live deployment."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import httpx
import pytest
import yaml
from host_helpers import cast_parameters, daemon_tools, model_schema
from jsonschema import Draft7Validator
from test_plugin_contract import run

pytest_plugins = ["test_plugin_contract"]
pytestmark = pytest.mark.skipif(
    not (os.environ.get("POWERCONTEXT_DIFY_SOURCE") and os.environ.get("POWERCONTEXT_DIFY_DAEMON_SOURCE")),
    reason="Set the pinned Dify and daemon source paths",
)


def test_probed_daemon_matches_default_host_compose():
    host = Path(os.environ["POWERCONTEXT_DIFY_SOURCE"])
    daemon = Path(os.environ["POWERCONTEXT_DIFY_DAEMON_SOURCE"])
    compose = yaml.safe_load((host / "docker/docker-compose.yaml").read_text(encoding="utf-8"))
    assert compose["services"]["plugin_daemon"]["image"] == "langgenius/dify-plugin-daemon:0.6.10-local"
    for root, expected in (
        (host, "8387590ace4a094de812b7847fc6a4c3a27cd52b"),
        (daemon, "1310a18b2f6bc6f18768a0a6265484830891433c"),
    ):
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True, timeout=10
        )
        assert result.stdout.strip() == expected


def test_nested_workflow_selectors_resolve_with_official_host_helpers(registry, tmp_path):
    loaded = daemon_tools(registry)
    schema = tmp_path / "schemas.json"
    schema.write_text(
        json.dumps({name: declaration["output_schema"] for name, declaration in loaded.items()}, ensure_ascii=False),
        encoding="utf-8",
    )
    result = subprocess.run(
        ["node", "--experimental-strip-types", str(Path(__file__).with_name("host_output_probe.ts")), str(schema)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=30,
    )
    assert json.loads(result.stdout) == {"tools": 20, "selectors": 20}


@pytest.mark.parametrize("target", [{}, 42, "not JSON"])
def test_host_cast_does_not_hide_invalid_optional_targets(registry, transport, target):
    loaded = daemon_tools(registry)
    calls = transport(lambda _request: pytest.fail("Malformed target must not generate"))
    parameters = {"source_refs": '[{"name":"content","source_id":"turn-1"}]', "target": json.dumps(target)}
    cast = cast_parameters(loaded["pc_experience_generate"], parameters)
    result = run(registry, "pc_experience_generate", cast)
    assert result["error"]["code"] == "invalid_request"
    assert all(path != "/v1/experiences/generate" for path, _ in calls)


def test_host_cast_preserves_encoded_null_and_complete_reference_values(registry):
    loaded = daemon_tools(registry)
    reference = {"family": "experience", "artifact_id": "e-1", "revision": 2}
    for value in (None, reference):
        parameters = {"target": json.dumps(value), "reason": "null"}
        assert cast_parameters(loaded["pc_experience_generate"], parameters) == parameters


def test_all_model_schemas_and_defaults_survive_default_daemon_discovery(registry):
    _, _, loaded = registry.tools_mapping["powercontext"]
    serialized = daemon_tools(registry)
    assert set(serialized) == set(loaded)
    for name, declaration in serialized.items():
        schema = model_schema(declaration)
        Draft7Validator.check_schema(schema)
        assert declaration["output_schema"] == loaded[name][0].output_schema
        for parameter in declaration["parameters"]:
            wire = schema["properties"][parameter["name"]]
            if parameter["default"] is not None:
                Draft7Validator(wire).validate(parameter["default"])
            if "Decoded JSON Schema: " in wire["description"]:
                decoded = json.loads(wire["description"].split("Decoded JSON Schema: ", 1)[1])
                Draft7Validator.check_schema(decoded)
                Draft7Validator(wire).validate("null")
                if parameter["default"] is not None:
                    Draft7Validator(decoded).validate(json.loads(parameter["default"]))
        assert "reason" not in schema["required"]


@pytest.mark.parametrize("reason,decoded", [("null", None), ('"理由"', "理由"), ('"null"', "null")])
def test_json_text_preserves_nullable_strings_without_double_decoding(registry, transport, reason, decoded):
    loaded = daemon_tools(registry)
    calls = transport(lambda _request: httpx.Response(200, json={"status": "no_op", "candidate": None}))
    parameters = {"source_refs": '[{"name":"content","source_id":"turn-1"}]', "target": "null", "reason": reason}
    result = run(registry, "pc_experience_generate", cast_parameters(loaded["pc_experience_generate"], parameters))
    assert result["ok"] is True
    assert calls[-1][1]["target"] is None
    assert calls[-1][1]["reason"] == decoded
    assert calls[-1][1]["source_refs"] == [{"name": "content", "source_id": "turn-1"}]
    assert calls[-1][1]["artifact_refs"] == []


def test_omitted_nullable_inputs_remain_omitted_with_array_defaults(registry, transport):
    calls = transport(lambda _request: httpx.Response(200, json={"status": "no_op", "candidate": None}))
    parameters = {"source_refs": '[{"name":"content","source_id":"turn-1"}]'}
    cast = cast_parameters(daemon_tools(registry)["pc_experience_generate"], parameters)
    result = run(registry, "pc_experience_generate", cast)
    assert result["ok"] is True
    assert "reason" not in calls[-1][1] and "target" not in calls[-1][1]
    assert calls[-1][1]["artifact_refs"] == []


@pytest.mark.parametrize("reason", ["unquoted reason", "", "NaN", "Infinity", "{not JSON}"])
def test_malformed_json_text_is_rejected_before_generation(registry, transport, reason):
    calls = transport(lambda _request: pytest.fail("Malformed JSON text must not generate"))
    parameters = {"source_refs": '[{"name":"content","source_id":"turn-1"}]', "reason": reason}
    cast = cast_parameters(daemon_tools(registry)["pc_experience_generate"], parameters)
    result = run(registry, "pc_experience_generate", cast)
    assert result["error"]["code"] == "invalid_request"
    assert all(path != "/v1/experiences/generate" for path, _ in calls)
