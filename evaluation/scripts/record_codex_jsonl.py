#!/usr/bin/env python3
"""Timestamp Codex JSONL events without changing its stdout byte stream."""

from __future__ import annotations

import argparse
import json
import os
import stat
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _write_all(stream: BinaryIO, data: bytes) -> None:
    view = memoryview(data)
    written = 0
    while written < len(view):
        count = stream.write(view[written:])
        if count is None or count <= 0:
            raise OSError("stream write made no progress")
        written += count
    stream.flush()


def _open_sidecar(path: Path) -> BinaryIO:
    if not path.is_absolute():
        raise ValueError("sidecar path must be absolute")
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
        0o600,
    )
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("sidecar must be a regular file")
        os.fchmod(descriptor, 0o600)
        return os.fdopen(descriptor, "ab", buffering=0)
    except BaseException:
        os.close(descriptor)
        raise


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sidecar", required=True, type=Path)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    command = list(args.command)
    if command[:1] == ["--"]:
        command = command[1:]
    if not command or not command[0] or "\0" in command[0]:
        raise ValueError("an exact child command is required")

    malformed = False
    with _open_sidecar(args.sidecar) as sidecar:
        process = subprocess.Popen(
            command,
            stdin=sys.stdin.buffer,
            stdout=subprocess.PIPE,
            stderr=None,
            shell=False,
            close_fds=True,
        )
        assert process.stdout is not None
        for sequence, raw_line in enumerate(process.stdout, start=1):
            _write_all(sys.stdout.buffer, raw_line)
            try:
                event = json.loads(raw_line)
                if not isinstance(event, dict):
                    raise TypeError
            except (json.JSONDecodeError, UnicodeDecodeError, TypeError):
                malformed = True
                continue
            envelope = (
                json.dumps(
                    {"sequence": sequence, "observed_at": _utc_now(), "event": event},
                    ensure_ascii=False,
                    separators=(",", ":"),
                    allow_nan=False,
                )
                + "\n"
            ).encode()
            _write_all(sidecar, envelope)
        returncode = process.wait()
    if returncode != 0:
        return returncode
    return 65 if malformed else 0


if __name__ == "__main__":
    raise SystemExit(main())
