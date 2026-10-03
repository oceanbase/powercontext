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

"""Strict dependency-free validation of the bootstrap HTTP contract."""

from __future__ import annotations

import re
from collections.abc import Mapping
from hashlib import sha256
from typing import cast

SCHEMA = "powercontext.bootstrap-context.v1"
PROFILE = "powercontext.scope-bootstrap.v1"
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_RESULT_KEYS = {
    "schema",
    "status",
    "reason",
    "profile",
    "content",
    "content_bytes",
    "package_digest",
    "items",
    "truncated",
    "receipt",
}
_ITEM_KEYS = {
    "kind",
    "scope_id",
    "artifact",
    "entry_id",
    "entry_version_id",
    "content_digest",
    "truncated",
}


class InvalidBootstrapResponse(ValueError):
    pass


def validate_bootstrap_context(value: Mapping[str, object], *, max_bytes: int) -> dict[str, object]:
    if set(value) != _RESULT_KEYS or value.get("schema") != SCHEMA or value.get("profile") != PROFILE:
        raise InvalidBootstrapResponse
    status = value.get("status")
    reason = value.get("reason")
    content = value.get("content")
    content_bytes = value.get("content_bytes")
    package_digest = value.get("package_digest")
    items = value.get("items")
    truncated = value.get("truncated")
    receipt = value.get("receipt")
    if (
        status not in {"ready", "empty", "skipped"}
        or (
            reason is not None
            and reason not in {"disabled", "no_eligible_context", "already_delivered", "preparation_failed"}
        )
        or not isinstance(content_bytes, int)
        or isinstance(content_bytes, bool)
        or not isinstance(items, list)
        or len(items) > 7
        or not isinstance(truncated, bool)
    ):
        raise InvalidBootstrapResponse
    validated_receipt = validate_delivery_receipt(receipt)
    validated_items = [_validate_item(item) for item in items]
    if status == "ready":
        if (
            reason is not None
            or not isinstance(content, str)
            or not content.strip()
            or content_bytes != len(content.encode("utf-8"))
            or content_bytes > max_bytes
            or not isinstance(package_digest, str)
            or _DIGEST.fullmatch(package_digest) is None
            or package_digest != "sha256:" + sha256(content.encode("utf-8")).hexdigest()
            or not validated_items
            or validated_receipt["state"] != "pending"
        ):
            raise InvalidBootstrapResponse
    elif (
        content is not None
        or content_bytes != 0
        or package_digest is not None
        or validated_items
        or reason is None
        or validated_receipt["state"] == "pending"
    ):
        raise InvalidBootstrapResponse
    if status == "empty" and (reason != "no_eligible_context" or validated_receipt["state"] != "skipped"):
        raise InvalidBootstrapResponse
    if status == "skipped" and (
        reason == "no_eligible_context"
        or (reason == "disabled" and validated_receipt["state"] != "skipped")
        or (reason == "preparation_failed" and validated_receipt["state"] != "failed")
    ):
        raise InvalidBootstrapResponse
    return dict(value)


def validate_delivery_receipt(value: object, *, receipt_id: str | None = None) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != {"receipt_id", "state"}:
        raise InvalidBootstrapResponse
    identifier, state = value.get("receipt_id"), value.get("state")
    if (
        not _printable_identifier(identifier, maximum=64)
        or state not in {"pending", "injected", "skipped", "failed"}
        or (receipt_id is not None and identifier != receipt_id)
    ):
        raise InvalidBootstrapResponse
    return {"receipt_id": cast(str, identifier), "state": cast(str, state)}


def _validate_item(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != _ITEM_KEYS:
        raise InvalidBootstrapResponse
    kind = value.get("kind")
    scope_id = value.get("scope_id")
    artifact = value.get("artifact")
    entry_id = value.get("entry_id")
    entry_version_id = value.get("entry_version_id")
    digest = value.get("content_digest")
    if (
        kind not in {"memory_entry", "handoff"}
        or not isinstance(scope_id, str)
        or not scope_id.strip()
        or len(scope_id) > 256
        or not isinstance(artifact, dict)
        or set(artifact) != {"family", "artifact_id", "revision"}
        or not _printable_identifier(artifact.get("artifact_id"), maximum=128)
        or not isinstance(artifact.get("revision"), int)
        or isinstance(artifact.get("revision"), bool)
        or cast(int, artifact.get("revision")) < 1
        or not isinstance(digest, str)
        or _DIGEST.fullmatch(digest) is None
        or not isinstance(value.get("truncated"), bool)
    ):
        raise InvalidBootstrapResponse
    if kind == "memory_entry":
        if (
            artifact.get("family") != "memory"
            or not _printable_identifier(entry_id, maximum=128)
            or not _printable_identifier(entry_version_id, maximum=128)
        ):
            raise InvalidBootstrapResponse
    elif artifact.get("family") != "handoff" or entry_id is not None or entry_version_id is not None:
        raise InvalidBootstrapResponse
    return cast(dict[str, object], value)


def _printable_identifier(value: object, *, maximum: int) -> bool:
    return (
        isinstance(value, str)
        and 1 <= len(value) <= maximum
        and all("\x21" <= character <= "\x7e" for character in value)
    )
