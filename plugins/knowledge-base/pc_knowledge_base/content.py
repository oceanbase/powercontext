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

"""Local immutable snapshots, Markdown/OCR parsing and structural chunking."""

from __future__ import annotations

import hashlib
import io
import json
import os
from importlib.metadata import version
from inspect import getsource
from pathlib import Path, PurePosixPath
from typing import Annotated
from uuid import uuid4

from markdown_it import MarkdownIt
from PIL import Image
from pydantic import Field, FiniteFloat, TypeAdapter

from pc_knowledge_base.models import identity
from pc_knowledge_base.parser_contract import (
    ContentError,
    Coverage,
    FormatCapability,
    ImageLocator,
    LineLocator,
    Locator,
    ParsedBlock,
    ParsedDocument,
    ParserAdapter,
    ParserOutput,
    ParserProfile,
    ParseWarning,
    Point,
    ResourceLimits,
    Span,
    build_parser_registry,
    serialize_context_locator,
)

MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_IMAGE_PIXELS = 25_000_000
OCR_ASSETS = {
    "config.yaml": "06623941e188461a0bc263fe16175cb5e5f6f6b01f4387e546d3a741569fc021",
    "models/PP-OCRv6_det_small.onnx": "090f04abcd9d9a7498bc4ebf677e4cb9bdce1fe4197ddb7e529f1ef44e1ff94f",
    "models/ch_ppocr_mobile_v2.0_cls_mobile.onnx": "e47acedf663230f8863ff1ab0e64dd2d82b838fceb5957146dab185a89d6215c",
    "models/PP-OCRv6_rec_small.onnx": "6f327246b50388f3c176ae304bd95767ea6dc0c9ae92153ef8cbe210b3c14884",
}
_MARKDOWN = FormatCapability(
    format="markdown", extensions=(".md", ".markdown"), limits=ResourceLimits(input_bytes=MAX_FILE_BYTES)
)
_IMAGE_LIMITS = ResourceLimits(input_bytes=MAX_FILE_BYTES, pixels=MAX_IMAGE_PIXELS)
_IMAGE_CAPABILITIES = (
    FormatCapability(format="jpeg", extensions=(".jpg", ".jpeg"), limits=_IMAGE_LIMITS, ocr=True),
    FormatCapability(format="png", extensions=(".png",), limits=_IMAGE_LIMITS, ocr=True),
    FormatCapability(format="webp", extensions=(".webp",), limits=_IMAGE_LIMITS, ocr=True),
)


def relative_path(value):
    if "\x00" in value:
        raise ContentError("File paths must not contain null characters")  # noqa: TRY003
    path = PurePosixPath(value)
    if not value or "\\" in value or path.is_absolute() or any(p in {"", ".", ".."} for p in value.split("/")):
        raise ContentError("Use a nonempty normalized relative path")  # noqa: TRY003
    if path.suffix.lower() not in EXTENSIONS:
        raise ContentError("Only Markdown and standalone PNG, JPEG and WebP images are supported")  # noqa: TRY003
    if len(value) > 1024:
        raise ContentError("Relative path is too long")  # noqa: TRY003
    return str(path)


class Snapshots:
    def __init__(self, root, allowed_roots):
        import opendal

        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.operator = opendal.Operator("fs", root=str(self.root))
        self.allowed_roots = tuple(Path(p).expanduser().resolve() for p in allowed_roots)

    def local(self, root, path):
        if "\x00" in root:
            raise ContentError("Import roots must not contain null characters")  # noqa: TRY003
        base = Path(root).expanduser().resolve(strict=True)
        if not any(base == allowed or base.is_relative_to(allowed) for allowed in self.allowed_roots):
            raise ContentError("Directory is outside the configured import roots")  # noqa: TRY003
        path = relative_path(path)
        # Anchor at the filesystem root, then open every directory without following links.
        # Opening `base` as one pathname would resolve it again after the containment check.
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        fd = os.open(base.anchor, flags)
        try:
            for part in base.parts[1:]:
                next_fd = os.open(part, flags, dir_fd=fd)
                os.close(fd)
                fd = next_fd
            parts = path.split("/")
            for part in parts[:-1]:
                next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = next_fd
            file_fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
            with os.fdopen(file_fd, "rb") as stream:
                data = stream.read(MAX_FILE_BYTES + 1)
        finally:
            os.close(fd)
        if len(data) > MAX_FILE_BYTES:
            raise ContentError("File exceeds 16 MiB")  # noqa: TRY003
        return data, str(base / path), identity("local", str(base))

    def save(self, data):
        if len(data) > MAX_FILE_BYTES:
            raise ContentError("File exceeds 16 MiB")  # noqa: TRY003
        digest = hashlib.sha256(data).hexdigest()
        key = f"sha256/{digest[:2]}/{digest}"
        # The operator's atomic-write directory prevents readers observing partial snapshots.
        temporary = f"temporary/{uuid4().hex}"
        self.operator.write(temporary, data)
        self.operator.create_dir(f"sha256/{digest[:2]}/")
        self.operator.rename(temporary, key)
        return key, digest

    def read(self, key, digest):
        if key != f"sha256/{digest[:2]}/{digest}":
            raise ContentError("Invalid snapshot reference")  # noqa: TRY003
        data = self.operator.read(key)
        if hashlib.sha256(data).hexdigest() != digest:
            raise ContentError("Snapshot checksum mismatch")  # noqa: TRY003
        return data


