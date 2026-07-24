from __future__ import annotations

import asyncio

import pytest

from powercontext import ArtifactRef
from powercontext.sources import ContentCapture, ContentSource, ContentSourceAdapter, SourceMaterialization
from powercontext.sources.backends.sqlite import (
    SQLiteSourceBackend,
    SQLiteSourceEvidenceCodec,
    _decode_content_source,
    _encode_content_source,
)
from powercontext.sources.journal import SourceCursor


def test_content_source_json_roundtrip_preserves_nested_unicode_metadata() -> None:
    source = ContentSource(
        name="task-中文",
        materialization=SourceMaterialization.CAPTURED,
        description="A captured result",
        content="验证完成",
        metadata={"nested": {"values": [1, True, None, "文本"]}},
    )

    assert _decode_content_source(_encode_content_source(source)) == source


def test_content_source_metadata_is_an_immutable_json_snapshot() -> None:
    async def scenario() -> None:
        metadata = {"nested": {"values": [1]}}
        capture = ContentCapture(source_id="task-1", content="Captured content.", metadata=metadata)
        source = await ContentSourceAdapter().resolve(capture)

        metadata["nested"]["values"].append(2)
        capture_nested = capture.metadata["nested"]
        assert isinstance(capture_nested, dict)
        capture_nested["values"].append(3)

        expected = {"nested": {"values": [1]}}
        assert capture.metadata == expected
        assert source.metadata == expected
        assert source.metadata is not capture.metadata
        assert _decode_content_source(_encode_content_source(source)) == source

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "payload",
    (
        "not-json",
        '{"name":"task"}',
        ('{"name":"task","materialization":"referenced","description":null,"content":"body","metadata":{}}'),
    ),
)
def test_content_source_json_rejects_invalid_payloads(payload: str) -> None:
    with pytest.raises(TypeError, match="stored Content Source payload is invalid"):
        _decode_content_source(payload)


def test_evidence_codec_reuses_core_artifact_reference(tmp_path) -> None:
    async def scenario() -> None:
        backend = SQLiteSourceBackend(tmp_path / "runtime.db")
        await backend.initialize()
        try:
            codec = SQLiteSourceEvidenceCodec(backend, "scope:codec")
            reference = ArtifactRef("memory-1", 3)

            assert codec.decode_artifact(codec.encode_artifact(reference)) == reference
            for invalid_revision in ("3", True):
                with pytest.raises(TypeError, match="Artifact reference"):
                    codec.decode_artifact({"artifact_id": "memory-1", "revision": invalid_revision})
        finally:
            await backend.close()

    asyncio.run(scenario())


def test_cursor_never_moves_backwards_across_backend_connections(tmp_path) -> None:
    async def scenario() -> None:
        database = tmp_path / "runtime.db"
        first = SQLiteSourceBackend(database)
        second = SQLiteSourceBackend(database)
        await first.initialize()
        await second.initialize()
        try:
            first_scope = first.for_scope("scope:cursor")
            second_scope = second.for_scope("scope:cursor")

            results = await asyncio.gather(
                first_scope.save_cursor("trigger", SourceCursor(3)),
                second_scope.save_cursor("trigger", SourceCursor(5)),
                return_exceptions=True,
            )
            assert all(result is None or isinstance(result, ValueError) for result in results)
            assert await first_scope.load_cursor("trigger") == SourceCursor(5)

            with pytest.raises(ValueError, match="must not move backwards"):
                await second_scope.save_cursor("trigger", SourceCursor(4))
            assert await first_scope.load_cursor("trigger") == SourceCursor(5)
        finally:
            await first.close()
            await second.close()

    asyncio.run(scenario())
