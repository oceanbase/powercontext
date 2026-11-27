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

"""Local immutable snapshots, Markdown/HTML/OCR parsing and structural chunking."""

from __future__ import annotations

import hashlib
import io
import os
import re
from copy import deepcopy
from pathlib import Path, PurePosixPath
from uuid import uuid4

import trafilatura
from lxml import etree
from lxml.etree import XMLSyntaxError
from markdown_it import MarkdownIt
from PIL import Image
from trafilatura import extract
from trafilatura.settings import DEFAULT_CONFIG

from pc_knowledge_base.models import identity

MAX_FILE_BYTES = 16 * 1024 * 1024
EXTENSIONS = {".md", ".markdown", ".html", ".htm", ".png", ".jpg", ".jpeg", ".webp"}

EXTRACT_CONFIG = deepcopy(DEFAULT_CONFIG)


class ContentError(ValueError):
    """A deterministic input error; a later file can still be consumed."""


def relative_path(value):
    if "\x00" in value:
        raise ContentError("File paths must not contain null characters")  # noqa: TRY003
    path = PurePosixPath(value)
    if not value or "\\" in value or path.is_absolute() or any(p in {"", ".", ".."} for p in value.split("/")):
        raise ContentError("Use a nonempty normalized relative path")  # noqa: TRY003
    if path.suffix.lower() not in EXTENSIONS:
        raise ContentError("Only Markdown, HTML and standalone PNG, JPEG and WebP images are supported")  # noqa: TRY003
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
            blocks.append({
                "text": "".join(lines[start:end]).rstrip(),
                "heading_path": [title for _, title in headings],
                "locator": {"line_start": start + 1, "line_end": end},
                "references": required_references("".join(lines[start:end]), environment),
            })
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


def parse_image(data):
    import numpy as np
    from rapidocr import RapidOCR
    from rapidocr.utils.output import RapidOCROutput

    with Image.open(io.BytesIO(data)) as source:
        if source.width * source.height > 25_000_000:
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
    # OCR reading order is retained. Escape leading Markdown syntax to avoid inventing headings.
    for index, (value, box, score) in enumerate(zip(result.txts, result.boxes, result.scores, strict=True)):
        value = value.replace("\n", " ")
        markdown = value if not value.startswith(("#", "-", ">", "*")) else "\\" + value
        blocks.append({
            "text": markdown,
            "heading_path": [],
            "locator": {
                "ocr_block": index,
                "polygon": box.tolist(),
                "confidence": float(score),
                "image_width": image.shape[1],
                "image_height": image.shape[0],
            },
        })
    return blocks


_BLOCK_CONTAINERS = {"item", "list", "quote"}
_BLOCK_SEPARATORS = {
    "cell": " ",
    "head": "\n",
    "item": "\n",
    "list": "\n",
    "p": "\n\n",
    "quote": "\n",
    "row": "\n",
    "table": "\n",
}


def _heading_level(element):
    rend = (element.get("rend") or "").strip().lower()
    if rend in {"1", "2", "3", "4", "5", "6"}:
        return int(rend)
    if len(rend) == 2 and rend[0] == "h" and rend[1] in "123456":
        return int(rend[1])
    return 1


def _append_inline(parts, tail, collapse):
    if not tail:
        return
    if collapse:
        parts.append(re.sub(r"\s+", " ", tail))
    elif tail.strip():
        parts.append(tail)


def _preformatted(element):
    """Extraction drops the `<pre>` wrapper, so a line break is what still separates it from inline code."""
    return "\n" in "".join(element.itertext()) or element.find(".//lb") is not None


def _render(element, collapse=True):
    """Render element text, keeping block-level boundaries and explicit line breaks."""
    return _render_with_flag(element, collapse)[0]


