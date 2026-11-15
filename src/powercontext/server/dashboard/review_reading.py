# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
# http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Readable projections of saved proposals; never generate a reason or evidence."""

from powercontext.server.dashboard.presenters import artifact_destination

_FIELDS = {
    "experience": ("lesson", "situation", "action", "outcome", "failure"),
    "skill": (
        "description",
        "instructions",
        "validation",
        "name",
        "allowed_tools",
        "compatibility",
        "license",
        "metadata",
        "package",
    ),
    "atomic-memory": ("kind", "text", "creation"),
    "topic-memory": ("title", "summary", "detail"),
    "profile": ("content",),
    "prompt": ("instructions", "demonstrations"),
    "handoff": ("objective", "state", "disposition", "next_action", "omissions"),
}
_LABELS = {
    "name": "review_name",
    "description": "review_description",
    "instructions": "review_instructions",
    "validation": "validation",
    "allowed_tools": "review_allowed_tools",
    "compatibility": "review_compatibility",
    "license": "review_license",
    "metadata": "review_metadata",
    "package": "review_package",
    "kind": "note_kind",
    "text": "note_body",
    "creation": "review_creation",
    "title": "review_title",
    "summary": "review_summary",
    "detail": "review_detail",
    "content": "review_body",
    "demonstrations": "prompt_demonstrations",
    "state": "recorded_state",
    "disposition": "review_disposition",
    "omissions": "handoff_omissions",
    "tags": "review_tags",
    "objective": "review_objective",
    "failure": "review_failure",
}
_FAMILY_LABELS = {"atomic-memory": "notes", "topic-memory": "topic_memory", "prompt": "prompts"}


def family_label(candidate, t):
    family = candidate["family"]
    return t.get(_FAMILY_LABELS.get(family, family), family)


def candidate_title(candidate, t):
    proposal = candidate.get("proposal") or {}
    if candidate["candidate_kind"] != "tag":
        key = {
            "skill": "description",
            "experience": "lesson",
            "atomic-memory": "text",
            "topic-memory": "title",
            "handoff": "objective",
        }.get(candidate["family"])
        value = proposal.get(key) if key else None
        if isinstance(value, str) and value.strip():
            return value
    return candidate.get("reason") or t["review_candidate_label"].format(family=family_label(candidate, t))


def content_fields(family, proposed, baseline=None):
    """Compare content fields against the recorded target, not today's head."""
    before = baseline or {}
    ignored = {"schema"} | ({"generation"} if family == "profile" else set())
    keys = list(_FIELDS.get(family, ()))
    keys.extend(sorted((proposed.keys() | before.keys()) - set(keys) - ignored))
    return [
        {
            "name": key,
            "label": _LABELS.get(key, key),
            "before": before.get(key),
            "after": proposed.get(key),
            "changed": before.get(key) != proposed.get(key),
            "technical": key in {"package", "metadata", "creation"},
        }
        for key in keys
        if key not in ignored
        and (
            family == "tag" or before.get(key) not in (None, "", [], {}) or proposed.get(key) not in (None, "", [], {})
        )
    ]


def present_detail(ctx, detail, baseline):
    candidate = detail["candidate"]
    detail["title"] = candidate_title(candidate, ctx["t"])
    detail["family_label"] = family_label(candidate, ctx["t"])
    detail["target_url"] = None
    detail["result_url"] = None
    if detail["kind"] == "tag":
        proposal = candidate["proposal"]
        detail["fields"] = content_fields("tag", {"tags": proposal["after_tags"]}, {"tags": proposal["before_tags"]})
        detail["target_url"] = artifact_destination(ctx, proposal["basis_ref"])
    else:
        detail["fields"] = content_fields(candidate["family"], candidate["proposal"], baseline)
        if detail.get("baseline_error"):
            for field in detail["fields"]:
                field["changed"] = None
        if candidate.get("target"):
            detail["target_url"] = artifact_destination(ctx, candidate["target"])
        if candidate.get("result_artifact"):
            detail["result_url"] = artifact_destination(ctx, candidate["result_artifact"])
    audit = candidate.get("audit") or {}
    run_id = candidate.get("dream_run_id") or audit.get("dream_run_id")
    detail["run_url"] = ctx["link"]("processing", record_type="dreams", run_id=run_id) if run_id else None
