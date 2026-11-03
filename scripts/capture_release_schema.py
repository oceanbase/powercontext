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

"""Capture a real installed PowerContext 1.4.0 Server baseline without migrating it."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from contextlib import contextmanager
from datetime import UTC, datetime
from inspect import unwrap
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
VERSION = "1.4.0"
PROFILES = ("full-server-no-vector", "full-server-code", "full-server-vector", "full-server-vector-code")


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def source_wheel(directory, metadata_path=None, wheel_path=None):
    url = f"https://pypi.org/pypi/powercontext/{VERSION}/json"
    if metadata_path is None:
        with urlopen(url, timeout=60) as response:  # noqa: S310 -- fixed official PyPI HTTPS URL
            metadata = json.load(response)
    else:
        metadata = json.loads(metadata_path.read_text())
    candidates = [
        entry for entry in metadata["urls"] if entry["filename"] == f"powercontext-{VERSION}-py3-none-any.whl"
    ]
    if metadata["info"]["version"] != VERSION or len(candidates) != 1:
        raise ValueError("Expected the official PowerContext 1.4.0 wheel")  # noqa: TRY003
    entry = candidates[0]
    if urlsplit(entry["url"]).scheme != "https" or urlsplit(entry["url"]).hostname != "files.pythonhosted.org":
        raise ValueError("Expected a wheel hosted by PyPI")  # noqa: TRY003
    destination = directory / entry["filename"]
    if wheel_path is None:
        with urlopen(entry["url"], timeout=120) as response:  # noqa: S310 -- validated official PyPI HTTPS host
            destination.write_bytes(response.read())
    else:
        destination.write_bytes(wheel_path.read_bytes())
    checksum = hashlib.sha256(destination.read_bytes()).hexdigest()
    if checksum != entry["digests"]["sha256"]:
        raise ValueError("Source wheel does not match the PyPI SHA256")  # noqa: TRY003
    return destination, {
        "name": "powercontext",
        "version": VERSION,
        "filename": entry["filename"],
        "sha256": checksum,
        "url": entry["url"],
        "uploaded_at": entry["upload_time_iso_8601"],
        "metadata_url": url,
        "metadata_origin": "live-pypi" if metadata_path is None else "provided-pypi-json",
    }


def canonical_schema(schema, backend):
    # Parse already-captured text with the reviewed checkout parser. Never use
    # the released wheel's older parser or initialize a current database profile.
    sys.path.insert(0, str(ROOT / "src"))
    from powercontext.builtin.persistence.migrations import mysql_schema
    from powercontext.builtin.persistence.migrations.models import digest

    parser = Path(mysql_schema.__file__).resolve()
    if not parser.is_relative_to(ROOT / "src"):
        raise RuntimeError("Schema canonicalization must use the checked-out parser")  # noqa: TRY003
    if backend == "sqlite":
        inventory = [row for row in schema["schema_rows"] if not row[1].startswith("sqlite_")]
    else:
        tables = {}
        for name, definition in schema["tables"].items():
            reflected, table = mysql_schema._parse_table(definition, database=schema["database"])
            if reflected != name:
                raise ValueError("Reflected table identity differs from raw evidence")  # noqa: TRY003
            tables[name] = table
        inventory = {
            "tables": tables,
            "views": sorted(schema["views"]),
            "triggers": sorted(schema["trigger_rows"], key=lambda row: row["Trigger"]),
        }
    return {
        "inventory": inventory,
        "sha256": digest(inventory),
        "canonicalizer": "exact sqlite_schema rows" if backend == "sqlite" else "current mysql_schema._parse_table",
        "parser_sha256": None if backend == "sqlite" else hashlib.sha256(parser.read_bytes()).hexdigest(),
    }


def capture(args):
    # This module contains only subprocess/HTTP fixture helpers. Source commands
    # use -I and a separate installed-release environment, never checkout runtime.
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / "scripts"))
    from upgrade_release_gate import case_workspace

    from tests.release_upgrades.test_full_server import (
        command,
        environment_for,
        historical_code,
        model_gateway,
        post,
        server,
        snapshot,
    )

    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    manifest = {
        "format": "installed-server-schema-capture-v1",
        "status": "incomplete",
        "captured_at": datetime.now(UTC).isoformat(),
        "checkout_commit": os.environ.get("GITHUB_SHA"),
        "workflow_run": os.environ.get("GITHUB_RUN_ID"),
        "backend": args.backend,
        "configuration": args.configuration,
        "source_version": VERSION,
        "capture_kind": "real installed Server startup and HTTP writes, followed by read-only reflection",
    }
    save(output / "manifest.json", manifest)
    # Keep database sockets short and database files outside uploaded evidence.
    with case_workspace(args.backend, output) as work:
        environment = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("POWERCONTEXT_", "PYTHON", "UV_", "PIP_")) and key != "VIRTUAL_ENV"
        }
        wheel, provenance = source_wheel(work, args.pypi_metadata, args.wheel)
        manifest["source_wheel"] = provenance
        save(output / "manifest.json", manifest)
        command(
            ["uv", "venv", "--python", sys.executable, str(work / "source")], environment, work, output / "venv.log"
        )
        python = work / "source" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        extras = "server,cli,code" + (",seekdb" if args.backend == "seekdb" else "")
        command(
            [
                "uv",
                "pip",
                "install",
                "--python",
                str(python),
                "--no-config",
                "--default-index",
                "https://pypi.org/simple",
                f"{wheel}[{extras}]",
            ],
            environment,
            work,
            output / "install.log",
        )
        installed = json.loads(
            command(
                [
                    str(python),
                    "-I",
                    "-c",
                    "import json,platform,powercontext; from importlib.metadata import distributions; "
                    "print(json.dumps({'python':platform.python_version(),'module_file':powercontext.__file__,"
                    "'packages':{d.metadata['Name']:d.version for d in distributions()}}))",
                ],
                environment,
                work,
                output / "installed.log",
            )
        )
        if installed["packages"].get("powercontext") != VERSION or not Path(installed["module_file"]).is_relative_to(
            work
        ):
            raise RuntimeError("The source interpreter did not import the isolated 1.4.0 installation")  # noqa: TRY003
        manifest["installed"] = installed
        manifest["profile"] = {
            "code_enabled": args.configuration.endswith("-code"),
            "vector_dimension": {"full-server-vector": 3, "full-server-vector-code": 4}.get(args.configuration, 0),
            "embedding_normalization": "unit"
            if "-vector" in args.configuration and not args.configuration.endswith("no-vector")
            else None,
        }
        save(output / "manifest.json", manifest)
        gateway_context = contextmanager(unwrap(model_gateway))
        with gateway_context() as gateway:
            case = {
                "source": python,
                "source_version": VERSION,
                "backend": args.backend,
                "configuration": args.configuration,
                "work": work,
                "report": output,
                "model": gateway,
                **manifest["profile"],
            }
            env = environment_for(case)
            manifest["embedding_profile"] = {
                key.removeprefix("POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_").lower(): value
                for key, value in env.items()
                if key.startswith("POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_") and not key.endswith("BASE_URL")
            }
            save(output / "manifest.json", manifest)
            if args.backend == "oceanbase" and snapshot(case, "source", "empty")["tables"]:
                raise RuntimeError("Refusing to initialize a nonempty OceanBase database")  # noqa: TRY003
            with server(python, env, work, output / "source-server.log") as client:
                readiness = client.get("/health/ready")
                readiness.raise_for_status()
                save(output / "source-readiness.json", readiness.json())
                scope = post(
                    client,
                    "/v1/scopes",
                    {
                        "title": "Release schema capture",
                        "summary": "Disposable fixture",
                        "idempotency_key": "capture-scope",
                    },
                )["scope_id"]
                post(
                    client,
                    "/v1/memory/remember",
                    {"scope_id": scope, "kind": "fact", "text": "Schema capture evidence."},
                )
                if case["vector_dimension"]:
                    post(
                        client,
                        f"/v1/scopes/{scope}/artifacts",
                        {
                            "family": "topic-memory",
                            "content": {
                                "title": "Capture",
                                "summary": "Vector fixture",
                                "detail": "# Capture\nProfile evidence.",
                            },
                        },
                    )
            if case["code_enabled"]:
                manifest["code"] = historical_code(case, scope)
            schema = snapshot(case, "source", "source")
            canonical = canonical_schema(schema, args.backend)
            save(output / "canonical-schema.json", canonical)
            if (
                not {"pc_scopes", "pc_artifacts", "pc_candidate_heads", "pc_candidate_versions"}
                <= schema["tables"].keys()
            ):
                raise RuntimeError("The released Server did not initialize the expected 1.4.0 schema")  # noqa: TRY003
            expected_vectors = {
                "pc_atomic_memory_current",
                "pc_topic_memory_vector_topics",
                "pc_topic_memory_vector_chunks",
            }
            if case["vector_dimension"] and (
                set(schema["vectors"]) != expected_vectors
                or not all(item["rows"] for item in schema["vectors"].values())
            ):
                raise RuntimeError("The vector profile did not persist its fixture embeddings")  # noqa: TRY003
            manifest.update({
                "status": "captured",
                "schema_file": "source-schema.json",
                "canonical_file": "canonical-schema.json",
                "canonical_sha256": canonical["sha256"],
                "schema_sha256": hashlib.sha256((output / "source-schema.json").read_bytes()).hexdigest(),
                "engine_version": schema["engine_version"],
                "driver_version": schema["driver_version"],
                "embedding_requests": gateway["embeddings"],
                "server_stopped_before_reflection": True,
                "target_migration_executed": False,
            })
            save(output / "manifest.json", manifest)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("backend", choices=("sqlite", "seekdb", "oceanbase"))
    parser.add_argument("--configuration", choices=PROFILES, default=PROFILES[0])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--wheel", type=Path, help="Optional cached wheel, verified against PyPI metadata")
    parser.add_argument(
        "--pypi-metadata", type=Path, help="Optional saved official release JSON for offline reproduction"
    )
    capture(parser.parse_args())


if __name__ == "__main__":
    main()
