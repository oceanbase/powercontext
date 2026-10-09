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

"""Identify usable plugin sources for checkout and installed-package instructions."""

from __future__ import annotations

import importlib.metadata
import json
import re
from dataclasses import dataclass
from pathlib import Path

from powercontext.cli.git_source import InvalidGitHubSourceError, github_clone_url


@dataclass(frozen=True)
class InstallationSource:
    """A verified checkout, preserved Git origin, or tagged package-index release."""

    source: str
    ref: str | None = None


def installation_source(module_path: Path) -> InstallationSource | None:
    """Return plugin coordinates without treating site-packages as a checkout.

    Wheels exclude the integration marketplaces. PEP 610 metadata preserves the
    origin of Git installs, including forks and requested branches or tags. Package
    index releases use the distribution's version at the official release tag.
    """
    checkout = module_path.resolve().parents[3]
    if (
        (checkout / ".agents/plugins/marketplace.json").is_file()
        and (checkout / ".claude-plugin/marketplace.json").is_file()
        and (checkout / "integrations/codex/plugins/powercontext").is_dir()
        and (checkout / "integrations/claude-code/plugins/powercontext").is_dir()
    ):
        return InstallationSource(str(checkout))

    try:
        distribution = importlib.metadata.distribution("powercontext")
        direct_url_text = distribution.read_text("direct_url.json")
        if direct_url_text is None:
            metadata = distribution.metadata
            # PackageMetadata's protocol does not expose get().
            installed_version = metadata["Version"] if "Version" in metadata else None  # noqa: SIM401
            if isinstance(installed_version, str) and re.fullmatch(
                r"[0-9]+\.[0-9]+\.[0-9]+(?:(?:a|b|rc)[0-9]+)?", installed_version
            ):
                return InstallationSource("oceanbase/powercontext", f"powercontext-v{installed_version}")
        direct_url = json.loads(direct_url_text) if direct_url_text else None
    except (importlib.metadata.PackageNotFoundError, OSError, UnicodeError, ValueError):
        return None
    if not isinstance(direct_url, dict):
        return None
    vcs_info = direct_url.get("vcs_info")
    if not isinstance(vcs_info, dict) or vcs_info.get("vcs") != "git":
        return None
    source = direct_url.get("url")
    ref = vcs_info.get("requested_revision")
    if not isinstance(source, str) or not isinstance(ref, str) or not ref.strip():
        return None
    # Several integrations clone using --branch, which cannot accept a commit ID.
    if re.fullmatch(r"[0-9a-fA-F]{40}", ref):
        return None
    try:
        normalized_source = github_clone_url(source)
    except InvalidGitHubSourceError:
        return None
    return InstallationSource(normalized_source, ref)
