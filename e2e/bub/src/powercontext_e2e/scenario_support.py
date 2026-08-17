"""Container-side helpers for segmented ACP scenario runs."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import tarfile
from pathlib import Path
from typing import Any


def snapshot_workspace(workspace: Path, archive: Path) -> None:
    workspace = _validated_workspace(workspace)
    archive = archive.resolve()
    archive.parent.mkdir(parents=True, exist_ok=True)
    temporary_archive = archive.with_name(f".{archive.name}.tmp")
    with tarfile.open(temporary_archive, "w") as destination:
        destination.add(workspace, arcname=".", recursive=True)
    os.replace(temporary_archive, archive)


def restore_workspace(workspace: Path, archive: Path) -> None:
    workspace = _validated_workspace(workspace)
    archive = archive.resolve()
    if not archive.is_file():
        raise FileNotFoundError(f"Workspace snapshot does not exist: {archive}")  # noqa: TRY003

    for child in workspace.iterdir():
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()
    with tarfile.open(archive, "r") as source:
        source.extractall(workspace, filter="data")


def aggregate_segments(logs_dir: Path, instruction: str) -> None:
    segment_directories = sorted(path for path in (logs_dir / "segments").iterdir() if path.is_dir())
    if not segment_directories:
        raise ValueError(f"No ACP segment logs found below {logs_dir}")  # noqa: TRY003

    summaries = [_read_summary(path / "acp-summary.json") for path in segment_directories]
    final_summary = dict(summaries[-1])
    prompt_response = dict(final_summary.get("prompt_response") or {})
    aggregate_usage = _sum_mappings(
        summary.get("prompt_response", {}).get("usage", {})
        for summary in summaries
        if isinstance(summary.get("prompt_response"), dict)
    )
    if aggregate_usage:
        prompt_response["usage"] = aggregate_usage
        final_summary["prompt_response"] = prompt_response

    final_summary.update({
        "instruction": instruction,
        "scenario_session_count": len(summaries),
        "segments": [
            {
                "index": index,
                "instruction": summary.get("instruction"),
                "session": summary.get("session"),
                "prompt_response": summary.get("prompt_response"),
                "error": summary.get("error"),
            }
            for index, summary in enumerate(summaries)
        ],
    })
    (logs_dir / "acp-summary.json").write_text(
        json.dumps(final_summary, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    with (logs_dir / "acp-events.jsonl").open("w", encoding="utf-8") as destination:
        for segment_directory in segment_directories:
            events_path = segment_directory / "acp-events.jsonl"
            if not events_path.is_file():
                continue
            content = events_path.read_text(encoding="utf-8")
            destination.write(content)
            if content and not content.endswith("\n"):
                destination.write("\n")


def _validated_workspace(workspace: Path) -> Path:
    resolved = workspace.resolve()
    if resolved == Path(resolved.anchor):
        raise ValueError("Refusing to snapshot or restore a filesystem root")  # noqa: TRY003
    if not resolved.is_dir():
        raise NotADirectoryError(f"Workspace is not a directory: {resolved}")  # noqa: TRY003
    return resolved


def _read_summary(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Unable to read ACP segment summary {path}: {exc}") from exc  # noqa: TRY003
    if not isinstance(payload, dict):
        raise TypeError(f"ACP segment summary is not an object: {path}")  # noqa: TRY003
    return payload


def _sum_mappings(values: Any) -> dict[str, Any]:
    total: dict[str, Any] = {}
    for value in values:
        if not isinstance(value, dict):
            continue
        for key, item in value.items():
            current = total.get(key)
            if isinstance(item, bool):
                continue
            if isinstance(item, int | float):
                total[key] = (
                    current if isinstance(current, int | float) and not isinstance(current, bool) else 0
                ) + item
            elif isinstance(item, dict):
                total[key] = _sum_mappings((current if isinstance(current, dict) else {}, item))
    return total


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Support segmented PowerContext Bub scenarios")
    subparsers = parser.add_subparsers(dest="command", required=True)

    for command in ("snapshot", "restore"):
        command_parser = subparsers.add_parser(command)
        command_parser.add_argument("--workspace", type=Path, required=True)
        command_parser.add_argument("--archive", type=Path, required=True)

    aggregate_parser = subparsers.add_parser("aggregate")
    aggregate_parser.add_argument("--logs-dir", type=Path, required=True)
    aggregate_parser.add_argument("--instruction", required=True)
    args = parser.parse_args(argv)

    if args.command == "snapshot":
        snapshot_workspace(args.workspace, args.archive)
    elif args.command == "restore":
        restore_workspace(args.workspace, args.archive)
    else:
        aggregate_segments(args.logs_dir, args.instruction)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
