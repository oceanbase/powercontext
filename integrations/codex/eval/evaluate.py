#!/usr/bin/env python3
"""Run a multi-turn Codex workflow and audit the memory it produces."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.request import urlopen

from powercontext.api import ListMemoryChangesRequest, ListMemoryEntriesRequest, SearchMemoryRequest
from powercontext.client import PowerContextClient

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
CODEX_ROOT = REPOSITORY_ROOT / "integrations" / "codex"
SCOPE_ID = "eval:codex-plugin"


def _run(
    command: list[str],
    *,
    env: dict[str, str],
    capture: bool = False,
    cwd: Path = REPOSITORY_ROOT,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(  # noqa: S603 - every command is assembled by this evaluation harness.
        command,
        cwd=cwd,
        env=env,
        check=False,
        text=True,
        capture_output=capture,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or f"command failed with exit code {completed.returncode}")
    return completed


def _wait_until_ready(base_url: str, process: subprocess.Popen[str]) -> None:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("server exited")  # noqa: TRY003
        try:
            with urlopen(f"{base_url}/health/ready", timeout=0.5) as response:  # noqa: S310
                if response.status == 200:
                    return
        except OSError:
            time.sleep(0.1)
    raise TimeoutError("server readiness timeout")  # noqa: TRY003


def _copy_auth(codex_home: Path) -> None:
    source = Path.home() / ".codex" / "auth.json"
    if source.is_file():
        shutil.copy2(source, codex_home / "auth.json")


def _database_audit(database: Path) -> dict[str, object]:
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        source_rows = connection.execute(
            """
            SELECT sequence, adapter_name, source_name, payload
            FROM runtime_sources
            WHERE scope_id = ?
            ORDER BY sequence
            """,
            (SCOPE_ID,),
        ).fetchall()
        cursor_rows = connection.execute(
            """
            SELECT trigger_name, sequence
            FROM runtime_trigger_cursors
            WHERE scope_id = ?
            ORDER BY trigger_name
            """,
            (SCOPE_ID,),
        ).fetchall()
        binding = connection.execute(
            "SELECT memory_artifact_id FROM runtime_memory_bindings WHERE scope_id = ?",
            (SCOPE_ID,),
        ).fetchone()
        if binding is None:
            raise AssertionError("database has no Memory binding")  # noqa: TRY003
        artifact_id = str(binding[0])
        head = connection.execute(
            "SELECT revision FROM artifact_heads WHERE artifact_id = ?",
            (artifact_id,),
        ).fetchone()
        revision_rows = connection.execute(
            """
            SELECT revision, family
            FROM artifact_revisions
            WHERE artifact_id = ?
            ORDER BY revision
            """,
            (artifact_id,),
        ).fetchall()
        entry_rows = connection.execute(
            """
            SELECT entry_id, entry_version_id, kind, text, source_refs, created_in_revision
            FROM memory_entry_versions
            WHERE memory_artifact_id = ?
            ORDER BY created_in_revision, entry_id
            """,
            (artifact_id,),
        ).fetchall()
    finally:
        connection.close()
    return {
        "artifact_head_revision": None if head is None else int(head[0]),
        "artifact_id": artifact_id,
        "entries": [
            {
                "created_in_revision": int(row[5]),
                "entry_id": str(row[0]),
                "entry_version_id": str(row[1]),
                "kind": str(row[2]),
                "source_refs": json.loads(str(row[4])),
                "text": str(row[3]),
            }
            for row in entry_rows
        ],
        "revisions": [{"family": str(row[1]), "revision": int(row[0])} for row in revision_rows],
        "sources": [
            {
                "adapter_name": str(row[1]),
                "payload": json.loads(str(row[3])),
                "sequence": int(row[0]),
                "source_name": str(row[2]),
            }
            for row in source_rows
        ],
        "trigger_cursors": [{"sequence": int(row[1]), "trigger_name": str(row[0])} for row in cursor_rows],
    }


def evaluate(*, skip_agent: bool) -> dict[str, object]:  # noqa: C901 - linear black-box audit keeps evidence together.
    port = 8000
    base_url = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryDirectory(prefix="powercontext-codex-eval-") as temp:
        temp_root = Path(temp)
        database = temp_root / "powercontext.db"
        codex_home = temp_root / "codex-home"
        codex_home.mkdir()
        agent_workspace = temp_root / "workspace"
        agent_workspace.mkdir()
        shutil.copy2(REPOSITORY_ROOT / "pyproject.toml", agent_workspace / "pyproject.toml")
        _copy_auth(codex_home)
        env = os.environ.copy()
        env.update({
            "CODEX_HOME": str(codex_home),
            "POWERCONTEXT_HTTP_URL": base_url,
            "POWERCONTEXT_SCOPE_ID": SCOPE_ID,
            "POWERCONTEXT_FLUSH_ON_CAPTURE": "true",
            "POWERCONTEXT_HOOK_PAYLOAD_LOG": str(temp_root / "hook-payload.json"),
        })
        server = subprocess.Popen(  # noqa: S603 - executable and arguments are integration-owned.
            [
                sys.executable,
                str(CODEX_ROOT / "eval" / "server.py"),
                "--database",
                str(database),
                "--port",
                str(port),
            ],
            cwd=REPOSITORY_ROOT,
            env=env,
            text=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        try:
            _wait_until_ready(base_url, server)
            _run(["codex", "plugin", "marketplace", "add", str(CODEX_ROOT)], env=env)
            _run(["codex", "plugin", "add", "powercontext@powercontext-local"], env=env)
            installed = _run(["codex", "plugin", "list"], env=env, capture=True).stdout
            if "powercontext" not in installed:
                raise AssertionError("plugin missing")  # noqa: TRY003
            with PowerContextClient(base_url) as client:
                capabilities = client.get_capabilities()
            if not capabilities.memory_extraction:
                raise AssertionError("Server memory extraction is disabled")  # noqa: TRY003

            if skip_agent:
                return {
                    "agent_exercised": False,
                    "codex_version": _run(["codex", "--version"], env=env, capture=True).stdout.strip(),
                    "plugin_install": "passed",
                }

            producer = _run(
                [
                    "codex",
                    "exec",
                    "--ephemeral",
                    "--json",
                    "--skip-git-repo-check",
                    "--sandbox",
                    "read-only",
                    "--dangerously-bypass-hook-trust",
                    "Project fact for this task: pyproject.toml supports Python >=3.11,<4.0. "
                    "Explain that compatibility range in one sentence without editing files.",
                ],
                env=env,
                capture=True,
                cwd=agent_workspace,
            )
            with PowerContextClient(base_url) as client:
                entries = client.list_memory_entries(ListMemoryEntriesRequest(scope_id=SCOPE_ID))
                hits = client.search_memory(
                    SearchMemoryRequest(scope_id=SCOPE_ID, query="supported Python version range")
                )
            produced_texts = [entry.text for entry in entries.entries]
            if not produced_texts:
                payload_log = temp_root / "hook-payload.json"
                diagnostic = payload_log.read_text() if payload_log.is_file() else "hook did not write a payload"
                raise AssertionError(  # noqa: TRY003
                    f"Codex prompt produced no memory.\nHook: {diagnostic}\nCodex: {producer.stdout[-8_000:]}"
                )
            if not any("3.11" in text and "4.0" in text for text in produced_texts):
                raise AssertionError("version-range memory missing")  # noqa: TRY003
            if not hits.hits:
                raise AssertionError("produced memory is not searchable")  # noqa: TRY003
            if not entries.entries[0].source_refs:
                raise AssertionError("derived memory has no Source provenance")  # noqa: TRY003
            if not entries.entries[0].source_refs[0].source_id.startswith("codex-user-prompt:"):
                raise AssertionError("derived memory has the wrong Source provenance")  # noqa: TRY003

            consumer = _run(
                [
                    "codex",
                    "exec",
                    "--ephemeral",
                    "--json",
                    "--sandbox",
                    "read-only",
                    "--skip-git-repo-check",
                    "--dangerously-bypass-hook-trust",
                    "Without opening pyproject.toml, use the installed PowerContext integration "
                    "to answer which Python version range this project supports. Reply with the "
                    "range and briefly state that it came from project memory.",
                ],
                env=env,
                capture=True,
                cwd=agent_workspace,
            )
            if "3.11" not in consumer.stdout or "4.0" not in consumer.stdout:
                raise AssertionError("consumer did not reuse memory")  # noqa: TRY003
            with PowerContextClient(base_url) as client:
                final_entries = client.list_memory_entries(ListMemoryEntriesRequest(scope_id=SCOPE_ID))
                final_changes = client.list_memory_changes(
                    ListMemoryChangesRequest(scope_id=SCOPE_ID, since_revision=0)
                )
            database_audit = _database_audit(database)
            audited_sources = database_audit["sources"]
            audited_entries = database_audit["entries"]
            if not isinstance(audited_sources, list) or not isinstance(audited_entries, list):
                raise TypeError
            source_count = len(audited_sources)
            if source_count != 2:
                raise AssertionError(f"expected two captured Sources, found {source_count}")  # noqa: TRY003
            if database_audit["trigger_cursors"] != [{"sequence": 2, "trigger_name": "memory-source-window"}]:
                raise AssertionError("Trigger cursor did not reach the Source high watermark")  # noqa: TRY003
            if database_audit["artifact_head_revision"] != 2:
                raise AssertionError("database Artifact head is not revision 2")  # noqa: TRY003
            if len(final_entries.entries) != len(audited_entries):
                raise AssertionError("SDK and database entry counts differ")  # noqa: TRY003

            return {
                "agent_exercised": True,
                "codex_version": _run(["codex", "--version"], env=env, capture=True).stdout.strip(),
                "consumer_reused_memory": True,
                "database_audit": database_audit,
                "http_sdk_audit": "passed",
                "mcp_endpoint": f"{base_url}/mcp",
                "plugin_install": "passed",
                "produced_entries": [
                    {
                        "citation": entry.citation.model_dump(mode="json"),
                        "kind": entry.kind,
                        "memory_revision": entry.citation.memory_ref.revision,
                        "text": entry.text,
                    }
                    for entry in final_entries.entries
                ],
                "revision_count": len(final_changes.revisions),
            }
        finally:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-agent", action="store_true", help="Validate installation without a model turn.")
    arguments = parser.parse_args()
    print(json.dumps(evaluate(skip_agent=arguments.skip_agent), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
