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

"""Read, write, and append operations for artifact-evolution bundles."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from pydantic import ValidationError

from powercontext_eval_artifact_evolution.models import ArtifactEvolutionBundle, ProposalAttempt
from powercontext_eval_artifact_evolution.privacy import digest_json, redact_payload


class BundleIOError(ValueError):
    """A bundle could not be read or written without losing its contract."""


class BundleValidationError(BundleIOError):
    """A bundle is not valid `powercontext.artifact-evolution/v1` JSON."""


def load_bundle(path: Path) -> ArtifactEvolutionBundle:
    """Load and validate a UTF-8 JSON bundle without invoking any evaluator."""

    resolved = path.resolve()
    try:
        raw = resolved.read_bytes()
    except OSError as error:
        raise BundleIOError(f"cannot read artifact-evolution bundle: {resolved}") from error
    try:
        return ArtifactEvolutionBundle.model_validate_json(raw)
    except ValidationError as error:
        raise BundleValidationError(f"invalid artifact-evolution bundle: {validation_error_summary(error)}") from error


def write_bundle(bundle: ArtifactEvolutionBundle, path: Path) -> Path:
    """Write one bundle, refusing to overwrite a previously recorded artifact."""

    target = path.resolve()
    try:
        bundle = ArtifactEvolutionBundle.model_validate(bundle.model_dump(mode="python", by_alias=True))
    except ValidationError as error:
        raise BundleValidationError(
            f"cannot write invalid artifact-evolution bundle: {validation_error_summary(error)}"
        ) from error
    temporary: Path | None = None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
        temporary = Path(temporary_name)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(_render(bundle))
            stream.flush()
            os.fsync(stream.fileno())
        # Hard-linking the completed file gives create-without-overwrite semantics.
        os.link(temporary, target)
    except FileExistsError as error:
        raise BundleIOError(f"refusing to overwrite artifact-evolution bundle: {target}") from error
    except OSError as error:
        raise BundleIOError(f"cannot write artifact-evolution bundle: {target}") from error
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
    return target


def record_attempt(bundle: ArtifactEvolutionBundle, attempt: ProposalAttempt) -> ArtifactEvolutionBundle:
    """Return a validated bundle with one additional realized attempt.

    This pure operation makes recording explicit and keeps the bundle immutable;
    persistence remains the caller's choice through :func:`write_bundle`.
    """

    if attempt.result.output_payload is not None:
        payload = attempt.result.output_payload
        if bundle.capture_policy.payloads == "redacted":
            payload = redact_payload(payload)
        attempt = attempt.model_copy(
            update={
                "result": attempt.result.model_copy(
                    update={"output_payload": payload, "payload_digest": digest_json(payload)}
                )
            }
        )
    updated = bundle.model_copy(update={"attempts": (*bundle.attempts, attempt)})
    return ArtifactEvolutionBundle.model_validate(updated.model_dump(mode="python", by_alias=True))


def _render(bundle: ArtifactEvolutionBundle) -> str:
    payload = bundle.model_dump(mode="json", by_alias=True, exclude_none=True)
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def validation_error_summary(error: ValidationError) -> str:
    """Return one content-safe validation error without echoing rejected input."""

    first = error.errors(include_url=False, include_context=False, include_input=False)[0]
    location = ".".join(str(part) for part in first["loc"]) or "bundle"
    return f"{location}: {first['msg']}"