def _render_with_flag(element, collapse):  # noqa: C901
    parts = []
    literal = False
    if element.text:
        parts.append(re.sub(r"\s+", " ", element.text) if collapse else element.text)
    for child in element:
        tag = child.tag if isinstance(child.tag, str) else None
        if tag is None:
            # Comments and processing instructions have no text, but their tail still separates siblings.
            _append_inline(parts, child.tail, collapse)
            continue
        if tag == "lb":
            parts.append("\n")
            _append_inline(parts, child.tail, collapse)
            continue
        if tag == "code" and element.tag in _BLOCK_CONTAINERS and _preformatted(child):
            # Only the containing element distinguishes a preformatted block from inline code.
            literal = True
            if parts:
                parts.append("\n")
            parts.append(_render_with_flag(child, False)[0].strip("\n"))
            if child.tail and child.tail.strip():
                parts.append("\n")
                parts.append(re.sub(r"\s+", " ", child.tail) if collapse else child.tail)
            continue
        separated = _BLOCK_SEPARATORS.get(tag)
        if separated is not None and parts:
            parts.append(separated)
        if tag == "table":
            parts.append(_table_text(child)[0])
        else:
            text, child_literal = _render_with_flag(child, collapse)
            # Preformatted whitespace must survive every enclosing container, not just its parent.
            literal = literal or child_literal
            parts.append(text)
        if separated is None:
            _append_inline(parts, child.tail, collapse)
        elif child.tail and child.tail.strip():
            parts.append(separated)
            parts.append(re.sub(r"\s+", " ", child.tail) if collapse else child.tail)
    text = "".join(parts)
    if literal or not collapse:
        return text, literal
    # Block separators already mark those boundaries; indentation whitespace around them is noise.
    return re.sub(r" *\n *", "\n", text), False


def _table_text(element):
    rows = []
    width = 0
    for row in element:
        if row.tag != "row":
            continue
        cells = [" ".join(_render(cell).split()) for cell in row if cell.tag == "cell"]
        # `cols` describes the source table, so empty cells still count towards its width.
        width = max(width, len(cells))
        cells = [cell for cell in cells if cell]
        if not cells:
            continue
        rows.append(" | ".join(cells))
    return "\n".join(rows), len(rows), width


def _block_for(element, headings, tree):  # noqa: C901
    tag = element.tag
    if not isinstance(tag, str):
        return None
    locator = {"xml_path": tree.getpath(element)}
    if tag == "head":
        text = " ".join("".join(element.itertext()).split())
        if not text:
            return None
        level = _heading_level(element)
        while headings and headings[-1][0] >= level:
            headings.pop()
        headings.append((level, text))
        return {"text": text, "locator": {**locator, "block_type": "heading"}}
    if tag == "p":
        text = _render(element).strip()
        if not text:
            return None
        return {"text": text, "locator": {**locator, "block_type": "paragraph"}}
    if tag == "list":
        text = _render(element).strip()
        if not text:
            return None
        return {"text": text, "locator": {**locator, "block_type": "list"}}
    if tag == "table":
        text, rows, cols = _table_text(element)
        if not text.strip():
            return None
        return {"text": text, "locator": {**locator, "block_type": "table", "rows": rows, "cols": cols}}
    if tag == "code":
        text = _render(element, collapse=False).strip("\n")
        if not text.strip():
            return None
        block = {"text": text, "locator": {**locator, "block_type": "code"}}
        if language := (element.get("rend") or "").strip():
            block["locator"]["language"] = language
        return block
    if tag == "quote":
        text = _render(element).strip()
        if not text:
            return None
        return {"text": text, "locator": {**locator, "block_type": "quote"}}
    return None


def html_blocks(xml_text):
    root = etree.fromstring(xml_text.encode("utf-8"))
    main = root.find("main")
    if main is None:
        return []

    tree = root.getroottree()
    headings, blocks, cursor = [], [], 0
    covered = set()
    for element in main.iter():
        if any(ancestor in covered for ancestor in element.iterancestors()):
            continue
        block = _block_for(element, headings, tree)
        if block is None:
            continue
        text = block["text"]
        if not text.strip():
            continue
        block["heading_path"] = [title for _, title in headings]
        block["locator"]["span_start"] = cursor
        block["locator"]["span_end"] = cursor + len(text)
        block["locator"]["span_unit"] = "char"
        blocks.append(block)
        covered.add(element)
        cursor += len(text) + 2  # Matches the "\n\n" join used when packing adjacent blocks.
    return blocks


