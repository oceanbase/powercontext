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
import hashlib
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "plugins" / "knowledge-base"))
from pc_knowledge_base.content import parse_document
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


@pytest.mark.parametrize(
    ("recorded", "processing", "expected"),
    [
        ("legacy", "ordinary", "unchanged"),
        ("legacy", "rechunk", "publish"),
        ("legacy", "reprocess", "publish"),
        ("missing_key", "ordinary", "publish"),
        ("key_only", "ordinary", "publish"),
        ("different_key", "ordinary", "publish"),
        ("current", "ordinary", "unchanged"),
        ("old_profile", "ordinary", "explicitly reprocess"),
        ("old_profile", "stale_request", "different parser profile"),
        ("old_profile", "reprocess", "publish"),
    ],
)
def test_generation_with_existing_revision(recorded, processing, expected):  # noqa: C901
    data = b"# Maintenance\n\nCheck fan bearings."
    digest = hashlib.sha256(data).hexdigest()
    parsed = parse_document(data, "service.md")
    profile = parsed.profile.model_dump(mode="json")
    old_profile = {**profile, "version": "older"}
    chunking = {"max_chars": 360, "overlap": 40}
    original_source = {"source_type": "knowledge-base-file", "source_id": "file"}
    source = SimpleNamespace(
        projection={
            "key": "request",
            "payload": {
                "request_key": "request",
                "original_source": original_source,
            },
        },
        ref="processing-source",
    )
    request = SimpleNamespace(scope_id="scope", sources=[source])
    revision_metadata = {
        "legacy": {},
        "missing_key": {"parser_profile": profile},
        "key_only": {"parsed_result_id": "0" * 64},
        "different_key": {"parser_profile": profile, "parsed_result_id": "0" * 64},
        "current": {"parser_profile": profile, "parsed_result_id": parsed.result_id},
        "old_profile": {"parser_profile": old_profile},
    }
    previous = SimpleNamespace(
        ref=SimpleNamespace(revision=1),
        lifecycle_state="active",
        content={"content_digest": digest, "chunking": chunking, **revision_metadata[recorded]},
    )
    pinned = {
        "document_id": "doc",
        "base_revision": 1,
        "snapshot": "source",
        "content_digest": digest,
        "relative_path": "service.md",
        "chunking": {**chunking, "max_chars": 120} if processing == "rechunk" else chunking,
        "embedding_profile": "embedding",
        "parser_profile": old_profile if processing == "stale_request" else profile,
        "manual_edits_policy": "reject",
        "force_reprocess": processing in {"reprocess", "stale_request"},
    }

    class Store:
        def __init__(self):
            self.observations = []

        async def resource(self, _scope, kind, _key):
            if kind == "scope":
                return {"value": {"embedding_profile": "embedding"}}
            return {"value": {"pinned": pinned, "original_source": original_source}}

        async def prepare(self, *_args):
            pass

        async def observation(self, _scope, _key, **fields):
            self.observations.append(fields)

    class Writer:
        def __init__(self):
            self.publications = []

        async def get(self, _document, *, revision=None):
            return previous if revision is None else None

        @asynccontextmanager
        async def transaction(self):
            yield self

        async def put(self, _document, **fields):
            self.publications.append(fields)

    class Embedding:
        profile = "embedding"

        async def embed(self, texts):
            return [[1.0] for _ in texts]

    def read_snapshot(_key, _digest):
        assert expected == "publish", "Unchanged and rejected imports must not read the original for parsing"
        return data

    writer, store = Writer(), Store()
    kb = KnowledgeBase(None, store, SimpleNamespace(read=read_snapshot), Embedding())
    asyncio.run(kb.generate(request, writer))
    if expected == "publish":
        (published,) = writer.publications
        assert published["expected_revision"] == 1
        assert published["content"]["parser_profile"] == profile
        assert published["content"]["parsed_result_id"] == parsed.result_id
        assert published["content"]["parse_coverage"] == parsed.coverage.model_dump(mode="json")
    elif expected == "unchanged":
        assert not writer.publications
        assert store.observations[-1] == {"state": "completed", "error": None, "unchanged_revision": 1}
    else:
        assert not writer.publications
        assert store.observations[-1]["state"] == "failed"
        assert expected in store.observations[-1]["error"]
