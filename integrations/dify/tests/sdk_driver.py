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

"""Invoke registered SDK entries in a separate gevent-patched process.

This is an SDK/HTTP test driver, not a Dify plugin-daemon or model simulator.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import gevent
from dify_plugin import DifyPluginEnv
from dify_plugin.core.plugin_registration import PluginRegistration
from dify_plugin.entities.tool import ToolRuntime
from jsonschema import Draft7Validator

PLUGIN = Path(__file__).resolve().parents[1] / "plugin"


def registration():
    os.chdir(PLUGIN)
    sys.path.insert(0, str(PLUGIN))
    return PluginRegistration(DifyPluginEnv())


def invoke(registry, job):
    _, _, tools = registry.tools_mapping["powercontext"]
    declaration, implementation = tools[job["tool"]]
    entry = implementation(
        runtime=ToolRuntime(credentials=job["credentials"], user_id=None, session_id=None),
        session=object(),
    )
    parameters = job.get("parameters", {})
    if job.get("host_cast"):
        from host_helpers import cast_parameters, daemon_tools

        parameters = cast_parameters(daemon_tools(registry)[job["tool"]], parameters)
    messages = list(entry.invoke(parameters))
    envelope = next(message.message.json_object for message in messages if message.type.value == "json")
    variables = {
        message.message.variable_name: message.message.variable_value
        for message in messages
        if message.type.value == "variable"
    }
    assert variables == {**envelope, "result": envelope["data"] if envelope["ok"] else {}}
    Draft7Validator(declaration.output_schema).validate(variables)
    return envelope


def main():
    registry = registration()
    for line in sys.stdin:
        jobs = json.loads(line)
        greenlets = [gevent.spawn(invoke, registry, job) for job in jobs]
        gevent.joinall(greenlets, raise_error=True)
        print(json.dumps([job.get() for job in greenlets], ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
