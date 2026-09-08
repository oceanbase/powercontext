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

"""Execute every lesson against fresh OceanBase databases and remove only those created here."""

# Keep ownership, execution, and cleanup in one auditable sequence.
# ruff: noqa: C901, TRY003, TRY301
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx
from dotenv import dotenv_values
from sqlalchemy import text
from sqlalchemy.dialects import registry
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

DIRECTORY = Path(__file__).resolve().parent


async def run(args: argparse.Namespace) -> int:
    environment = {
        **{key: value for key, value in dotenv_values(args.env_file).items() if value is not None},
        **os.environ,
    }
    admin_url = environment.get("POWERCONTEXT_NOTEBOOK_OCEANBASE_ADMIN_URL") or environment.get(
        "POWERCONTEXT_SERVER_DATABASE_URL"
    )
    if not admin_url or make_url(admin_url).drivername != "mysql+aoceanbase":
        raise ValueError("Configure an OceanBase mysql+aoceanbase admin URL in the selected env file")
    environment["POWERCONTEXT_NOTEBOOK_ENV_FILE"] = str(args.env_file.resolve())
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=False)
    registry.register("mysql.aoceanbase", "pyobvector", "AsyncOceanBaseDialect")
    engine = create_async_engine(make_url(admin_url).set(database=None), isolation_level="AUTOCOMMIT")
    records = []
    prefix = "pc_nb_" + uuid4().hex[:12]
    paths = sorted(DIRECTORY.glob("[0-9A][0-9]_*.ipynb"))
    selected = [p.name[:2] for p in paths if not args.only or p.name[:2] in args.only]
    if not selected or (args.only and set(args.only) - set(selected)):
        raise ValueError("--only must contain existing two-digit lesson numbers")
    cases = [(number, False) for number in selected]
    if "11" in selected:
        cases.append(("11", True))
    try:
        async with engine.connect() as connection:
            version = str((await connection.execute(text("SELECT VERSION()"))).scalar())
        for number, bearer in cases:
            label = number + ("-bearer" if bearer else "")
            database_name = prefix + "_" + label.replace("-", "_").lower()
            if not re.fullmatch(r"pc_nb_[a-f0-9]{12}_(?:[0-9]{2}|a1)(?:_bearer)?", database_name):
                raise ValueError("Refuse an unexpected temporary database name")
            record = {
                "lesson": number,
                "variant": "bearer" if bearer else "default",
                "database": database_name,
                "created": False,
                "deleted": False,
            }
            records.append(record)
            server = None
            server_log = None
            private_config = output / (label + ".env")
            started = time.monotonic()
            try:
                async with engine.connect() as connection:
                    # No IF NOT EXISTS: ownership is established only after this CREATE succeeds.
                    await connection.execute(text(f"CREATE DATABASE `{database_name}`"))
                record["created"] = True
                (output / "verification.json").write_text(
                    json.dumps({"backend": "oceanbase", "version": version, "records": records}, indent=2)
                )
                lesson_env = dict(environment)
                database_url = make_url(admin_url).set(database=database_name).render_as_string(hide_password=False)
                lesson_env["POWERCONTEXT_NOTEBOOK_OCEANBASE_URL"] = database_url
                lesson_env["POWERCONTEXT_NOTEBOOK_DATA_DIR"] = str(output / "data")
                if number == "11":
                    with socket.socket() as listener:
                        listener.bind(("127.0.0.1", 0))
                        port = listener.getsockname()[1]
                    server_env = {
                        key: value for key, value in lesson_env.items() if not key.startswith("POWERCONTEXT_SERVER_")
                    }
                    settings = {
                        "POWERCONTEXT_SERVER_DATABASE_KIND": "oceanbase",
                        "POWERCONTEXT_SERVER_DATABASE_URL": database_url,
                        "POWERCONTEXT_SERVER_ACCESS_MODE": "disabled",
                        "POWERCONTEXT_SERVER_AUTH_ENABLED": "true" if bearer else "false",
                        "POWERCONTEXT_SERVER_MCP_ENABLED": "false",
                        "POWERCONTEXT_SERVER_DASHBOARD_ENABLED": "false",
                        "POWERCONTEXT_SERVER_METRICS_ENABLED": "false",
                        "POWERCONTEXT_SERVER_EXTERNAL_SKILLS": "{}",
                    }
                    token = secrets.token_urlsafe(32) if bearer else ""
                    if bearer:
                        settings["POWERCONTEXT_SERVER_AUTH_TOKEN"] = token
                    with private_config.open("x", encoding="utf-8") as stream:
                        private_config.chmod(0o600)
                        for key, value in settings.items():
                            stream.write(key + "=" + json.dumps(value) + "\n")
                    server_env.update(settings)
                    server_log = (output / (label + "-server.log")).open("w")
                    server = await asyncio.to_thread(
                        subprocess.Popen,
                        [
                            sys.executable,
                            "-c",
                            "from powercontext.cli.app import main; main()",
                            "server",
                            "run",
                            "--host",
                            "127.0.0.1",
                            "--port",
                            str(port),
                            "--env-file",
                            str(private_config),
                        ],
                        cwd=DIRECTORY,
                        env=server_env,
                        stdout=server_log,
                        stderr=subprocess.STDOUT,
                    )
                    lesson_env["POWERCONTEXT_CLIENT_SERVER_URL"] = f"http://127.0.0.1:{port}"
                    lesson_env["POWERCONTEXT_CLIENT_API_TOKEN"] = token
                    deadline = time.monotonic() + 90
                    async with httpx.AsyncClient(timeout=2) as http:
                        while True:
                            try:
                                response = await http.get(
                                    f"http://127.0.0.1:{port}/health/ready",
                                    headers={"Authorization": "Bearer " + token} if token else {},
                                )
                                if response.status_code == 200:
                                    break
                            except httpx.HTTPError:
                                pass
                            if server.poll() is not None or time.monotonic() > deadline:
                                raise RuntimeError("Independent HTTP Server did not become ready")
                            await asyncio.sleep(1)
                command = [
                    sys.executable,
                    str(DIRECTORY / "run.py"),
                    "--only",
                    number,
                    "--with-models",
                    "--with-http",
                    "--with-browser",
                    "--timeout",
                    str(args.timeout),
                    "--output-dir",
                    str(output / label),
                ]
                print("OCEANBASE RUN " + label, flush=True)
                completed = await asyncio.to_thread(
                    subprocess.run, command, env=lesson_env, cwd=DIRECTORY.parent.parent, check=False
                )
                record["returncode"] = completed.returncode
                summary = output / label / "summary.json"
                record["execution"] = json.loads(summary.read_text()) if summary.is_file() else []
                async with engine.connect() as connection:
                    count = (
                        await connection.execute(
                            text("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = :database"),
                            {"database": database_name},
                        )
                    ).scalar()
                record["tables_before_cleanup"] = count
            except Exception as error:
                record["error_type"] = type(error).__name__
                record["returncode"] = 1
            finally:
                if server is not None:
                    server.terminate()
                    try:
                        await asyncio.to_thread(server.wait, timeout=20)
                    except subprocess.TimeoutExpired:
                        server.kill()
                        await asyncio.to_thread(server.wait)
                if server_log is not None:
                    server_log.close()
                private_config.unlink(missing_ok=True)
                if record["created"]:
                    try:
                        async with engine.connect() as connection:
                            await connection.execute(text(f"DROP DATABASE `{database_name}`"))
                            remaining = (
                                await connection.execute(
                                    text(
                                        "SELECT COUNT(*) FROM information_schema.schemata WHERE schema_name = :database"
                                    ),
                                    {"database": database_name},
                                )
                            ).scalar()
                        record["deleted"] = remaining == 0
                    except Exception as error:
                        record["cleanup_error_type"] = type(error).__name__
                record["seconds"] = round(time.monotonic() - started, 2)
                (output / "verification.json").write_text(
                    json.dumps(
                        {"backend": "oceanbase", "version": version, "records": records}, ensure_ascii=False, indent=2
                    )
                    + "\n"
                )
            if record["created"] and not record["deleted"]:
                raise RuntimeError("Temporary database cleanup was not confirmed; stop before creating another")
            print(f"OCEANBASE END {label}: returncode={record['returncode']}, cleanup={record['deleted']}", flush=True)
    finally:
        await engine.dispose()
    return int(any(record.get("returncode") != 0 or not record["deleted"] for record in records))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument(
        "--create-temporary-databases",
        action="store_true",
        required=True,
        help="Explicitly authorize creation and removal of this run's new databases.",
    )
    parser.add_argument("--only", nargs="+")
    parser.add_argument("--timeout", type=int, default=360)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DIRECTORY / ".powercontext" / ("oceanbase-full-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")),
    )
    args = parser.parse_args()
    try:
        return asyncio.run(run(args))
    except Exception as error:
        print("OceanBase verification stopped: " + type(error).__name__, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
