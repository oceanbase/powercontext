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

"""Load the exact committed evaluation bytes and retain their identity."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from pydantic import ValidationError

from .models import EvaluationManifest

DEFAULT_MANIFEST = Path(__file__).resolve().parent / "cases" / "atomic-extraction-v1.json"


@dataclass(frozen=True)
class LoadedManifest:
    manifest: EvaluationManifest
    sha256: str
    path: Path


def load_manifest(path: Path = DEFAULT_MANIFEST) -> LoadedManifest:
    """Validate a UTF-8 manifest and hash the exact bytes used for the run."""

    resolved = path.resolve()
    try:
        raw = resolved.read_bytes()
        text = raw.decode("utf-8")
        manifest = EvaluationManifest.model_validate_json(text)
    except (OSError, UnicodeError, ValidationError) as error:
        raise ValueError(f"invalid Atomic Memory extraction manifest: {resolved}") from error  # noqa: TRY003
    return LoadedManifest(manifest=manifest, sha256=sha256(raw).hexdigest(), path=resolved)


__all__ = ["DEFAULT_MANIFEST", "LoadedManifest", "load_manifest"]
