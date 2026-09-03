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

"""Generate the checked-in integration capability matrix pages."""

from __future__ import annotations

import argparse
from pathlib import Path

from integration_manifest import (
    DOCUMENTATION_PATHS,
    evidence_path_errors,
    load_integration_manifest,
    release_tag_errors,
    render_integration_capability_reference,
    tool_surface_errors,
)


def _expected_documents() -> dict[Path, str]:
    manifest = load_integration_manifest()
    errors = (*evidence_path_errors(manifest), *release_tag_errors(manifest), *tool_surface_errors(manifest))
    if errors:
        raise ValueError("\n".join(errors))
    return {
        path: render_integration_capability_reference(manifest, locale) for locale, path in DOCUMENTATION_PATHS.items()
    }


def main() -> None:
    """Write the checked-in matrices, or reject stale generated output."""

    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="fail when generated documentation is stale")
    arguments = parser.parse_args()
    expected = _expected_documents()
    stale = [
        path for path, content in expected.items() if not path.is_file() or path.read_text(encoding="utf-8") != content
    ]
    if arguments.check:
        if stale:
            names = "\n".join(str(path) for path in stale)
            raise SystemExit(f"Integration capability documentation is stale:\n{names}")  # noqa: TRY003
        return
    for path, content in expected.items():
        path.write_text(content, encoding="utf-8")


if __name__ == "__main__":
    main()
