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

"""Validate and clone credential-free GitHub sources for CLI integrations."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from powercontext.cli.installation import SetupError
from powercontext.paths import powercontext_data_dir

_GITHUB_HOST = "github.com"


class InvalidGitHubSourceError(ValueError):
    """Raised when a source is not a supported credential-free GitHub repository."""


def is_local_source(source: str) -> bool:
    """Return whether a source describes a local filesystem path."""

    candidate = Path(source).expanduser()
    return source.startswith((".", "/", "~")) or (len(source) >= 2 and source[1] == ":") or candidate.exists()


def github_clone_url(source: str) -> str:
    """Return a clone URL for a GitHub slug, HTTPS URL, or SSH shorthand."""

    text = source.strip()
    if text.startswith("git@github.com:"):
        return f"git@github.com:{_repository_path(text.removeprefix('git@github.com:'))}"
    if text.startswith("git@"):
        raise InvalidGitHubSourceError

    parsed = urlsplit(text)
    if parsed.scheme:
        if parsed.scheme != "https" or parsed.netloc != _GITHUB_HOST or parsed.query or parsed.fragment:
            raise InvalidGitHubSourceError
        return f"{parsed.scheme}://{_GITHUB_HOST}/{_repository_path(parsed.path)}"

    return f"https://{_GITHUB_HOST}/{_repository_path(text)}"


def clone_github_source(source: str, ref: str, target: Path) -> None:
    """Clone a supported source without returning command or remote error details."""

    command = ["git", "clone", "--depth", "1", "--branch", ref, github_clone_url(source), str(target)]
    try:
        completed = subprocess.run(  # noqa: S603 - arguments are passed directly to git.
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise SetupError("failed to clone the GitHub source") from error  # noqa: TRY003
    if completed.returncode != 0:
        raise SetupError("failed to clone the GitHub source")  # noqa: TRY003


def _repository_path(value: str) -> str:
    path = value.strip().strip("/")
    if path.endswith(".git"):
        path = path.removesuffix(".git")
    parts = path.split("/")
    if len(parts) != 2 or any(not part or any(character.isspace() for character in part) for part in parts):
        raise InvalidGitHubSourceError
    return f"{parts[0]}/{parts[1]}.git"


def source_checkout(source: str, ref: str) -> Path:
    """Keep a separate checkout for each installation, including linked packages."""
    if is_local_source(source):
        return Path(source).expanduser().resolve()
    try:
        github_clone_url(source)
    except InvalidGitHubSourceError:
        raise SetupError("invalid GitHub source; use a local path or an HTTPS/SSH GitHub repository") from None  # noqa: TRY003
    parent = powercontext_data_dir() / "checkouts"
    parent.mkdir(parents=True, exist_ok=True)
    target = parent / uuid4().hex
    try:
        clone_github_source(source, ref, target)
    except BaseException:
        shutil.rmtree(target, ignore_errors=True)
        raise
    return target


def linked_checkout(path: Path) -> Path | None:
    """Return the checkout that ``source_checkout`` created for a linked package path, if it made one."""
    root = (powercontext_data_dir() / "checkouts").resolve()
    try:
        relative = path.resolve().relative_to(root)
    except (OSError, RuntimeError, ValueError):
        return None
    return root / relative.parts[0] if relative.parts else None


def resolve_plugin_source(source: str, ref: str, host: str) -> Path:
    """Locate a native package in a checkout, leaving installation to its host."""
    from powercontext.cli.guidance import HOST_GUIDANCE

    root = source_checkout(source, ref)
    descriptor = HOST_GUIDANCE[host]
    for candidate in (root, root / descriptor.plugin):
        if descriptor.recognizes(candidate):
            return candidate
    raise SetupError(f"PowerContext {host} plugin was not found in source")  # noqa: TRY003
