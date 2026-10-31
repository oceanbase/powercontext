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

"""Explicit freeze, private replay, human review, and aggregate report commands."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any, cast

from .host import repository_root
from .models import Manifest, RunConfig, read_json, write_json


def parser() -> argparse.ArgumentParser:
    app = argparse.ArgumentParser(description=__doc__)
    commands = app.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate", help="Validate frozen ownership spans without model credentials")
    validate.add_argument("--manifest", type=Path, required=True)
    frozen = commands.add_parser(
        "freeze", help="Pin cases, code, parser, prompt, provider and budgets before generation"
    )
    frozen.add_argument("--manifest", type=Path, required=True)
    frozen.add_argument("--config", type=Path, required=True)
    frozen.add_argument("--lock", type=Path, required=True)
    run = commands.add_parser("run", help="Replay through a real local Server application and persistent SQLite")
    run.add_argument("--manifest", type=Path, required=True)
    run.add_argument("--lock", type=Path, required=True)
    run.add_argument("--private-dir", type=Path, required=True)
    run.add_argument("--mode", choices=("offline", "live"), required=True)
    run.add_argument("--generation-env-file", type=Path)
    report = commands.add_parser("report", help="Generate an aggregate from a saved private run, optionally reviewed")
    report.add_argument("--private-dir", type=Path, required=True)
    report.add_argument("--output", type=Path, required=True)
    report.add_argument("--review", type=Path)
    config = commands.add_parser(
        "config", help="Export nonsecret model settings from an explicit generation environment"
    )
    config.add_argument("--generation-env-file", type=Path, required=True)
    config.add_argument("--output", type=Path, required=True)
    return app


def main() -> None:
    args = parser().parse_args()
    try:
        if args.command == "validate":
            manifest = Manifest.model_validate(read_json(args.manifest))
            print(f"Validated {len(manifest.cases)} cases; no generation performed.")
        elif args.command == "freeze":
            from .replay import freeze

            if args.lock.exists():
                raise ValueError("experiment lock already exists")
            value = freeze(
                repository_root(),
                Manifest.model_validate(read_json(args.manifest)),
                RunConfig.model_validate(read_json(args.config)),
            )
            write_json(args.lock, value)
            print("Frozen experiment written; no generation performed.")
        elif args.command == "run":
            from .replay import run_experiment
            from .report import review_template

            lock = read_json(args.lock)
            if not isinstance(lock, dict):
                raise ValueError("invalid experiment lock")
            result = asyncio.run(
                run_experiment(
                    repository_root(),
                    Manifest.model_validate(read_json(args.manifest)),
                    cast(dict[str, Any], lock),
                    args.private_dir,
                    mode=args.mode,
                    env_file=args.generation_env_file,
                )
            )
            write_json(args.private_dir / "review-template.json", review_template(result))
            print(f"Completed {len(result['rows'])} case/arm/repetition rows ({args.mode}); review template written.")
        elif args.command == "report":
            from .report import report_from_run

            result = report_from_run(args.private_dir, args.output, args.review)
            print(f"Decision: {result['decision']}")
        elif args.command == "config":
            from powercontext.server.configuration import server_settings_context

            if args.output.exists():
                raise ValueError("configuration already exists")
            with server_settings_context(env_file=args.generation_env_file) as settings:
                config = RunConfig(
                    schema_version="capture-ownership.config.v1",
                    model=settings.inference.generation_model,
                    base_url=None
                    if settings.inference.generation_base_url is None
                    else str(settings.inference.generation_base_url),
                    model_settings=dict(settings.inference.generation_model_settings),
                )
            write_json(args.output, config.model_dump(mode="json"))
            print("Nonsecret configuration written; no provider request performed.")
    except KeyboardInterrupt:
        print("Replay cancelled; no completed report was published.", file=sys.stderr)
        raise SystemExit(130) from None
    except Exception as error:  # noqa: BLE001 -- redact provider errors at the CLI boundary.
        # Provider/validation exceptions can include raw evidence and credentials.
        print(
            f"Capture ownership command failed ({type(error).__name__}); no result should be inferred.", file=sys.stderr
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
