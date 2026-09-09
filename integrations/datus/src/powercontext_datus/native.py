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
import re
import sys
from collections.abc import Iterator
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any, TextIO

from powercontext_datus import DATUS_COMMIT
from powercontext_datus.admission import read_document, read_secret, validate_safety
from powercontext_datus.freeze import IntegrityError, snapshot, verify_snapshot
from powercontext_datus.transport import mysql_connector


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
    before = snapshot(skill_root)
    if len(set(expected_names)) != len(expected_names) or any(
        not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name) or len(name) > 64 for name in expected_names
    ):
        raise IntegrityError("invalid or duplicate expected Skill names")
    root = skill_root.resolve()
    expected_entries = {root / name / "SKILL.md": name for name in expected_names}
    # Inspect physical entries before native scanning can silently deduplicate
    # names. Packages have exactly the root/name/SKILL.md layout installed by
    # delivery.py: root-level, nested or aliased entries are never alternatives.
    if set(root.rglob("SKILL.md")) != expected_entries.keys():
        raise IntegrityError("Skill entrypoints differ from exact package layout")
    config_class = importlib.import_module("datus.tools.skill_tools.skill_config").SkillConfig
    manager_class = importlib.import_module("datus.tools.skill_tools.skill_manager").SkillManager
    registry_class = importlib.import_module("datus.tools.skill_tools.skill_registry").SkillRegistry
    config = config_class(directories=[str(root)], auto_sync=False)
    registry = registry_class(config=config)
    # Use the pinned native parser without populating its deduplicating registry.
    for entrypoint, name in expected_entries.items():
        metadata = registry._parse_skill_file(entrypoint)
        if metadata is None or metadata.name != name:
            raise IntegrityError("Skill name does not match its exact package entrypoint")
    manager = manager_class(config=config, registry=registry, config_mutable=False)
    skills = manager.registry.list_skills()
    inventory = sorted(skill.name for skill in skills)
    visible = sorted(skill.name for skill in manager.get_available_skills("GenSQL", node_class="gen_sql"))
    if inventory != sorted(expected_names) or visible != inventory:
        raise IntegrityError("effective native Skill inventory differs from frozen allowlist")
    if any(Path(skill.location) != root / skill.name for skill in skills):
        raise IntegrityError("native Skill resolves to a different package entrypoint")
    verify_snapshot(skill_root, before)
    return manager


def skill_smoke(skill_root: Path, expected_names: list[str]) -> dict[str, object]:
    before = snapshot(skill_root)
    manager = skill_manager_for(skill_root, expected_names)
    loaded = {}
    for name in expected_names:
        success, _, content = manager.load_skill(name, "GenSQL", node_class="gen_sql")
        expected_content = (skill_root / name / "SKILL.md").read_text(encoding="utf-8")
        if not success or not content or content != expected_content:
            raise IntegrityError("native Skill loading failed")
        loaded[name] = hashlib.sha256(content.encode()).hexdigest()
    verify_snapshot(skill_root, before)
    return {"inventory": sorted(expected_names), "loaded_content_sha256": loaded, "files": before}


def database_smoke(plan: dict[str, Any], approval_sha256: str | None = None) -> dict[str, object]:
    """No environment credentials or plaintext fallback; gate before secret access."""
    if plan.get("evidence_kind") != "native_smoke":
        raise IntegrityError("database smoke needs a task-authorized smoke plan")
    validate_safety(plan, approval_sha256)
    password = read_secret(plan["secret_refs"]["db_password"])
    connector = mysql_connector(plan["public"]["database"], password)
    try:
        result = connector.execute_query("SELECT 1 AS adapter_smoke", result_format="list")
        success = bool(result.success and result.sql_return == [{"adapter_smoke": 1}])
        return {
            "success": success,
            "sql": "SELECT 1 AS adapter_smoke",
            # Never forward arbitrary connector metadata, even on success.
            "row_count": 1 if success else None,
            "result": [{"adapter_smoke": 1}] if success else None,
            "error": None if success else "native_query_failed",
        }
    finally:
        connector.close()


@contextmanager
def _discard_third_party_output() -> Iterator[TextIO]:
    """Process-owned CLI boundary, not safe for concurrent in-process callers.

    Python stream redirection alone misses DBAPI/native writes and inherited
    child descriptors. Keep FD 1/2 discarded through process shutdown as C stdio
    buffers and atexit callbacks can write after the body. The non-inheritable
    duplicate is exclusively for the bounded report and closes before shutdown.
    """
    previous_streams = (sys.stdout, sys.stderr)
    for stream in previous_streams:
        stream.flush()
    with (
        os.fdopen(os.dup(1), "w") as report_channel,
        open(os.devnull, "w") as discarded,
    ):
        os.dup2(discarded.fileno(), 1)
        os.dup2(discarded.fileno(), 2)
        with redirect_stdout(discarded), redirect_stderr(discarded):
            yield report_channel


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skill-root", type=Path, required=True)
    parser.add_argument("--expected-skill", action="append", default=[])
    parser.add_argument("--db", action="store_true", help="Opt in to an independently approved TLS SELECT 1 probe")
    parser.add_argument("--db-plan", type=Path, help="Controlled evaluator's native_smoke plan; never a secret value")
    parser.add_argument("--approval-sha256", help="Approval anchor supplied separately by the controlled runner")
    args = parser.parse_args()
    # No third-party debug logs or connection exceptions may reveal a URL/password.
    logging.disable(logging.CRITICAL)
    report: dict[str, object] = {"evidence_kind": "native_component_smoke", "native_agent_qa_runs": 0}
    failure_exit_code = 1
    with _discard_third_party_output() as report_channel:
        try:
            structlog = importlib.import_module("structlog")
            structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.CRITICAL))
            report["runtime"] = verify_runtime()
            report["skills"] = skill_smoke(args.skill_root, args.expected_skill)
            if args.db and args.db_plan is None:
                raise IntegrityError("database smoke requires an approved TLS plan")  # noqa: TRY301 - bounded CLI report
            database = (
                database_smoke(read_document(args.db_plan)[1], args.approval_sha256)
                if args.db
                else {"status": "not_requested"}
            )
            report["database"] = database
            report["status"] = "passed" if not args.db or database["success"] else "failed"
        except BaseException as error:
            # CLI boundary only: even SystemExit/interrupt messages from an imported
            # component must not become an interpreter traceback containing secrets.
            report.update(status="failed", error_type=type(error).__name__)
            if isinstance(error, KeyboardInterrupt):
                failure_exit_code = 130
        print(json.dumps(report, sort_keys=True, allow_nan=False), file=report_channel, flush=True)
    if report["status"] != "passed":
        raise SystemExit(failure_exit_code)


if __name__ == "__main__":
    main()
