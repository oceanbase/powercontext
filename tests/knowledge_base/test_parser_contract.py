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

"""Exercise serialization, source anchors, and parse result reuse."""

import hashlib
import io
import sys
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFont
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "plugins" / "knowledge-base"))
from pc_knowledge_base import content
from pc_knowledge_base.parser_contract import (
    CONTEXT_LOCATOR_MAX_BYTES,
    CharacterLocator,
    Coverage,
    FormatCapability,
    Geometry,
    ImageLocator,
    LineLocator,
    PageLocator,
    ParagraphLocator,
    ParsedBlock,
    ParsedDocument,
    ParserAdapter,
    ParserOutput,
    ParserProfile,
    PreflightCounts,
    RecordLocator,
    ResourceLimits,
    SheetLocator,
    SlideLocator,
    Span,
    TableLocator,
    build_parser_registry,
    can_reuse_parse,
    context_locator,
    parsed_result_id,
    serialize_context_locator,
)


@pytest.mark.parametrize("extension", [".md", ".markdown"])
def test_markdown_result_retains_source_and_legacy_output(extension):
    data = "\ufeff# 写作\r\n\r\nRead [target][ref].\r\n\r\n[ref]: /there\r\n".encode()
    path = f"synthetic{extension}"
    parsed = content.parse_document(data, path)
    assert ParsedDocument.model_validate_json(parsed.model_dump_json()) == parsed
    heading, paragraph = parsed.blocks
    assert heading.locator == LineLocator(lines=Span(start=0, end=1))
    assert paragraph.locator == LineLocator(lines=Span(start=2, end=3))
    assert paragraph.heading_path == ("写作",)
    chunks = content.parse(data, path, {"max_chars": 360, "overlap": 40})
    assert chunks[0]["text"] == '# 写作\n\nRead [target][ref].\n\n[REF]: </there> ""'
    assert chunks[0]["locator"] == {
        "line_start": 1,
        "line_end": 3,
        "block_char_start": 0,
        "block_char_end": len("# 写作\n\nRead [target][ref]."),
    }
    assert "<" + "/there" + ">" in chunks[0]["text"]


def test_cross_format_round_trip_and_cropped_rotated_page():
    # Translate an unrotated bottom-left crop and rotate it clockwise in display space.
    region = Geometry(
        origin="bottom_left",
        unit="point",
        reference_box="crop_box",
        frame="unrotated_page",
        bounds=(20, 30, 220, 130),
        native_to_canonical=(0, 0.01, -0.3, 0.005, 0, -0.1),
        native_polygon=((20, 30), (70, 30), (70, 80), (20, 80)),
    )
    page = PageLocator(page=1, region=region)
    assert region.transform((220, 130)) == pytest.approx((1, 1))
    assert region.transform((70, 80)) == pytest.approx((0.5, 0.25))
    assert context_locator(page) == {
        "kind": "page",
        "page": 1,
        "polygon": [[0, 0], [0, 0.25], [0.5, 0.25], [0.5, 0]],
    }
    span = Span(start=1, end=3)
    unicode_text = "Aé😀中"
    assert unicode_text[span.start : span.end] == "é😀"
    for locator in (
        CharacterLocator(characters=span),
        page,
        ParagraphLocator(paragraph=0, characters=span),
        TableLocator(table=0, rows=span, columns=span),
        SlideLocator(slide=0, shape_path=(0, 1)),
        SheetLocator(sheet=0, rows=span, columns=span),
        RecordLocator(rows=span, columns=span),
        LineLocator(lines=span),
        ImageLocator.from_pixels(5, 100, 100, ((0, 0), (40, 0), (40, 40), (0, 40))),
    ):
        block = ParsedBlock(text=unicode_text, locator=locator)
        assert ParsedBlock.model_validate_json(block.model_dump_json()) == block
        entry = ParserAdapter(
            capability=FormatCapability(format="pdf", extensions=(".pdf",), limits=ResourceLimits(input_bytes=10)),
            profile=content.markdown_profile,
            parse=lambda _data, _limits, block=block: ParserOutput(
                (block,), Coverage(unit="block", covered=1, total=1)
            ),
        )
        parsed = build_parser_registry((entry,))[".pdf"].parse_document(b"fixture")
        assert parsed.source_digest == hashlib.sha256(b"fixture").hexdigest()
        (chunk,) = content.chunk_document(parsed, {"max_chars": 80, "overlap": 10})
        assert chunk["source_locator"] == locator.model_dump(mode="json", exclude_none=True)
        assert chunk["text"] == unicode_text
    with pytest.raises(content.ContentError, match="File exceeds 10 bytes"):
        entry.parse_document(b"a" * 11)
    with pytest.raises(ValueError, match="Duplicate parser extension"):
        build_parser_registry((entry, entry))
    with pytest.raises(ValidationError):
        Geometry.model_validate({**region.model_dump(), "native_to_canonical": (0, 0, 0, 0, 0, 0)})
    with pytest.raises(ValidationError):
        Coverage(unit="page", covered=1, total=2)