def parse_html(data):
    try:
        xml = extract(
            data,
            output_format="xml",
            config=EXTRACT_CONFIG,
            include_tables=True,
            include_comments=False,
            include_formatting=False,
            include_links=False,
            include_images=False,
            # A fresh page-level LRU never suppresses a repeat: each text is counted once per
            # extraction. `True` would use a process-wide LRU and drop whole documents instead.
            deduplicate=False,
        )
    except (OSError, ValueError) as error:
        raise ContentError("HTML extraction failed") from error  # noqa: TRY003
    if not xml:
        raise ContentError("HTML contains no extractable main content")  # noqa: TRY003
    try:
        blocks = html_blocks(xml)
    except (XMLSyntaxError, TypeError, ValueError) as error:
        raise ContentError("HTML mapping failed") from error  # noqa: TRY003
    if not blocks:
        raise ContentError("HTML contains no extractable main content")  # noqa: TRY003
    return blocks


def _packable_span(locator):
    if "span_start" in locator:
        return "span_start", "span_end"
    if "line_start" in locator:
        return "line_start", "line_end"
    return None


# Settings that shape the XML rendered from an in-memory snapshot. Download and byte-size
# limits cannot change a single block, and deduplication is disabled, so tracking any of
# them would only invalidate published documents when an unrelated default moves.
OUTPUT_CONFIG_KEYS = ("max_tree_size", "min_extracted_size", "min_output_comm_size", "min_output_size")


def extract_config_snapshot():
    section = EXTRACT_CONFIG["DEFAULT"]
    snapshot = {}
    for key in OUTPUT_CONFIG_KEYS:
        value = section.get(key, "")
        snapshot[key] = int(value) if value.strip().isdigit() else None
    return snapshot


# The module attribute is also present in source checkouts, which carry no installed metadata.
TRAFILATURA_VERSION = getattr(trafilatura, "__version__", "unknown")


PARSER_PROFILE = identity(
    "html-parser-v1",
    TRAFILATURA_VERSION,
    extract_config_snapshot(),
    "mapping-v1",
)


def parser_profile(path):
    suffix = PurePosixPath(path).suffix.lower()
    if suffix in {".html", ".htm"}:
        return PARSER_PROFILE
    if suffix in {".md", ".markdown"}:
        return identity("markdown-parser-v1")
    return identity("image-parser-v1")


PARSERS = {
    ".md": parse_markdown,
    ".markdown": parse_markdown,
    ".html": parse_html,
    ".htm": parse_html,
    ".png": parse_image,
    ".jpg": parse_image,
    ".jpeg": parse_image,
    ".webp": parse_image,
}


def parse(data, path, config):  # noqa: C901
    parser = PARSERS[PurePosixPath(path).suffix.lower()]
    try:
        blocks = parser(data)
    except (OSError, Image.DecompressionBombError) as error:
        raise ContentError("Image is invalid or exceeds the decoded pixel limit") from error  # noqa: TRY003
    maximum, overlap = config["max_chars"], config["overlap"]
    if overlap >= maximum:
        raise ContentError("Overlap must be smaller than chunk size")  # noqa: TRY003
    # Pack adjacent blocks within a heading, retaining their complete source range.
    packed = []
    for block in blocks:
        span = _packable_span(block["locator"])
        if (
            packed
            and span is not None
            and packed[-1]["heading_path"] == block["heading_path"]
            and len(packed[-1]["text"]) + len(block["text"]) + 2 <= maximum
        ):
            packed[-1]["text"] += "\n\n" + block["text"]
            packed[-1].setdefault("references", {}).update(block.get("references", {}))
            packed[-1]["locator"][span[1]] = block["locator"][span[1]]
            if packed[-1]["locator"].get("block_type") != block["locator"].get("block_type"):
                packed[-1]["locator"].pop("block_type", None)
            for field in ("rows", "cols", "language"):
                packed[-1]["locator"].pop(field, None)
        else:
            packed.append(block)
    chunks = []
    for block in packed:
        value = block["text"]
        references = block.pop("references", {})
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
                **block,
                "text": text,
                "locator": {**block["locator"], "block_char_start": start, "block_char_end": end},
                "manually_modified": False,
                "length": len(text),
            }
            item["chunk_id"] = identity("parsed", len(chunks), item)
            chunks.append(item)
            if len(chunks) > 2000:
                raise ContentError("Document exceeds 2000 chunks; split the input document")  # noqa: TRY003
            if end == len(value):
                break
            start = max(start + 1, end - overlap)
        if references.keys() - included_references:
            raise ContentError("Reference links do not fit the chunk size; increase max_chars")  # noqa: TRY003
    return chunks
