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

"""A status read may straddle multiple document publications."""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "plugins" / "knowledge-base"))
from pc_knowledge_base.service import KnowledgeBase


def test_status_preserves_publication_when_two_revisions_commit_between_reads():
    def snapshot(revision, request):
        return SimpleNamespace(
            ref=SimpleNamespace(revision=revision), content={"applied_request_id": request}, lifecycle_state="active"
        )

    revisions = {1: snapshot(1, "previous")}

    class Reads:
        async def get(self, document, *, revision=None):
            result = revisions.get(max(revisions) if revision is None else revision)
            # The first read observes v1. Two writes commit before the next read.
            revisions.update({2: snapshot(2, "import-a"), 3: snapshot(3, "import-b")})
            return result

    kb = KnowledgeBase(SimpleNamespace(reads=lambda scope: Reads()), None, None, None)
    row = {
        "resource_key": "import-a",
        "value": {
            "pinned": {"document_id": "doc", "relative_path": "doc.md", "base_revision": 1},
            "state": "processing",
        },
    }
    status = asyncio.run(kb.request_status("scope", row))
    assert (status["state"], status["published_revision"], status["error"]) == ("completed", 2, None)
