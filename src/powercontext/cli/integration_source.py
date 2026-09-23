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

"""Load repository-owned integration rules using the installed CLI environment."""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

from powercontext.cli.errors import SetupError
from powercontext.cli.git_source import clone_github_source, github_clone_url, is_local_source
from powercontext.client.transport_policy import client_config_file
from powercontext.paths import powercontext_data_dir

RULES = Path("integrations/distribution/powercontext_integrations")
DEFAULT_SOURCE = "oceanbase/powercontext"
DEFAULT_REF = "master"


def saved_source(target: str | None) -> str | None:
    """Reuse the source recorded by successful setup without fetching during doctor."""
    try:
        hosts = json.loads(client_config_file().read_text())["hosts"]
        records = [hosts.get(target, {})] if target else reversed(list(hosts.values()))
        return next(
            (source for record in records if isinstance(source := record.get("installation", {}).get("source"), str)),
            None,
        )
    except FileNotFoundError:
        return None
    except (OSError, ValueError, KeyError, AttributeError, TypeError) as error:
        raise SetupError("Cannot read the saved integration source.") from error


def resolve_source(
    source: str | None = None,
    ref: str | None = None,
    *,
    target: str | None = None,
    fetch: bool = False,
    refresh: bool = False,
) -> Path | None:
    """Resolve explicit source, saved installation, development checkout, or shared Git cache."""
    source = source or os.environ.get("POWERCONTEXT_INTEGRATIONS_SOURCE")
    checkout = Path(__file__).resolve().parents[3]
    if source is None and ref is None:
        source = saved_source(target) if target else None
        source = source or (str(checkout) if (checkout / RULES).is_dir() else saved_source(None))
    if source and is_local_source(source):
        root = Path(source).expanduser().resolve()
    else:
        source, ref = source or DEFAULT_SOURCE, ref or DEFAULT_REF
        try:
            url = github_clone_url(source)
        except ValueError as error:
            raise SetupError.invalid_source("integration") from error
        if not ref or ref.startswith("-") or any(character.isspace() for character in ref):
            raise SetupError.invalid_ref("integration", ref)
        identity = hashlib.sha256(f"{url}\n{ref}".encode()).hexdigest()[:20]
        root = powercontext_data_dir() / "integrations" / identity
        if not root.exists():
            if not fetch:
                return None
            root.parent.mkdir(parents=True, exist_ok=True)
            clone_github_source(source, ref, root)
        elif refresh and fetch:
            _refresh(root, ref)
    if not (root / RULES / "__init__.py").is_file():
        raise SetupError.not_found("PowerContext integration rules", root)
    return root


def _refresh(root: Path, ref: str) -> None:
    """Refresh only an unmodified managed checkout; never discard local edits."""
    try:
        dirty = subprocess.run(  # noqa: S603
            ["git", "-C", str(root), "status", "--porcelain"],  # noqa: S607
            check=True,
            capture_output=True,
            timeout=10,
        ).stdout
        if dirty:
            raise SetupError("Integration checkout has local changes; select a clean checkout with --source.")
        for arguments in (["fetch", "--depth", "1", "origin", ref], ["checkout", "--detach", "FETCH_HEAD"]):
            subprocess.run(  # noqa: S603
                ["git", "-C", str(root), *arguments],  # noqa: S607
                check=True,
                capture_output=True,
                timeout=120,
            )
    except (OSError, subprocess.SubprocessError) as error:
        raise SetupError("Cannot refresh the integration source.") from error


def load_rules(root: Path, module: str) -> ModuleType:
    """Import rules from a selected checkout without installing a Python distribution."""
    package = "_powercontext_integrations_" + hashlib.sha256(str(root).encode()).hexdigest()[:20]
    if package not in sys.modules:
        spec = importlib.util.spec_from_file_location(package, root / RULES / "__init__.py")
        if spec is None or spec.loader is None:
            raise SetupError.not_found("PowerContext integration rules", root)
        instance = importlib.util.module_from_spec(spec)
        sys.modules[package] = instance
        try:
            spec.loader.exec_module(instance)
        except Exception:
            sys.modules.pop(package, None)
            raise
    return importlib.import_module(f"{package}.{module}")
