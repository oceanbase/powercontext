#!/usr/bin/env python3
"""Derive a stable PowerContext scope for one project directory."""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from shutil import which
from urllib.parse import urlsplit

_MAX_SCOPE_LENGTH = 256
_SCP_REMOTE = re.compile(r"^(?:[^@/\s]+@)?(?P<host>[^:/\s]+):(?P<path>.+)$")


def derive_scope_id(cwd: str, *, environ: Mapping[str, str] | None = None) -> str:
    """Return an explicit, remote-derived, or path-derived project scope."""

    environment = os.environ if environ is None else environ
    configured = environment.get("POWERCONTEXT_SCOPE_ID", "").strip()
    if configured:
        return _bounded_explicit(configured)
    root_value = _git_value(cwd, "rev-parse", "--show-toplevel")
    project_root = Path(root_value or cwd).resolve(strict=False)
    remote = _git_value(str(project_root), "config", "--get", "remote.origin.url")
    normalized_remote = normalize_git_remote(remote) if remote else None
    if normalized_remote:
        return _bounded("git", normalized_remote)
    return f"local:{hashlib.sha256(os.fsencode(project_root)).hexdigest()}"


def normalize_git_remote(remote: str) -> str | None:
    """Normalize common network remotes without retaining credentials."""

    value = remote.strip()
    if not value:
        return None
    scp_match = _SCP_REMOTE.fullmatch(value)
    if scp_match and "://" not in value:
        host = scp_match.group("host").lower()
        path = _normalize_path(scp_match.group("path"))
        return f"{host}/{path}" if path else None
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https", "ssh", "git"} or parsed.hostname is None:
        return None
    host = parsed.hostname.lower()
    if parsed.port is not None:
        host = f"{host}:{parsed.port}"
    path = _normalize_path(parsed.path)
    return f"{host}/{path}" if path else None


def _normalize_path(path: str) -> str:
    normalized = "/".join(part for part in path.replace("\\", "/").split("/") if part)
    if normalized.endswith(".git"):
        normalized = normalized[:-4]
    return normalized.rstrip("/")


def _bounded(prefix: str, value: str) -> str:
    candidate = f"{prefix}:{value}"
    if len(candidate) <= _MAX_SCOPE_LENGTH:
        return candidate
    return f"{prefix}:sha256:{hashlib.sha256(value.encode()).hexdigest()}"


def _bounded_explicit(value: str) -> str:
    if len(value) <= _MAX_SCOPE_LENGTH:
        return value
    return f"sha256:{hashlib.sha256(value.encode()).hexdigest()}"


def _git_value(cwd: str, *arguments: str) -> str | None:
    executable = which("git")
    if executable is None:
        return None
    try:
        completed = subprocess.run(  # noqa: S603 - git executable and arguments are integration-owned.
            [executable, *arguments],
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout.strip() or None


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cwd", default=os.getcwd())
    arguments = parser.parse_args(argv)
    print(derive_scope_id(arguments.cwd))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