def markdown_blocks(text):
    # CommonMark counts only CR, LF and CRLF as line endings. Preserve their original bytes.
    lines = io.StringIO(text, newline="").readlines()
    headings = []
    blocks = []
    covered = -1
    environment = {}
    markdown = MarkdownIt("commonmark", {"store_labels": True}).enable("table")
    parsed = markdown.parse(text, environment)
    for i, token in enumerate(parsed):
        if token.map is None or token.map[0] < covered or token.nesting == -1:
            continue
        if token.type == "heading_open":
            level = int(token.tag[1:])
            # A missing heading level does not create a parent. Atomic blocks already
            # covered above must not change the outline of subsequent document blocks.
            while headings and headings[-1][0] >= level:
                headings.pop()
            headings.append((level, parsed[i + 1].content))
        if token.type in {
            "paragraph_open",
            "heading_open",
            "fence",
            "code_block",
            "table_open",
            "html_block",
            "blockquote_open",
            "bullet_list_open",
            "ordered_list_open",
        }:
            start, end = token.map
            source = "".join(lines[start:end])
            blocks.append(
                ParsedBlock(
                    text=source.rstrip(),
                    heading_path=tuple(title for _, title in headings),
                    locator=LineLocator(lines=Span(start=start, end=end)),
                    references=tuple(required_references(source, environment).items()),
                )
            )
            covered = end
    return blocks


def required_references(text, environment):
    """Keep definitions needed to render an extracted block independently."""
    markdown = MarkdownIt("commonmark", {"store_labels": True}).enable("table")
    references = environment.get("references", {})
    required = {}

    def visit(tokens):
        for token in tokens:
            if label := token.meta.get("label"):
                reference = references[label]
                # Markdown-it has already normalized/validated the destination. Escape
                # titles for a standalone definition, including multiline titles.
                title = reference["title"].replace("\\", "\\\\").replace('"', '\\"')
                required[label] = f'[{label}]: <{reference["href"]}> "{title}"'
            visit(token.children or [])

    visit(markdown.parse(text, {"references": references}))
    return required


def parse_markdown(data):
    try:
        return markdown_blocks(data.decode("utf-8-sig"))
    except UnicodeDecodeError as error:
        raise ContentError("Markdown must be UTF-8") from error  # noqa: TRY003


def parse_image(data, pixel_limit):
    import numpy as np
    from rapidocr import RapidOCR
    from rapidocr.utils.output import RapidOCROutput

    with Image.open(io.BytesIO(data)) as source:
        if pixel_limit is not None and source.width * source.height > pixel_limit:
            raise ContentError("Image exceeds 25 million pixels")  # noqa: TRY003
        if source.has_transparency_data:
            rgba = source.convert("RGBA")
            rgb = Image.new("RGB", source.size, "white")
            rgb.paste(rgba, mask=rgba.getchannel("A"))
        else:
            rgb = source.convert("RGB")
        image = np.asarray(rgb)[:, :, ::-1]
    result = RapidOCR()(image)
    if not isinstance(result, RapidOCROutput):
        raise TypeError("OCR must return detection and recognition results")  # noqa: TRY003
    if not result.txts or not any(value.strip() for value in result.txts):
        raise ContentError("Image contains no recognized text")  # noqa: TRY003
    if result.boxes is None or result.scores is None:
        raise RuntimeError("OCR returned incomplete geometry")  # noqa: TRY003
    blocks = []
    height, width = image.shape[:2]
    # OCR reading order is retained. Escape leading Markdown syntax to avoid inventing headings.
    for index, (value, box, score) in enumerate(zip(result.txts, result.boxes, result.scores, strict=True)):
        value = value.replace("\n", " ")
        markdown = value if not value.startswith(("#", "-", ">", "*")) else "\\" + value
        blocks.append(
            ParsedBlock(
                text=markdown,
                locator=ImageLocator.from_pixels(index, width, height, box.tolist(), float(score)),
            )
        )
    return blocks


