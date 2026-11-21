# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Save the profile.generate Prompt from the Dashboard editor.

The Dashboard never decides what a Prompt means. It checks what the editor can know (blank text,
access, capability, a stale revision), then writes through the public Artifact API with the
precondition the form was rendered with, so a concurrent save is reported instead of overwritten.
"""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any

from fastapi import Request

from powercontext.server.dashboard.api import DashboardAPI, segment
from powercontext.server.dashboard.revisions import _can_replace

PROMPT_KEY = "profile.generate"
MAX_BODY_BYTES = 512_000
MAX_INSTRUCTIONS = 32_768
SCHEMA_VERSION = "powercontext.prompt.v1"
MODES = {"auto", "custom"}


@dataclass(frozen=True)
class Submission:
    """What the editor form posted."""

    scope: str
    mode: str
    instructions: str
    expected_etag: str

    def draft(self, reconcile_etag: str | None = None) -> dict[str, str]:
        """Return the form state to show again, aimed at ``reconcile_etag`` when the head moved."""

        etag = self.expected_etag if reconcile_etag is None else reconcile_etag
        return {
            "scope": self.scope,
            "mode": self.mode,
            "instructions": self.instructions,
            "expected_etag": self.expected_etag,
            "reconcile_etag": etag,
        }


@dataclass(frozen=True)
class Rejection:
    """A save that wrote nothing, with what the page needs to explain it."""

    status: int = 0
    extra: dict[str, Any] = field(default_factory=dict)
    reconcile_etag: str | None = None
    login: bool = False


def parse_submission(form: Any) -> Submission:
    """Read the posted form. The Return to Auto button wins over the mode radios."""

    mode = "auto" if form.get("return_to_auto") else str(form.get("mode") or "")
    # Browsers post textarea line breaks as CRLF. Store LF and keep the rest of the text as typed.
    return Submission(
        scope=str(form.get("scope") or "").strip()[:256],
        mode=mode,
        instructions=str(form.get("instructions") or "").replace("\r\n", "\n"),
        expected_etag=str(form.get("expected_etag") or ""),
    )


def validation_error(submission: Submission) -> str | None:
    """Return the error code for input the Server would reject anyway, before any request."""

    if submission.mode not in MODES:
        return "invalid_mode"
    if submission.mode == "custom" and not submission.instructions.strip():
        return "blank_instructions"
    if submission.mode == "custom" and len(submission.instructions) > MAX_INSTRUCTIONS:
        return "instructions_too_long"
    return None


def capability_error(submission: Submission, current: dict[str, Any]) -> str | None:
    """Refuse Custom writes whenever generation cannot use custom prompts. Returning to Auto stays allowed."""

    if submission.mode != "custom" or current.get("status") == "supported":
        return None
    return f"capability_{current.get('status', 'unsupported')}"


def stale_head(submission: Submission, current: dict[str, Any]) -> tuple[int, str] | None:
    """Return the conflict status and the latest ETag when the head is not the one the form saw."""

    head = current.get("artifact_etag")
    if submission.expected_etag == "":
        return (409, head) if head is not None else None
    return (412, head or "") if head != submission.expected_etag else None


def _content(submission: Submission, current: dict[str, Any]) -> dict[str, Any]:
    custom = submission.mode == "custom"
    effective = current.get("effective") or {}
    # Existing demonstrations belong to the Custom head. The editor edits text only, so keep them.
    keep = effective.get("demonstrations") or [] if custom and current.get("mode") == "custom" else []
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": submission.mode,
        "instructions": submission.instructions if custom else "",
        "demonstrations": keep,
    }


def read_error_write_code(error: Any) -> str:
    """Map an upstream ReadError during save to a Dashboard prompt write error code."""

    if getattr(error, "status", None) == 403:
        return "forbidden"
    if getattr(error, "status", None) == 422 and getattr(error, "code", None):
        return str(error.code)
    return "service_unavailable"


def _write_failure(response: Any) -> Rejection:
    if response.status_code == 403:
        return Rejection(403, {"prompt_write_error": "forbidden"})
    if response.status_code == 422:
        code = "invalid_request"
        with suppress(ValueError, KeyError, TypeError, AttributeError):
            code = response.json()["error"]["code"]
        return Rejection(422, {"prompt_write_error": code})
    return Rejection(503, {"prompt_write_error": "service_unavailable"})


async def precheck(request: Request, api: DashboardAPI, submission: Submission) -> dict[str, Any] | Rejection:
    """Return the current configuration when a save may proceed, otherwise why it must not."""

    if code := validation_error(submission):
        return Rejection(422, {"prompt_write_error": code})
    if not await _can_replace(request, api, submission.scope, "prompt", PROMPT_KEY):
        return Rejection(403, {"prompt_write_error": "forbidden"})
    response = await api.send("GET", f"/v1/scopes/{segment(submission.scope)}/prompts/{PROMPT_KEY}")
    if response.status_code == 401:
        return Rejection(login=True)
    if response.status_code != 200:
        return Rejection(503, {"prompt_write_error": "service_unavailable"})
    current = response.json()
    if code := capability_error(submission, current):
        return Rejection(422, {"prompt_write_error": code})
    if stale := stale_head(submission, current):
        return Rejection(stale[0], {"prompt_conflict": True}, reconcile_etag=stale[1])
    return current


async def submit(api: DashboardAPI, submission: Submission, current: dict[str, Any]) -> int | None | Rejection:
    """Write the Prompt. Return the saved revision, or why nothing was written."""

    base = f"/v1/scopes/{segment(submission.scope)}"
    content = _content(submission, current)
    if submission.expected_etag == "":
        payload = {"family": "prompt", "prompt_key": PROMPT_KEY, "content": content}
        response = await api.send("POST", f"{base}/artifacts", payload)
    else:
        response = await api.send(
            "PUT",
            f"{base}/artifacts/prompt/{PROMPT_KEY}",
            {"content": content},
            headers={"If-Match": submission.expected_etag},
        )
    if response.status_code in {200, 201}:
        revision = response.json().get("revision")
        return revision if isinstance(revision, int) else None
    if response.status_code == 401:
        return Rejection(login=True)
    if response.status_code in {409, 412}:
        reread = await api.send("GET", f"{base}/prompts/{PROMPT_KEY}")
        latest = reread.json().get("artifact_etag") if reread.status_code == 200 else submission.expected_etag
        return Rejection(response.status_code, {"prompt_conflict": True}, reconcile_etag=latest or "")
    return _write_failure(response)
