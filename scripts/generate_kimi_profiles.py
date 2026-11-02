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

"""Generate the Kimi opt-in MCP profiles from the public Server catalog and capability contract."""

from __future__ import annotations

import argparse
import json
import tomllib
from pathlib import Path

from integration_manifest import FULL_CAPABILITIES, RECOMMENDED_CAPABILITIES
from mcp_catalog import server_mcp_tools

ROOT = Path(__file__).resolve().parents[1]
DESTINATION = ROOT / "integrations/kimi-code/plugins/powercontext/profiles.json"


def render() -> str:
    manifest = tomllib.loads((ROOT / "integrations/capabilities.toml").read_text(encoding="utf-8"))
    tools = next(item["tools"] for item in manifest["toolsets"] if item["id"] == "server-mcp")
    available = {tool.name for tool in server_mcp_tools()}
    profiles = {"minimal": ["search_memory", "remember_memory", "get_artifact_revision"]}
    for profile, capabilities in (("recommended", RECOMMENDED_CAPABILITIES), ("full", FULL_CAPABILITIES)):
        selected = {
            tool["id"]
            for tool in tools
            if set(tool.get("capabilities", [])) & capabilities and tool["id"] != "get_handoff_report"
        }
        if profile == "full":
            selected.update({"approve_candidate", "reject_candidate", "revise_candidate"})
        if selected - available:
            message = f"Profile references unavailable MCP tools: {sorted(selected - available)}"
            raise ValueError(message)
        profiles[profile] = sorted(selected)
    return json.dumps({"schema": "powercontext.kimi-code.profiles.v1", "profiles": profiles}, indent=2) + "\n"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    content = render()
    if args.check:
        if not DESTINATION.is_file() or DESTINATION.read_text(encoding="utf-8") != content:
            message = "Kimi profiles are stale; run scripts/generate_kimi_profiles.py"
            raise SystemExit(message)
    else:
        DESTINATION.write_text(content, encoding="utf-8", newline="\n")