def markdown_profile() -> ParserProfile:
    return ParserProfile(
        parser="markdown-it-py",
        version=version("markdown-it-py"),
        config_digest=identity(version("mdurl"), _MARKDOWN_ADAPTER_SOURCE),
    )


def image_profile() -> ParserProfile:
    return ParserProfile(
        parser="rapidocr",
        version=version("rapidocr"),
        model_digest=identity({name: digest for name, digest in OCR_ASSETS.items() if name.endswith(".onnx")}),
        config_digest=identity(
            OCR_ASSETS,
            _IMAGE_ADAPTER_SOURCE,
            *(
                (name, version(name))
                for name in (
                    "pillow",
                    "onnxruntime",
                    "opencv-python-headless",
                    "numpy",
                    "pyclipper",
                    "shapely",
                    "omegaconf",
                    "pyyaml",
                )
            ),
        ),
    )


def markdown_output(data: bytes, limits: ResourceLimits) -> ParserOutput:
    blocks = tuple(parse_markdown(data))
    return ParserOutput(blocks, Coverage(unit="block", covered=len(blocks), total=len(blocks)))


def image_output(data: bytes, limits: ResourceLimits) -> ParserOutput:
    try:
        blocks = tuple(parse_image(data, limits.pixels))
    except (OSError, Image.DecompressionBombError) as error:
        raise ContentError("Image is invalid or exceeds the decoded pixel limit") from error  # noqa: TRY003
    return ParserOutput(
        blocks,
        Coverage(
            unit="block",
            covered=len(blocks),
            warnings=(ParseWarning(code="coverage_unknown", feature="unrecognized_image_content"),),
        ),
    )


_MARKDOWN_ADAPTER_SOURCE = tuple(
    map(getsource, (markdown_output, parse_markdown, markdown_blocks, required_references))
)
_IMAGE_ADAPTER_SOURCE = tuple(map(getsource, (image_output, parse_image, ImageLocator.from_pixels)))

PARSERS = build_parser_registry((
    ParserAdapter(_MARKDOWN, markdown_profile, markdown_output),
    *(ParserAdapter(capability, image_profile, image_output) for capability in _IMAGE_CAPABILITIES),
))
CAPABILITIES = {extension: adapter.capability for extension, adapter in PARSERS.items()}
EXTENSIONS = set(PARSERS)


def parser_for(path: str) -> ParserAdapter:
    extension = PurePosixPath(path).suffix.lower()
    try:
        return PARSERS[extension]
    except KeyError:
        raise ContentError("Only Markdown and standalone PNG, JPEG and WebP images are supported") from None  # noqa: TRY003


def parser_profile(path: str) -> ParserProfile:
    return parser_for(path).profile()


def parse_document(data: bytes, path: str) -> ParsedDocument:
    return parser_for(path).parse_document(data)


def parse(data, path, config):
    return chunk_document(parse_document(data, path), config, structured=False)


def legacy_location(locator: Locator, *, legacy_lines: bool):
    if legacy_lines and isinstance(locator, LineLocator):
        return {"line_start": locator.lines.start + 1, "line_end": locator.lines.end}
    if isinstance(locator, ImageLocator):
        return {
            "ocr_block": locator.index,
            "polygon": [list(point) for point in locator.region.native_polygon],
            "confidence": locator.confidence,
            "image_width": int(locator.region.bounds[2]),
            "image_height": int(locator.region.bounds[3]),
        }
    return {}


_LEGACY_POINT = Annotated[list[FiniteFloat], Field(min_length=2, max_length=2)]
_LEGACY_POLYGON = TypeAdapter(Annotated[list[_LEGACY_POINT], Field(min_length=3, max_length=16)])


