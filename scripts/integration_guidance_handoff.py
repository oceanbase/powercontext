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

"""Controlled, contract-validated Handoff replies for live routing evaluation.

No persistence or generation runs here. This fixture validates the selected
operation, Scope, arguments, and provenance before returning transport values.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from powercontext.http._generated import models
from powercontext.server.mapping import handoff_current_work_request

REQUESTS = {
    "capture_content_source": models.CaptureContentSourceRequest,
    "handoff_current_work": models.HandoffCurrentWorkRequest,
    "activate_handoff": models.ActivateHandoffRequest,
    "prepare_handoff": models.PrepareHandoffRequest,
    "finalize_handoff": models.FinalizeHandoffRequest,
}
NATIVE_OPERATIONS = {
    "pc_capture_source": "capture_content_source",
    "pc_handoff_activate": "activate_handoff",
    "pc_handoff_prepare": "prepare_handoff",
    "pc_handoff_finalize": "finalize_handoff",
    "powercontext_capture_source": "capture_content_source",
}


class HandoffFixture:
    def __init__(self) -> None:
        self.source: dict[str, Any] | None = None
        self.draft: dict[str, Any] | None = None
        self.prepared: dict[str, Any] | None = None

    def respond(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        operation = NATIVE_OPERATIONS.get(name, name.removeprefix("powercontext_"))
        if operation not in REQUESTS or self.prepared is not None:
            raise ValueError("Unexpected operation after or during a temporary Handoff: " + name)
        request = REQUESTS[operation].model_validate({"scope_id": "fixture-scope", **arguments})
        payload = request.model_dump(mode="json", by_alias=True)
        if payload["scope_id"] != "fixture-scope":
            message = "Handoff operation changed the bound Scope"
            raise ValueError(message)
        result = self._respond(operation, payload)
        return {"ok": True, "data": result} if name.startswith("pc_") else result

    def _respond(self, operation: str, payload: dict[str, Any]) -> dict[str, Any]:
        if operation in {"capture_content_source", "handoff_current_work"}:
            if self.source is not None:
                message = "The Handoff already has a captured boundary"
                raise ValueError(message)
            self.source = {"name": "content", "source_id": payload["source_id"]}
            if operation == "capture_content_source":
                return models.CaptureContentSourceResponse.model_validate({
                    "status": "accepted",
                    "source": self.source,
                    "position": 1,
                }).model_dump(mode="json", by_alias=True)
            # Use the Server's semantic validation too: declared facts cannot invent evidence.
            work = handoff_current_work_request(models.HandoffCurrentWorkRequest.model_validate(payload)).handoff
            if any(
                claim.evidence for claim in (*work.state, *(() if work.next_action is None else (work.next_action,)))
            ):
                message = "No prior exact evidence exists in this fixture"
                raise ValueError(message)
            citation = {"kind": "source", "source_ref": self.source}
            draft = models.HandoffDraft.model_validate({
                "objective": work.objective,
                "state": [{"text": claim.text, "citations": [citation]} for claim in work.state],
                "disposition": work.disposition,
                "next_action": None
                if work.next_action is None
                else {"text": work.next_action.text, "citations": [citation]},
                "omissions": [{"text": text, "citation": None} for text in work.omissions],
            }).model_dump(mode="json", by_alias=True, exclude_none=False)
            self.prepared = self._prepare(draft)
            return models.PreparedWorkHandoff.model_validate({
                "boundary": {
                    "kind": "handoff-boundary",
                    "source": self.source,
                    "position": 1,
                    "content_digest": "sha256:" + hashlib.sha256(json.dumps(payload["handoff"]).encode()).hexdigest(),
                },
                "handoff": self.prepared,
            }).model_dump(mode="json", by_alias=True)
        if operation in {"activate_handoff", "prepare_handoff"}:
            self._check_evidence(operation, payload)
            citation = {"kind": "source", "source_ref": self.source}
            self.draft = models.HandoffDraft.model_validate({
                "objective": payload["objective"],
                "state": [
                    {"text": "Aurora README is complete and its links passed validation.", "citations": [citation]}
                ],
                "disposition": "continuable",
                "next_action": {"text": "Review the examples.", "citations": [citation]},
                "omissions": [],
            }).model_dump(mode="json", by_alias=True)
            if operation == "prepare_handoff":
                return self.draft
            return models.HandoffActivation.model_validate({
                "status": "generated",
                "boundary_source": self.source,
                "previous_position": 0,
                "current_position": 1,
                "draft": self.draft,
            }).model_dump(mode="json", by_alias=True)
        if self.draft is None or payload["draft"] != self.draft:
            message = "Finalization must use the exact returned Draft"
            raise ValueError(message)
        self.prepared = self._prepare(self.draft)
        return self.prepared

    def _check_evidence(self, operation: str, payload: dict[str, Any]) -> None:
        if self.source is None or self.draft is not None:
            message = "Prepare once, after capturing the inspected boundary"
            raise ValueError(message)
        if operation == "activate_handoff" and payload["boundary_source"] != self.source:
            message = "Activation did not use the exact returned Source"
            raise ValueError(message)
        expected = {"kind": "source", "source_ref": self.source}
        if any(citation != expected for citation in payload["evidence"]):
            message = "Handoff evidence was not returned by the capture operation"
            raise ValueError(message)

    @staticmethod
    def _prepare(draft: dict[str, Any]) -> dict[str, Any]:
        return models.PreparedHandoff.model_validate({
            "schema": "powercontext.prepared-handoff.v1",
            "scope_id": "fixture-scope",
            "base": None,
            "content": {**draft, "schema": "powercontext.handoff.v1"},
        }).model_dump(mode="json", by_alias=True)

    def carrier_returned(self, text: str) -> bool:
        """Require the exact usable carrier, not just an acknowledgement of preparation."""
        if self.prepared is None:
            return False
        decoder = json.JSONDecoder()
        for index, character in enumerate(text):
            if character != "{":
                continue
            try:
                value, _ = decoder.raw_decode(text[index:])
                if value == self.prepared:
                    return True
            except ValueError:
                continue
        return False
