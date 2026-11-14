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

from __future__ import annotations

import json
import os
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from powercontext_integration_lifecycle.dsh_handoff import receiver_context

pytestmark = pytest.mark.skipif(os.name != "posix", reason="DSH local delivery uses POSIX file permissions")


@pytest.fixture
def delivery(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, dict[str, Any]]:
    monkeypatch.setenv("HOME", str(tmp_path))
    root = tmp_path / ".powercontext-dsh-handoff"
    profile = root / ("a" * 64)
    store = profile / "deliveries"
    for directory in (root, profile, store):
        directory.mkdir(mode=0o700)
    cwd = tmp_path / "workspace"
    cwd.mkdir()
    delivery_id, attempt_id = str(uuid4()), str(uuid4())
    path = store / f"{delivery_id}.package.json"
    delivery = {
        "schema": "powercontext.dsh-delivery.v1",
        "deliveryId": delivery_id,
        "source": {"sessionId": "test"},
        "binding": {
            "source": {"sessionId": "test"},
            "effectiveCwd": str(cwd.resolve()),
            "scopeId": "scope-a",
            "serverIdentity": "server-a",
            "targetId": "claude-code-cli",
        },
        "receiptAttempt": {
            "receiptAttemptId": attempt_id,
            "sourceId": f"dsh-ack:{attempt_id}",
            "sourceType": "content",
        },
        "carrier": {
            "kind": "prepared",
            "digest": "sha256:" + "b" * 64,
            "prepared": {"scope_id": "scope-a", "content": {"objective": "Package text is not hook guidance."}},
        },
    }
    _write_delivery(path, delivery)
    return path, cwd, delivery


def _write_delivery(path: Path, delivery: dict[str, Any]) -> None:
    data = json.dumps(delivery).encode()
    path.write_bytes(data)
    path.chmod(0o600)
    record = {
        **{key: delivery[key] for key in ("schema", "deliveryId", "binding", "receiptAttempt")},
        "preparationState": "prepared",
        "package": {"sha256": sha256(data).hexdigest(), "bytes": len(data)},
    }
    record_path = path.with_name(path.name.replace(".package.json", ".record.json"))
    record_path.write_text(json.dumps(record))
    record_path.chmod(0o600)


def _context(prompt: str, cwd: Path, scope: str = "scope-a", target: str = "claude-code-cli") -> str | None:
    return receiver_context(prompt, cwd=str(cwd), target_ids=(target,), resolve_scope=lambda: scope)


@pytest.mark.parametrize("escaped_tilde", [False, True])
def test_receiver_resolves_exact_delivery_after_user_edits_the_draft(
    delivery, tmp_path: Path, escaped_tilde: bool
) -> None:
    path, cwd, package = delivery
    relative = "~/" + path.relative_to(tmp_path).as_posix()
    reference = json.dumps(relative)
    if escaped_tilde:
        reference = reference.replace('"~/', r'"\~/', 1)
    prompt = f"请先检查测试。\n{reference}\n不要修改代码。"
    context = _context(prompt, cwd)
    assert context is not None
    assert "get_connection_identity" in context
    assert "continue_handoff" in context and "acknowledge_handoff" in context
    details = json.loads(context.splitlines()[-1])
    assert details == {
        "path": str(path),
        "sha256": sha256(path.read_bytes()).hexdigest(),
        "delivery_id": package["deliveryId"],
        "scope_id": "scope-a",
        "server_id": "server-a",
        "target_id": "claude-code-cli",
        "receipt_source_id": package["receiptAttempt"]["sourceId"],
        "prepared_digest": package["carrier"]["digest"],
    }
    assert "Package text is not hook guidance." not in context
    assert "不要修改代码" not in context
    assert "user's current message" in context
    assert _context("普通会话。", cwd) is None


def test_receiver_preserves_exact_committed_revision(delivery) -> None:
    path, cwd, package = delivery
    ref = {"family": "handoff", "artifact_id": "artifact-one", "revision": 2}
    package["carrier"] = {"kind": "revision", "revision": ref}
    _write_delivery(path, package)
    context = _context(json.dumps(str(path)), cwd)
    assert context is not None
    assert json.loads(context.splitlines()[-1])["revision"] == ref


@pytest.mark.parametrize("problem", ["changed", "symlink", "scope", "target", "workspace", "multiple", "deleted"])
def test_receiver_rejects_unbound_or_unverified_delivery(delivery, tmp_path: Path, problem: str) -> None:
    path, cwd, _ = delivery
    prompt = json.dumps(str(path))
    scope, target = "scope-a", "claude-code-cli"
    if problem == "changed":
        path.write_bytes(path.read_bytes() + b" ")
    elif problem == "symlink":
        other = tmp_path / "other"
        path.rename(other)
        path.symlink_to(other)
    elif problem == "scope":
        scope = "scope-b"
    elif problem == "target":
        target = "codex-desktop"
    elif problem == "workspace":
        cwd = tmp_path
    elif problem == "multiple":
        prompt += "\n" + json.dumps(str(path.with_name(f"{uuid4()}.package.json")))
    else:
        path.with_name(path.name.replace(".package.json", ".record.json")).unlink()
    result = _context(prompt, cwd, scope=scope, target=target)
    assert result is not None
    assert result.startswith("PowerContext could not verify")
    assert str(path) not in result
