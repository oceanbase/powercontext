#!/usr/bin/env python3
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

"""Prefer the internal CI package mirror at explicit installation boundaries.

The helper remains usable with Python 3.9 on developer machines. It does not
change global uv/pip configuration. Locked artifacts keep their official
registry identity and hashes; only their download locations change temporarily.
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
from contextlib import contextmanager, suppress
from pathlib import Path

DEFAULT_MIRROR = "https://mirrors.aliyun.com/pypi/simple/"
OFFICIAL_ARTIFACTS = "https://files.pythonhosted.org/packages/"
SOURCE_OPTIONS = {
    "--index",
    "--default-index",
    "--index-url",
    "--extra-index-url",
    "--find-links",
    "--no-index",
    "--offline",
    "--config-file",
    "-i",
    "-f",
}
_child = None


class Interrupted(Exception):
    def __init__(self, signum: int) -> None:
        self.signum = signum
        super().__init__(signum)


def _handle_signal(signum: int, _frame: object) -> None:
    if _child is not None:
        with suppress(ProcessLookupError):
            _child.send_signal(signum)
    raise Interrupted(signum)


def _run(command: list[str], environment: dict[str, str]) -> int:
    global _child
    try:
        _child = subprocess.Popen(command, env=environment)  # noqa: S603 -- explicit installer argv, never a shell
        return _child.wait()
    finally:
        _child = None


def _internal_runner(environment: dict[str, str]) -> bool:
    return environment.get("RUNNER_OS") == "Linux" and environment.get("RUNNER_ENVIRONMENT") == "self-hosted"


def _mirror(environment: dict[str, str]) -> str:
    return environment.get("PC_CI_PYTHON_MIRROR", "") or (DEFAULT_MIRROR if _internal_runner(environment) else "")


def _enabled(value: str) -> bool:
    return value.lower() not in {"", "0", "false", "no", "off"}


def _source_is_explicit(arguments: list[str], environment: dict[str, str], *, pip: bool = False) -> bool:
    for argument in arguments:
        if argument.split("=", 1)[0] in SOURCE_OPTIONS:
            return True
        if argument.startswith(("-i", "-f")) and not argument.startswith("--"):
            return True
    prefix = "PIP" if pip else "UV"
    names = (
        ("INDEX_URL", "EXTRA_INDEX_URL", "FIND_LINKS")
        if pip
        else (
            "INDEX",
            "DEFAULT_INDEX",
            "INDEX_URL",
            "EXTRA_INDEX_URL",
            "FIND_LINKS",
            "CONFIG_FILE",
        )
    )
    return any(environment.get(f"{prefix}_{name}") for name in names) or any(
        _enabled(environment.get(f"{prefix}_{name}", "")) for name in ("NO_INDEX", "OFFLINE")
    )


def _option(arguments: list[str], long_name: str, short_name: str = "") -> str:
    result = ""
    for index, argument in enumerate(arguments):
        if argument == long_name or (short_name and argument == short_name):
            if index + 1 < len(arguments):
                result = arguments[index + 1]
        elif argument.startswith(long_name + "="):
            result = argument.split("=", 1)[1]
        elif short_name and argument.startswith(short_name) and argument != short_name:
            result = argument[len(short_name) :]
    return result


def _lock_path(arguments: list[str], environment: dict[str, str]) -> Path:
    directory = _option(arguments, "--directory", "-C") or environment.get("UV_WORKING_DIR", ".")
    start = Path(directory).resolve()
    project = _option(arguments, "--project") or environment.get("UV_PROJECT", "")
    if project:
        start = (start / project).resolve()
    for parent in (start, *start.parents):
        lock = parent / "uv.lock"
        if lock.is_file():
            return lock
    return start / "uv.lock"


def _artifact_prefix(mirror: str) -> str:
    index = mirror.rstrip("/")
    if not index.endswith("/simple"):
        message = "PC_CI_PYTHON_MIRROR must be a Simple API URL ending in /simple/"
        raise ValueError(message)
    return index[: -len("simple")] + "packages/"


def _rewrite_artifacts(contents: bytes, mirror: str) -> bytes:
    # uv's lock writer emits sdist inline and wheels as an array. Restrict the
    # replacement to these fields so direct-URL source identities stay intact.
    old = OFFICIAL_ARTIFACTS.encode()
    new = _artifact_prefix(mirror).encode()
    lines = []
    in_wheels = False
    for line in contents.splitlines(keepends=True):
        stripped = line.strip()
        if stripped.startswith(b"wheels = ["):
            in_wheels = True
        if in_wheels or stripped.startswith(b"sdist = "):
            line = line.replace(old, new)
        lines.append(line)
        if in_wheels and stripped.endswith(b"]"):
            in_wheels = False
    return b"".join(lines)


@contextmanager
def _temporary_lock(arguments: list[str], environment: dict[str, str], mirror: str):
    lock = _lock_path(arguments, environment)
    original = lock.read_bytes() if lock.is_file() else None
    try:
        if original is not None and mirror:
            lock.write_bytes(_rewrite_artifacts(original, mirror))
        yield
    finally:
        if original is not None:
            lock.write_bytes(original)


def _install(
    command: list[str],
    arguments: list[str],
    environment: dict[str, str],
    mirror: str,
    *,
    project: bool = False,
    pip: bool = False,
    direct_requirements: bool = False,
) -> tuple[int, dict[str, str]]:
    selected = dict(environment)
    selected["PIP_INDEX_URL" if pip else "UV_INDEX"] = mirror
    mirrored_command = command
    if direct_requirements:
        mirrored_command = [
            argument.replace(OFFICIAL_ARTIFACTS, _artifact_prefix(mirror)) if "#sha256=" in argument else argument
            for argument in command
        ]
    if project:
        with _temporary_lock(arguments, environment, mirror):
            result = _run(mirrored_command, selected)
    else:
        result = _run(mirrored_command, selected)
    if result <= 0:
        return result, selected
    print("Mirror installation failed; retrying the original package source.", file=sys.stderr, flush=True)
    if project:
        with _temporary_lock(arguments, environment, ""):
            return _run(command, environment), environment
    return _run(command, environment), environment


def _configure(environment: dict[str, str]) -> int:
    destination = environment.get("GITHUB_ENV")
    if not destination:
        message = "configure requires GITHUB_ENV"
        raise ValueError(message)
    with Path(destination).open("a", encoding="utf-8") as output:
        output.write(f"PC_CI_PYTHON_INSTALLER={Path(__file__).resolve()}\n")
        if _internal_runner(environment):
            output.write(f"PC_CI_PYTHON_MIRROR={_mirror(environment)}\n")
    return 0


def _run_prepared(arguments: list[str], environment: dict[str, str], mirror: str) -> int:
    if "--" not in arguments or arguments[-1] == "--":
        message = "run requires -- followed by the command"
        raise ValueError(message)
    separator = arguments.index("--")
    flags, executable = arguments[:separator], arguments[separator + 1 :]
    project = "--no-project" not in flags
    existing_lock = project and _lock_path(flags, environment).is_file()
    # Module selection applies to the actual program, not the no-op Python
    # command used to prepare the same project and --with requirements.
    preparation_flags = [flag for flag in flags if flag not in {"--module", "-m"}]
    if (
        existing_lock
        and not any(flag in flags for flag in ("--locked", "--frozen"))
        and not any(_enabled(environment.get(name, "")) for name in ("UV_LOCKED", "UV_FROZEN"))
    ):
        preparation_flags.append("--locked")
    prepare = ["uv", "run", *preparation_flags, "--", "python", "-c", "pass"]
    result, selected = _install(prepare, flags, environment, mirror, project=project)
    if result:
        return result
    # Preparation has validated the graph unless the caller requested frozen
    # semantics. The actual program must see the original lock immediately,
    # with no implicit relocking under its selected installation index.
    actual_flags = [flag for flag in flags if flag != "--locked"] if existing_lock else flags
    if existing_lock and "--frozen" not in actual_flags:
        actual_flags = [*actual_flags, "--frozen"]
    return _run(["uv", "run", *actual_flags, "--offline", "--", *executable], selected)


def _dispatch(arguments: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("configure", "sync", "run", "uv-pip", "pip", "build", "lock", "hooks"))
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    options = parser.parse_args(arguments[:1])
    operation, args = options.operation, arguments[1:]
    environment = dict(os.environ)
    if operation == "configure":
        return _configure(environment)
    if operation == "hooks":
        if args:
            parser.error("hooks does not accept arguments")
        command = ["uv", "run", "--no-sync", "prek", "install-hooks"]
    elif operation == "pip":
        pip_parser = argparse.ArgumentParser(prog="ci_python_mirror.py pip", add_help=False)
        pip_parser.add_argument("--python", required=True)
        pip_options, args = pip_parser.parse_known_args(args)
        command = [pip_options.python, "-m", "pip", *args]
    else:
        command = ["uv", "pip" if operation == "uv-pip" else operation, *args]
    mirror = _mirror(environment)
    flags = args[: args.index("--")] if operation == "run" and "--" in args else args
    bypass = not mirror or _source_is_explicit(flags, environment, pip=operation == "pip")
    if operation == "lock" and "--dry-run" not in args:
        bypass = True
    if operation == "run" and (
        any(flag in flags for flag in ("--no-cache", "--no-cache-dir", "-n", "--script", "-s"))
        or _enabled(environment.get("UV_NO_CACHE", ""))
    ):
        bypass = True
    if bypass:
        return _run(command, environment)
    if operation == "run":
        return _run_prepared(args, environment, mirror)
    result, _ = _install(
        command,
        args,
        environment,
        mirror,
        project=operation == "sync",
        pip=operation == "pip",
        direct_requirements=operation == "uv-pip",
    )
    return result


def main(arguments: list[str] | None = None) -> int:
    previous_handlers = {}
    try:
        for name in ("SIGINT", "SIGTERM", "SIGHUP"):
            signum = getattr(signal, name, None)
            if signum is not None:
                previous_handlers[signum] = signal.signal(signum, _handle_signal)
        result = _dispatch(sys.argv[1:] if arguments is None else arguments)
        return 128 - result if result < 0 else result
    except Interrupted as error:
        return 128 + error.signum
    except (OSError, ValueError) as error:
        print(f"CI Python installation failed: {error}", file=sys.stderr)
        return 1
    finally:
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)


if __name__ == "__main__":
    raise SystemExit(main())
