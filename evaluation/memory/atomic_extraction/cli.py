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

"""CLI for validation, opt-in generation, replay, and safe report pinning."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from pathlib import Path
from typing import Any

from .manifest import DEFAULT_MANIFEST, load_manifest
from .models import LiveRunManifest
from .report import build_report, load_observations, write_report
from .runner import prompt_run_identities, run_live_evaluation

_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    arguments = parser.parse_args(argv)
    try:
        return int(arguments.handler(arguments))
    except (OSError, TypeError, ValueError) as error:
        parser.exit(2, f"error: {error}\n")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m evaluation.memory.atomic_extraction",
        description="Evaluate subject-bound Atomic Memory extraction with synthetic bilingual cases.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    validate = commands.add_parser("validate", help="validate and identify the committed case manifest")
    validate.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    validate.set_defaults(handler=_validate)

    run = commands.add_parser("run", help="run the opt-in cases against a configured real generation provider")
    run.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    run.add_argument("--env-file", type=Path, required=True)
    run.add_argument("--output-directory", type=Path, required=True)
    run.add_argument("--run-id", type=_run_id, required=True)
    run.set_defaults(handler=_run)

    replay = commands.add_parser("replay", help="rebuild a content-free report without provider calls")
    replay.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    replay.add_argument("--run-directory", type=Path, required=True)
    replay.set_defaults(handler=_replay)

    pin = commands.add_parser("pin", help="copy only the safe manifest/report files into a review directory")
    pin.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    pin.add_argument("--run-directory", type=Path, required=True)
    pin.add_argument("--destination", type=Path, required=True)
    pin.set_defaults(handler=_pin)
    return parser


def _validate(arguments: argparse.Namespace) -> int:
    loaded = load_manifest(arguments.manifest)
    dispositions: dict[str, int] = {}
    for case in loaded.manifest.cases:
        disposition = case.expected.disposition
        dispositions[disposition] = dispositions.get(disposition, 0) + 1
    _print({
        "schema_version": loaded.manifest.schema_version,
        "dataset_version": loaded.manifest.dataset_version,
        "manifest_sha256": loaded.sha256,
        "case_count": len(loaded.manifest.cases),
        "dispositions": dispositions,
    })
    return 0


def _run(arguments: argparse.Namespace) -> int:
    loaded = load_manifest(arguments.manifest)
    report = asyncio.run(
        run_live_evaluation(
            loaded=loaded,
            env_file=arguments.env_file,
            output_directory=arguments.output_directory,
            run_id=arguments.run_id,
        )
    )
    _print({"status": report["status"], "output_directory": str(arguments.output_directory.resolve())})
    return int(report["status"] != "passed")


def _replay(arguments: argparse.Namespace) -> int:
    loaded = load_manifest(arguments.manifest)
    directory = arguments.run_directory.resolve()
    run_manifest = _load_json(directory / "run-manifest.json")
    observations = load_observations(directory / "observations.private.jsonl")
    report = build_report(loaded, observations, run_manifest)
    write_report(directory, report)
    _print({"status": report["status"], "run_directory": str(directory)})
    return int(report["status"] != "passed")


def _pin(arguments: argparse.Namespace) -> int:
    source = arguments.run_directory.resolve()
    destination = arguments.destination.resolve()
    loaded = load_manifest(arguments.manifest)
    manifest = LiveRunManifest.model_validate(_load_json(source / "run-manifest.json"))
    if manifest.prompts != prompt_run_identities():
        raise ValueError("run manifest Prompt identities do not match the current built-ins")  # noqa: TRY003
    observations = load_observations(source / "observations.private.jsonl")
    public_manifest = manifest.model_dump(mode="json")
    report = build_report(loaded, observations, public_manifest)
    if report["status"] != "passed":
        raise ValueError("only a passing complete live run can be pinned")  # noqa: TRY003
    try:
        destination.mkdir(parents=True, exist_ok=False)
    except FileExistsError as error:
        raise ValueError(f"refusing to overwrite pinned report directory: {destination}") from error  # noqa: TRY003
    (destination / "run-manifest.json").write_text(
        json.dumps(public_manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    write_report(destination, report)
    _print({"pinned": str(destination), "run_id": manifest.run_id})
    return 0


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot load JSON artifact: {path}") from error  # noqa: TRY003
    if not isinstance(value, dict):
        raise TypeError(f"JSON artifact must contain an object: {path}")  # noqa: TRY003
    return value


def _run_id(value: str) -> str:
    if _RUN_ID.fullmatch(value) is None:
        raise argparse.ArgumentTypeError(  # noqa: TRY003
            "run ID must contain only letters, digits, dot, underscore, and hyphen"
        )
    return value


def _print(value: dict[str, Any]) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


__all__ = ["main"]
