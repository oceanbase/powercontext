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
import os
from pathlib import Path, PurePosixPath
from uuid import uuid4

from markdown_it import MarkdownIt
from PIL import Image

from pc_knowledge_base.models import identity

MAX_FILE_BYTES = 16 * 1024 * 1024
EXTENSIONS = {".md", ".markdown", ".png", ".jpg", ".jpeg", ".webp"}


class ContentError(ValueError):
    """A deterministic input error; a later file can still be consumed."""


def relative_path(value):
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
        base = Path(root).expanduser().resolve(strict=True)
        if not any(base == allowed or base.is_relative_to(allowed) for allowed in self.allowed_roots):
            raise ContentError("Directory is outside the configured import roots")  # noqa: TRY003
        path = relative_path(path)
        # Open each component without following links, including races during import.
        fd = os.open(base, os.O_RDONLY | os.O_DIRECTORY)
        try:
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
    lines = text.splitlines(keepends=True)
    headings = []
    blocks = []
    covered = -1
    parsed = MarkdownIt("commonmark").enable("table").parse(text)
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
            "bullet_list_open",
            "ordered_list_open",
        }:
            start, end = token.map
            blocks.append({
                "text": "".join(lines[start:end]).rstrip(),
                "heading_path": [title for _, title in headings],
                "locator": {"line_start": start + 1, "line_end": end},
            })
            covered = end
    return blocks


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
        image = np.asarray(source.convert("RGB"))[:, :, ::-1]
    result = RapidOCR()(image)
    if not isinstance(result, RapidOCROutput):
        raise TypeError("OCR must return detection and recognition results")  # noqa: TRY003
    if not result.txts:
        return []
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


PARSERS = {
    ".md": parse_markdown,
    ".markdown": parse_markdown,
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
    # Pack adjacent Markdown blocks within a heading, retaining their complete source line range.
    packed = []
    for block in blocks:
        if (
            packed
            and "line_start" in block["locator"]
            and packed[-1]["heading_path"] == block["heading_path"]
            and len(packed[-1]["text"]) + len(block["text"]) + 2 <= maximum
        ):
            packed[-1]["text"] += "\n\n" + block["text"]
            packed[-1]["locator"]["line_end"] = block["locator"]["line_end"]
        else:
            packed.append(block)
    chunks = []
    for block in packed:
        value = block["text"]
        start = 0
        while start < len(value):
            end = min(len(value), start + maximum)
            if end < len(value):
                # Prefer paragraph/sentence boundaries; hard split only oversized individual blocks.
                cuts = [
                    value.rfind(delimiter, start + maximum // 2, end)
                    for delimiter in ("\n\n", "\n", "。", ". ", "；", "; ")  # noqa: RUF001
                ]
                boundary = max(cuts)
                if boundary > start:
                    end = boundary + 1
            text = value[start:end]
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
    return chunks