def project_legacy_chunk_locator(location: dict[str, object]) -> dict[str, object]:
    """Translate the persisted Markdown or image locator for agent context."""

    projection: dict[str, object] = {}
    start, end = location.get("block_char_start"), location.get("block_char_end")
    if type(start) is int and type(end) is int and 0 <= start < end < 2**31:
        projection.update(block_char_start=start, block_char_end=end)
    try:
        line_start, line_end = location.get("line_start"), location.get("line_end")
        if type(line_start) is int and type(line_end) is int and line_start >= 1:
            locator: LineLocator | ImageLocator = LineLocator(lines=Span(start=line_start - 1, end=line_end))
        else:
            index, width, height = (location.get(key) for key in ("ocr_block", "image_width", "image_height"))
            if type(index) is not int or type(width) is not int or type(height) is not int:
                return projection
            if width <= 0 or height <= 0 or width * height > MAX_IMAGE_PIXELS:
                return projection
            polygon = _LEGACY_POLYGON.validate_python(location.get("polygon"), strict=True)
            points: tuple[Point, ...] = tuple((x, y) for x, y in polygon)
            locator = ImageLocator.from_pixels(index, width, height, points)
        projection["source_locator"] = json.loads(serialize_context_locator(locator))
    except (ValueError, OverflowError):
        # An incomplete historical locator must not hide the cited chunk.
        pass
    return projection


def pack_markdown(blocks: tuple[ParsedBlock, ...], maximum: int) -> list[ParsedBlock]:
    # Pack adjacent Markdown blocks within a heading, retaining their complete source line range.
    packed: list[ParsedBlock] = []
    for block in blocks:
        if packed and isinstance(block.locator, LineLocator):
            previous = packed[-1]
            if (
                isinstance(previous.locator, LineLocator)
                and previous.heading_path == block.heading_path
                and len(previous.text) + len(block.text) + 2 <= maximum
            ):
                packed[-1] = ParsedBlock(
                    text=f"{previous.text}\n\n{block.text}",
                    heading_path=block.heading_path,
                    locator=LineLocator(lines=Span(start=previous.locator.lines.start, end=block.locator.lines.end)),
                    references=tuple((dict(previous.references) | dict(block.references)).items()),
                )
                continue
        packed.append(block)
    return packed


def chunk_document(document: ParsedDocument, config, *, structured=True):  # noqa: C901
    maximum, overlap = config["max_chars"], config["overlap"]
    if overlap >= maximum:
        raise ContentError("Overlap must be smaller than chunk size")  # noqa: TRY003
    markdown = document.format == "markdown"
    packed = pack_markdown(document.blocks, maximum) if markdown else document.blocks
    chunks = []
    for block in packed:
        value = block.text
        legacy = legacy_location(block.locator, legacy_lines=markdown)
        references = dict(block.references)
        included_references = set()
        environment = {}
        if references:
            MarkdownIt("commonmark").parse("\n".join(references.values()), environment)
        body_limit = maximum
        start = 0
        while start < len(value):
            end = min(len(value), start + body_limit)
            if end < len(value):
                # Prefer paragraph/sentence boundaries; hard split only oversized individual blocks.
                cuts = [
                    value.rfind(delimiter, start + body_limit // 2, end)
                    for delimiter in ("\n\n", "\n", "。", ". ", "；", "; ")  # noqa: RUF001
                ]
                boundary = max(cuts)
                if boundary > start:
                    end = boundary + 1
            text = value[start:end]
            if references:
                # Only attach definitions used by this slice. Reserve their space in
                # the configured limit instead of rejecting a link-heavy document.
                while True:
                    needed = required_references(value[start:end], environment)
                    suffix = "\n\n" + "\n".join(needed.values()) if needed else ""
                    text = value[start:end] + suffix
                    excess = len(text) - maximum
                    if excess <= 0:
                        break
                    end = max(start + 1, end - excess)
                included_references.update(needed)
            item = {
                "text": text,
                "heading_path": list(block.heading_path),
                "locator": {**legacy, "block_char_start": start, "block_char_end": end},
                "manually_modified": False,
                "length": len(text),
            }
            if not legacy:
                item["source_locator"] = block.locator.model_dump(mode="json", exclude_none=True)
            item["chunk_id"] = identity("parsed", len(chunks), item)
            if structured and legacy:
                item["source_locator"] = block.locator.model_dump(mode="json", exclude_none=True)
            chunks.append(item)
            if len(chunks) > 2000:
                raise ContentError("Document exceeds 2000 chunks; split the input document")  # noqa: TRY003
            if end == len(value):
                break
            start = max(start + 1, end - overlap)
        if references.keys() - included_references:
            raise ContentError("Reference links do not fit the chunk size; increase max_chars")  # noqa: TRY003
    return chunks
