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

"""Stable, redacted evidence serialization shared by all task adapters."""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

from .models import EvaluationReport, NativeArtifact, ResolvedInstruction
from .settings import HarnessSettings

REDACTED = "[REDACTED]"


def fingerprint(path: Path, *, relative_to: Path | None = None) -> NativeArtifact:
    content = path.read_bytes()
    name = path.relative_to(relative_to).as_posix() if relative_to is not None else path.name
    return NativeArtifact(name=name, sha256=sha256(content).hexdigest(), bytes=len(content))


def redact(value: str, settings: HarnessSettings) -> str:
    """Redact configured runtime secrets from diagnostic text."""

    for secret in settings.evidence_secrets():
        value = value.replace(secret, REDACTED)
        value = value.replace(json.dumps(secret, ensure_ascii=False)[1:-1], REDACTED)
    return value


def load_resolved_instructions(
    trial_dir: Path,
    settings: HarnessSettings,
) -> tuple[ResolvedInstruction, ...]:
    """Load the instructions that Harbor's ACP runner actually received."""

    resolved: list[ResolvedInstruction] = []
    for summary_path in sorted(trial_dir.rglob("acp-summary.json")):
        try:
            payload = json.loads(summary_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        instruction = payload.get("instruction") if isinstance(payload, dict) else None
        if not isinstance(instruction, str) or not instruction.strip():
            continue
        artifact = summary_path.relative_to(trial_dir)
        resolved.append(
            ResolvedInstruction(
                step=_step_name(artifact),
                artifact=artifact.as_posix(),
                content=redact(instruction, settings),
                sha256=sha256(instruction.encode("utf-8")).hexdigest(),
            )
        )
    return tuple(resolved)


def _step_name(artifact: Path) -> str | None:
    parts = artifact.parts
    return parts[1] if len(parts) > 1 and parts[0] == "steps" else None


def write_evidence(path: Path, content: str, settings: HarnessSettings) -> None:
    path.write_text(redact(content, settings), encoding="utf-8")


def write_evaluation_report(
    path: Path,
    *,
    report: EvaluationReport,
    settings: HarnessSettings,
) -> None:
    write_evidence(
        path,
        report.model_dump_json(by_alias=True, indent=2) + "\n",
        settings,
    )
