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

"""Declaration validation in a real child within its existing startup budget."""

import asyncio
import os

import httpx
import pytest

from powercontext.builtin.artifact_plugin_validation import validate_plugin
from tests.e2e.test_artifact_plugins import EXAMPLE, REGISTRATION, deploy_plugin, enable_all


def test_declaration_validation_does_not_spend_its_budget_starting_the_runtime(tmp_path, monkeypatch):
    plugin = deploy_plugin(EXAMPLE, tmp_path / "plugin", None, config={})
    entry = plugin / "plugin.py"
    entry.write_text(
        entry.read_text()
        + '\nimport os\nos.environ["PC_TEST_PLUGIN_IMPORTED"] = "child"\n'
        + 'print("plugin import output is not the declaration protocol")\n'
    )
    monkeypatch.delenv("PC_TEST_PLUGIN_IMPORTED", raising=False)
    # A slow runtime bootstrap must not consume declaration validation's 15-second budget.
    # This hook is installed only in new Python processes, not in the pytest process.
    hook = tmp_path / "startup"
    hook.mkdir()
    (hook / "sitecustomize.py").write_text(
        "import sys, time\n"
        "class SlowRuntime:\n"
        "    def find_spec(self, fullname, path=None, target=None):\n"
        "        if fullname == 'powercontext.builtin.runtime':\n"
        "            time.sleep(20)\n"
        "sys.meta_path.insert(0, SlowRuntime())\n"
    )
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(filter(None, (str(hook), os.environ.get("PYTHONPATH")))))

    async def run():
        described = await validate_plugin(str(plugin))
        restored = await validate_plugin(str(plugin), described["manifest"])
        assert "PC_TEST_PLUGIN_IMPORTED" not in os.environ
        assert restored == described
        assert described["declaration"]["family"] == "incident-report"
        assert described["manifest"]["config"]["title_prefix"] == "Incident report"

    asyncio.run(run())


def test_invalid_declaration_is_rejected_by_the_validation_child(tmp_path):
    plugin = deploy_plugin(EXAMPLE, tmp_path / "invalid", None, config={"unknown": True})

    async def run():
        with pytest.raises(ValueError, match="plugin declaration or configuration validation failed"):
            await validate_plugin(str(plugin))

    asyncio.run(run())


def test_enable_all_reports_unexpected_item_failure_immediately():
    item = {
        "binding_name": "incident-reports",
        "outcome": "failed",
        "status_url": REGISTRATION,
        "state": {"phase": "disabled"},
        "error": "invalid_plugin",
    }

    async def run():
        transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"items": [item]}))
        async with httpx.AsyncClient(base_url="http://test", transport=transport) as client:
            with pytest.raises(AssertionError, match="invalid_plugin"):
                await enable_all(client)
            results = await enable_all(client, expected_failures=frozenset({"incident-reports"}))
            assert results["incident-reports"] == item

    asyncio.run(run())
