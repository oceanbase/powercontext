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

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.runtime.bootstrap_context import BootstrapCandidate, build_bootstrap_context
from powercontext.builtin.runtime.models import BootstrapContextItem


def test_bootstrap_item_body_limit_includes_the_truncation_marker() -> None:
    item = BootstrapContextItem(
        kind="memory_entry",
        scope_id="scope:test",
        artifact=ArtifactRef(family="memory", artifact_id="memory", revision=1),
        entry_id="entry",
        entry_version_id="version",
        content_digest="sha256:" + ("0" * 64),
    )

    build = build_bootstrap_context((BootstrapCandidate(item=item, content="x" * 2001),), max_bytes=8192)

    assert build.content is not None
    body = next(line.removeprefix(">     ") for line in build.content.splitlines() if line.startswith(">     "))
    assert body == ("x" * 1997) + "…"
    assert len(body.encode("utf-8")) == 2000
    assert build.items[0].truncated is True
