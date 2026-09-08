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

"""Native Datus component smoke in the separately locked Python 3.12 runtime.

This is NOT a model run, an independent learning example, or a QA benchmark.
It reads only caller-supplied package files and (optionally) SELECT 1.
"""

# ruff: noqa: TRY003 - bounded diagnostics never include credentials or connector exception text.

from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.metadata
import json
import logging
import os
import platform
from pathlib import Path
from typing import Any

from powercontext_datus import DATUS_COMMIT
from powercontext_datus.freeze import IntegrityError, snapshot, verify_snapshot


def verify_runtime() -> dict[str, object]:
    distribution = importlib.metadata.distribution("datus-agent")
    source = json.loads(distribution.read_text("direct_url.json") or "{}")
    expected = f"https://github.com/Datus-ai/Datus-agent/archive/{DATUS_COMMIT}.tar.gz"
    if distribution.version != "0.4.0" or source.get("url") != expected:
        raise IntegrityError("unexpected Datus distribution; use the committed runtime lock")
    packages = ["datus-agent", "datus-mysql", "datus-sqlalchemy", "datus-db-core", "openai-agents", "httpx"]
    versions = {name: importlib.metadata.version(name) for name in packages}
    if platform.python_version_tuple()[:2] != ("3", "12") or versions["datus-mysql"] != "0.1.8":
        raise IntegrityError("unexpected Python or MySQL adapter version")
    return {
        "python": platform.python_version(),
        "datus_commit": DATUS_COMMIT,
        "packages": versions,
        "source_archive": source.get("archive_info", {}),
    }


def skill_manager_for(skill_root: Path, expected_names: list[str]) -> Any:
    """Build an explicit native manager; do not use from_dict's implicit directories.

    This manager must actually be injected into the node by the caller. Creating
    it does not reconfigure Datus CLI defaults or prove a complete tool sandbox.
    """
    verify_runtime()
    snapshot(skill_root)
    if len(set(expected_names)) != len(expected_names):
        raise IntegrityError("duplicate expected Skill names")
    config_class = importlib.import_module("datus.tools.skill_tools.skill_config").SkillConfig
    manager_class = importlib.import_module("datus.tools.skill_tools.skill_manager").SkillManager
    config = config_class(directories=[str(skill_root.resolve())], auto_sync=False)
    manager = manager_class(config=config, config_mutable=False)
    inventory = sorted(skill.name for skill in manager.registry.list_skills())
    visible = sorted(skill.name for skill in manager.get_available_skills("GenSQL", node_class="gen_sql"))
    if inventory != sorted(expected_names) or visible != inventory:
        raise IntegrityError("effective native Skill inventory differs from frozen allowlist")
    return manager


def skill_smoke(skill_root: Path, expected_names: list[str]) -> dict[str, object]:
    before = snapshot(skill_root)
    manager = skill_manager_for(skill_root, expected_names)
    loaded = {}
    for name in expected_names:
        success, _, content = manager.load_skill(name, "GenSQL", node_class="gen_sql")
        if not success or not content:
            raise IntegrityError("native Skill loading failed")
        loaded[name] = hashlib.sha256(content.encode()).hexdigest()
    verify_snapshot(skill_root, before)
    return {"inventory": sorted(expected_names), "loaded_content_sha256": loaded, "files": before}


def database_smoke() -> dict[str, object]:
    """The authorized read-only account is supplied through task-scoped env only."""
    connector_class = importlib.import_module("datus_mysql").MySQLConnector
    config = {
        "host": os.environ["DATUS_DB_HOST"],
        "port": int(os.environ.get("DATUS_DB_PORT", "3306")),
        "username": os.environ["DATUS_DB_USER"],
        "password": os.environ["DATUS_DB_PASSWORD"],
        "database": os.environ["DATUS_DB_NAME"],
    }
    connector = connector_class(config)
    try:
        result = connector.execute_query("SELECT 1 AS adapter_smoke", result_format="list")
        success = bool(result.success and result.sql_return == [{"adapter_smoke": 1}])
        return {
            "success": success,
            "sql": "SELECT 1 AS adapter_smoke",
            "row_count": result.row_count,
            "result": result.sql_return if success else None,
            "error": None if success else "native_query_failed",
        }
    finally:
        connector.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skill-root", type=Path, required=True)
    parser.add_argument("--expected-skill", action="append", default=[])
    parser.add_argument("--db", action="store_true", help="Opt in to the environment-configured SELECT 1 probe")
    args = parser.parse_args()
    # No third-party debug logs or connection exceptions may reveal a URL/password.
    logging.disable(logging.CRITICAL)
    report: dict[str, object] = {"evidence_kind": "native_component_smoke", "native_agent_qa_runs": 0}
    try:
        structlog = importlib.import_module("structlog")
        structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.CRITICAL))
        report["runtime"] = verify_runtime()
        report["skills"] = skill_smoke(args.skill_root, args.expected_skill)
        database = database_smoke() if args.db else {"status": "not_requested"}
        report["database"] = database
        report["status"] = "passed" if not args.db or database["success"] else "failed"
    except Exception as error:
        report.update(status="failed", error_type=type(error).__name__)
    print(json.dumps(report, sort_keys=True, allow_nan=False))
    if report["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