def test_parse_identity_and_reuse_decisions():
    source_digest = hashlib.sha256(b"# Title").hexdigest()
    profile = ParserProfile(parser="test", version="1", config_digest="a" * 64, model_digest="b" * 64)
    expected = parsed_result_id(source_digest, "markdown", profile)
    assert expected == "1b4d112d10c88c0c878b84c56b4f7ed7874d0dd475c4e7dafe4c9c481296b612"

    assert can_reuse_parse(expected, source_digest, "markdown", profile.model_copy())
    assert not can_reuse_parse(None, source_digest, "markdown", profile)
    assert not can_reuse_parse(expected, "c" * 64, "markdown", profile)
    assert not can_reuse_parse(expected, source_digest, "text", profile)
    for changed in ({"version": "2"}, {"config_digest": "d" * 64}, {"locator_version": 2}, {"model_digest": "e" * 64}):
        assert not can_reuse_parse(expected, source_digest, "markdown", profile.model_copy(update=changed))

    document = content.parse_document(b"# Title", "sample.md")
    assert content.parse_document(b"# Title", "sample.markdown").result_id == document.result_id
    # Both downstream requests consume the same parse key, although their index configuration differs.
    text = b"# Section\n\n" + b"PowerContext. " * 20
    parsed = content.parse_document(text, "sample.md")
    downstream = [({"max_chars": 80, "overlap": 10}, "embedding-a"), ({"max_chars": 360, "overlap": 40}, "embedding-b")]
    sizes = []
    for chunking, _embedding in downstream:
        assert can_reuse_parse(parsed.result_id, parsed.source_digest, parsed.format, parsed.profile)
        sizes.append(len(content.chunk_document(parsed, chunking)))
    assert sizes[0] > sizes[1]


def test_preflight_consumes_actual_counts_and_requires_docx_renderer():
    base = ResourceLimits(input_bytes=50_000_000, pages=1000)
    docx = FormatCapability(format="docx", extensions=(".docx",), limits=base, requires_renderer=True)
    pptx = FormatCapability(
        format="pptx", extensions=(".pptx",), limits=ResourceLimits(input_bytes=50_000_000, slides=1000)
    )
    for capability, field in ((docx, "pages"), (pptx, "slides")):
        counts = PreflightCounts.model_validate({field: 1000})
        assert PreflightCounts.model_validate_json(counts.model_dump_json()) == counts
        counts.check(capability)
        with pytest.raises(content.ContentError, match=f"Document exceeds 1000 {field}"):
            PreflightCounts.model_validate({field: 1001}).check(capability)
    with pytest.raises(content.ContentError, match="configured renderer"):
        PreflightCounts().check(docx)
    inconsistent = ParserAdapter(
        capability=docx,
        profile=content.markdown_profile,
        parse=lambda _data, _limits: ParserOutput((), Coverage(unit="page", covered=1, total=1)),
        count=lambda _data, _limits: PreflightCounts(pages=2),
    )
    with pytest.raises(content.ContentError, match="coverage disagrees"):
        inconsistent.parse_document(b"protocol-only fixture")


def test_context_locator_uses_whitelist_and_byte_budget():
    pixel = ImageLocator.from_pixels(2, 100, 200, ((0, 0), (100, 0), (100, 50), (0, 50)), 0.95)
    assert serialize_context_locator(pixel) == (
        b'{"index":2,"kind":"image","polygon":[[0.0,0.0],[1.0,0.0],[1.0,0.25],[0.0,0.25]]}'
    )
    maximum = 2**31 - 1
    slide = SlideLocator(
        slide=maximum,
        shape_path=(maximum,) * 16,
        rows=Span(start=maximum - 1, end=maximum),
        columns=Span(start=maximum - 1, end=maximum),
        region=Geometry(
            origin="top_left",
            unit="emu",
            reference_box="slide",
            frame="slide",
            bounds=(0, 0, 1, 1),
            native_to_canonical=(1, 0, 0, 0, 1, 0),
            native_polygon=((0.12345678, 0.87654321),) * 16,
        ),
    )
    encoded = serialize_context_locator(slide)
    assert len(encoded) <= CONTEXT_LOCATOR_MAX_BYTES
    assert b"native_polygon" not in encoded and b"transform" not in encoded and b"region" not in encoded


@pytest.mark.parametrize(
    ("extension", "image_format"), [("png", "PNG"), ("jpg", "JPEG"), ("jpeg", "JPEG"), ("webp", "WEBP")]
)
def test_image_formats_return_real_ocr_with_normalized_geometry(extension, image_format):
    pytest.importorskip("rapidocr")
    image = Image.new("RGB", (1000, 240), "white")
    ImageDraw.Draw(image).text(
        (30, 40), "Verify backups before restarting.", font=ImageFont.load_default(size=36), fill="black"
    )
    data = io.BytesIO()
    image.save(data, format=image_format)
    result = content.parse_document(data.getvalue(), f"notes.{extension}")
    assert "backups" in " ".join(block.text.lower() for block in result.blocks)
    assert result.coverage.warnings[0].code == "coverage_unknown"
    assert ParsedDocument.model_validate_json(result.model_dump_json()) == result
    for parsed, chunk in zip(
        result.blocks, content.chunk_document(result, {"max_chars": 360, "overlap": 40}), strict=True
    ):
        assert isinstance(parsed.locator, ImageLocator)
        assert chunk["locator"]["ocr_block"] == parsed.locator.index
        assert all(0 <= value <= 1 for point in parsed.locator.region.canonical_polygon for value in point)
