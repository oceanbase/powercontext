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

"""Evaluation-only view built with WorkBuddy's owning parser, plus an explicit envelope gate."""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

PARSER_VERSION = "workbuddy-host-envelope-candidate.v1"


def repository_root() -> Path:
    for path in Path(__file__).resolve().parents:
        if (path / "integrations/workbuddy/plugins/powercontext/hooks/workbuddy_powercontext_hook.py").is_file():
            return path
    raise ValueError("capture ownership replay requires a repository checkout")


def load_hook(root: Path) -> ModuleType:
    path = root / "integrations/workbuddy/plugins/powercontext/hooks/workbuddy_powercontext_hook.py"
    name = "_capture_ownership_workbuddy_hook"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError("WorkBuddy hook cannot be loaded")
    previous_path = sys.path[:]
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path[:] = previous_path
    return module


@dataclass(frozen=True)
class Turn:
    status: str
    start: int | None = None
    end: int | None = None
    reason: str = "validated_host_envelope"

    def text(self, prompt: str) -> str:
        return "" if self.start is None or self.end is None else prompt[self.start : self.end]


def current_turn(prompt: str, hook: ModuleType) -> Turn:
    """Accept complete host records only; never use recall's whole-prompt fallback.

    This is a frozen evaluation hypothesis, not authentication or a production parser.
    Ground-truth ownership labels are deliberately not available to this function.
    """

    cursor = 0
    pending_wrapper = False
    selected: tuple[int, int] | None = None
    while cursor < len(prompt):
        if prompt[cursor].isspace():
            cursor += 1
            continue
        if prompt.startswith("<user_query>", cursor):
            if not pending_wrapper:
                return Turn("unknown", reason="missing_host_envelope")
            closed = hook._matched_user_query_close(prompt, cursor)
            if closed is None or hook._host_wrapper_close(prompt, cursor) != closed:
                return Turn("unknown", reason="malformed_user_wrapper")
            selected = (cursor + len("<user_query>"), closed)
            cursor = closed + len("</user_query>")
            pending_wrapper = False
            continue
        matched = False
        for tag in ("system-reminder", "conversation_history_summary", "cb_summary", "task-notification"):
            opener = f"<{tag}"
            if not prompt.startswith(opener, cursor):
                continue
            boundary = cursor + len(opener)
            if boundary >= len(prompt) or prompt[boundary] not in "> \t\r\n":
                continue
            opened = prompt.find(">", boundary)
            closed = prompt.find(f"</{tag}>", opened + 1)
            if opened < 0 or closed < 0 or opener in prompt[opened + 1 : closed]:
                return Turn("unknown", reason="malformed_host_block")
            if tag == "system-reminder":
                pending_wrapper = True
            elif pending_wrapper:
                return Turn("unknown", reason="unfinished_host_record")
            cursor = closed + len(f"</{tag}>")
            matched = True
            break
        if not matched:
            return Turn("unknown", reason="unrecognized_host_record")
    if selected is None or pending_wrapper:
        return Turn("unknown", reason="missing_current_wrapper")
    start, end = selected
    # The candidate must agree with the actual host-owned parser, not override it.
    if hook._last_user_query(prompt) != prompt[start:end]:
        return Turn("unknown", reason="ambiguous_host_parser")
    return Turn("eligible" if prompt[start:end].strip() else "empty", start, end)
