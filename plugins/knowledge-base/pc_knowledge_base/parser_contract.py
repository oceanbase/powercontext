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

"""Versioned, backend-independent results and source locations."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import ConfigDict, Field, FiniteFloat, PositiveInt, model_validator

from pc_knowledge_base.models import Model, canonical, identity

Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Index = Annotated[int, Field(ge=0, le=2**31 - 1)]
EndIndex = Annotated[int, Field(gt=0, le=2**31 - 1)]
ShortName = Annotated[str, Field(min_length=1, max_length=128)]
Point = tuple[FiniteFloat, FiniteFloat]
Format = Literal["markdown", "text", "pdf", "jpeg", "png", "webp", "docx", "xlsx", "pptx", "csv", "html"]
CONTEXT_LOCATOR_MAX_BYTES = 1024


class ContentError(ValueError):
    """A deterministic input error; a later file can still be consumed."""


class _ContractModel(Model):
    model_config = ConfigDict(frozen=True)


class Span(_ContractModel):
    """Zero-based, half-open range."""

    start: Index
    end: EndIndex

    @model_validator(mode="after")
    def validate_order(self):
        if self.start >= self.end:
            raise ValueError("A source range must be nonempty")  # noqa: TRY003
        return self


class LineLocator(_ContractModel):
    kind: Literal["lines"] = "lines"
    lines: Span


class CharacterLocator(_ContractModel):
    kind: Literal["characters"] = "characters"
    characters: Span


class Geometry(_ContractModel):
    """Native polygon and its affine map to the normalized display box."""

    origin: Literal["top_left", "bottom_left"]
    unit: Literal["pixel", "point", "emu"]
    reference_box: Literal["image", "crop_box", "media_box", "slide"]
    frame: Literal["decoded_image", "unrotated_page", "slide"]
    bounds: tuple[FiniteFloat, FiniteFloat, FiniteFloat, FiniteFloat]
    native_to_canonical: tuple[FiniteFloat, FiniteFloat, FiniteFloat, FiniteFloat, FiniteFloat, FiniteFloat]
    native_polygon: tuple[Point, ...] = Field(min_length=3, max_length=16)

    def transform(self, point: Point) -> Point:
        a, b, c, d, e, f = self.native_to_canonical
        x, y = point
        return a * x + b * y + c, d * x + e * y + f

    @property
    def canonical_polygon(self) -> tuple[Point, ...]:
        return tuple(self.transform(point) for point in self.native_polygon)

    @model_validator(mode="after")
    def validate_geometry(self):
        frame_rules = {
            "decoded_image": ("top_left", "pixel", {"image"}),
            "unrotated_page": ("bottom_left", "point", {"crop_box", "media_box"}),
            "slide": ("top_left", "emu", {"slide"}),
        }
        origin, unit, boxes = frame_rules[self.frame]
        if (self.origin, self.unit) != (origin, unit) or self.reference_box not in boxes:
            raise ValueError("The native reference box, origin and unit must match the frame")  # noqa: TRY003
        left, min_y, right, max_y = self.bounds
        if left >= right or min_y >= max_y:
            raise ValueError("The native reference box must have positive area")  # noqa: TRY003
        corners = ((left, min_y), (left, max_y), (right, min_y), (right, max_y))
        transformed = {(round(x, 12), round(y, 12)) for x, y in map(self.transform, corners)}
        if transformed != {(0, 0), (1, 0), (0, 1), (1, 1)}:
            raise ValueError("The affine map must map the native reference box to the unit box")  # noqa: TRY003
        a, b, _, d, e, _ = self.native_to_canonical
        if ((a * e - b * d) > 0) != (self.origin == "top_left"):
            raise ValueError("The affine map must preserve the native display orientation")  # noqa: TRY003
        if self.origin == "top_left":
            top_left, _, top_right, _ = corners
            anchors = tuple(
                tuple(round(value, 12) for value in self.transform(point)) for point in (top_left, top_right)
            )
            if anchors != ((0, 0), (1, 0)):
                raise ValueError("The decoded image or slide must keep its top edge in display order")  # noqa: TRY003
        if any(not (left <= x <= right and min_y <= y <= max_y) for x, y in self.native_polygon):
            raise ValueError("The native polygon must be inside its reference box")  # noqa: TRY003
        return self


class PageLocator(_ContractModel):
    kind: Literal["page"] = "page"
    page: Index
    region: Geometry | None = None

    @model_validator(mode="after")
    def validate_region(self):
        if self.region is not None and self.region.frame != "unrotated_page":
            raise ValueError("Page regions require a native page frame")  # noqa: TRY003
        return self


class ParagraphLocator(_ContractModel):
    kind: Literal["paragraph"] = "paragraph"
    paragraph: Index
    characters: Span | None = None


class TableLocator(_ContractModel):
    kind: Literal["table"] = "table"
    table: Index
    rows: Span
    columns: Span


class SlideLocator(_ContractModel):
    kind: Literal["slide"] = "slide"
    slide: Index
    shape_path: tuple[Index, ...] = Field(default=(), max_length=16)
    rows: Span | None = None
    columns: Span | None = None
    region: Geometry | None = None

    @model_validator(mode="after")
    def validate_location(self):
        if (self.rows is None) != (self.columns is None) or (self.rows is not None and not self.shape_path):
            raise ValueError("Slide table regions require a shape path and both grid ranges")  # noqa: TRY003
        if self.region is not None and self.region.frame != "slide":
            raise ValueError("Slide regions require a native slide frame")  # noqa: TRY003
        return self


class SheetLocator(_ContractModel):
    kind: Literal["sheet"] = "sheet"
    sheet: Index
    rows: Span
    columns: Span


class RecordLocator(_ContractModel):
    kind: Literal["records"] = "records"
    rows: Span
    columns: Span


class ImageLocator(_ContractModel):
    kind: Literal["image"] = "image"
    index: Index
    region: Geometry
    confidence: FiniteFloat | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def validate_region(self):
        if self.region.frame != "decoded_image":
            raise ValueError("Image regions require a decoded image frame")  # noqa: TRY003
        return self

    @classmethod
    def from_pixels(
        cls, index: int, width: int, height: int, polygon: tuple[Point, ...], confidence: float | None = None
    ):
        if width <= 0 or height <= 0:
            raise ValueError("Image dimensions must be positive")  # noqa: TRY003
        return cls(
            index=index,
            confidence=confidence,
            region=Geometry(
                origin="top_left",
                unit="pixel",
                reference_box="image",
                frame="decoded_image",
                bounds=(0, 0, width, height),
                native_to_canonical=(1 / width, 0, 0, 0, 1 / height, 0),
                native_polygon=polygon,
            ),
        )


Locator = Annotated[
    LineLocator
    | CharacterLocator
    | PageLocator
    | ParagraphLocator
    | TableLocator
    | SlideLocator
    | SheetLocator
    | RecordLocator
    | ImageLocator,
    Field(discriminator="kind"),
]


def context_locator(locator: Locator) -> dict[str, object]:
    """Serialize a bounded locator for Prepared Context."""

    fields = {
        "lines": {"lines"},
        "characters": {"characters"},
        "page": {"page"},
        "paragraph": {"paragraph", "characters"},
        "table": {"table", "rows", "columns"},
        "slide": {"slide", "shape_path", "rows", "columns"},
        "sheet": {"sheet", "rows", "columns"},
        "records": {"rows", "columns"},
        "image": {"index", "confidence"},
    }
    result = locator.model_dump(mode="json", include={"kind", *fields[locator.kind]}, exclude_none=True)
    if isinstance(locator, (PageLocator, SlideLocator, ImageLocator)) and locator.region is not None:
        result["polygon"] = [[round(x, 8), round(y, 8)] for x, y in locator.region.canonical_polygon]
    return result


def serialize_context_locator(locator: Locator) -> bytes:
    """Encode the v1 locator fields as compact UTF-8 for a downstream context consumer."""

    encoded = canonical(context_locator(locator)).encode("utf-8")
    if len(encoded) > CONTEXT_LOCATOR_MAX_BYTES:
        raise ContentError("Prepared Context locator exceeds its byte budget")  # noqa: TRY003
    return encoded


class ParserProfile(_ContractModel):
    parser: ShortName
    version: ShortName
    config_digest: Sha256
    locator_version: PositiveInt = 1
    model_digest: Sha256 | None = None


class ParsedBlock(_ContractModel):
    text: str
    locator: Locator
    heading_path: tuple[str, ...] = ()
    references: tuple[tuple[str, str], ...] = ()


class ParseWarning(_ContractModel):
    code: Literal["unsupported_content", "unreadable_region", "no_text", "coverage_unknown"]
    feature: ShortName
    locator: Locator | None = None


class Coverage(_ContractModel):
    unit: Literal["block", "page", "slide", "sheet", "record", "image"]
    covered: Index
    total: Index | None = None
    warnings: tuple[ParseWarning, ...] = Field(default=(), max_length=64)

    @model_validator(mode="after")
    def validate_counts(self):
        if self.total is not None and self.covered > self.total:
            raise ValueError("Covered units exceed the total")  # noqa: TRY003
        if (self.total is None or self.covered < self.total) and not self.warnings:
            raise ValueError("Coverage gaps require a warning")  # noqa: TRY003
        return self


class ParsedDocument(_ContractModel):
    schema_version: Literal[1] = 1
    source_digest: Sha256
    format: Format
    profile: ParserProfile
    coverage: Coverage
    blocks: tuple[ParsedBlock, ...]

    @property
    def result_id(self) -> str:
        return parsed_result_id(self.source_digest, self.format, self.profile)


def parsed_result_id(source_digest: str, format: Format, profile: ParserProfile) -> str:  # noqa: A002
    return identity("kb-parse-v1", source_digest, format, profile.model_dump(mode="json"))


def can_reuse_parse(
    previous_result_id: str | None, source_digest: str, source_format: Format, profile: ParserProfile
) -> bool:
    return previous_result_id == parsed_result_id(source_digest, source_format, profile)


class ResourceLimits(_ContractModel):
    input_bytes: PositiveInt
    expanded_bytes: PositiveInt | None = None
    pixels: PositiveInt | None = None
    pages: PositiveInt | None = None
    slides: PositiveInt | None = None
    rows: PositiveInt | None = None
    columns: PositiveInt | None = None
    cells: PositiveInt | None = None
    extracted_chars: PositiveInt | None = None


class FormatCapability(_ContractModel):
    format: Format
    extensions: tuple[str, ...] = Field(min_length=1)
    limits: ResourceLimits
    text: bool = True
    ocr: bool = False
    requires_renderer: bool = False


class PreflightCounts(_ContractModel):
    """Exact source totals, independent of blocks or successfully extracted units."""

    pages: Index | None = None
    slides: Index | None = None

    def check(self, capability: FormatCapability) -> None:
        """Apply the declared page or slide limits before extraction or publication."""

        for field, count, limit in (
            ("pages", self.pages, capability.limits.pages),
            ("slides", self.slides, capability.limits.slides),
        ):
            if limit is None:
                continue
            if count is None:
                detail = " via the configured renderer" if capability.requires_renderer and field == "pages" else ""
                raise ContentError(f"Unable to verify {field}{detail}")  # noqa: TRY003
            if count > limit:
                raise ContentError(f"Document exceeds {limit} {field}")  # noqa: TRY003


@dataclass(frozen=True)
class ParserOutput:
    blocks: tuple[ParsedBlock, ...]
    coverage: Coverage


@dataclass(frozen=True)
class ParserAdapter:
    capability: FormatCapability
    profile: Callable[[], ParserProfile]
    parse: Callable[[bytes, ResourceLimits], ParserOutput]
    count: Callable[[bytes, ResourceLimits], PreflightCounts] | None = None

    def check_input_size(self, data: bytes) -> None:
        limit = self.capability.limits.input_bytes
        if len(data) > limit:
            message = "File exceeds 16 MiB" if limit == 16 * 1024 * 1024 else f"File exceeds {limit} bytes"
            raise ContentError(message)

    def preflight(self, data: bytes) -> PreflightCounts:
        self.check_input_size(data)
        counts = PreflightCounts() if self.count is None else self.count(data, self.capability.limits)
        counts.check(self.capability)
        return counts

    def parse_document(self, data: bytes, *, profile: ParserProfile | None = None) -> ParsedDocument:
        counts = self.preflight(data)
        current_profile = self.profile()
        if profile is not None and profile != current_profile:
            raise ContentError("Processing request uses a different parser profile")  # noqa: TRY003
        limits = self.capability.limits
        output = self.parse(data, limits)
        if output.coverage.unit in {"page", "slide"}:
            total = counts.pages if output.coverage.unit == "page" else counts.slides
            if total is not None and output.coverage.total != total:
                raise ContentError("Parser coverage disagrees with preflight count")  # noqa: TRY003
        if (
            limits.extracted_chars is not None
            and sum(len(block.text) for block in output.blocks) > limits.extracted_chars
        ):
            raise ContentError("Document exceeds the extracted character limit")  # noqa: TRY003
        return ParsedDocument(
            source_digest=hashlib.sha256(data).hexdigest(),
            format=self.capability.format,
            profile=current_profile,
            coverage=output.coverage,
            blocks=output.blocks,
        )


def build_parser_registry(adapters: Iterable[ParserAdapter]) -> dict[str, ParserAdapter]:
    registry = {}
    for adapter in adapters:
        for extension in adapter.capability.extensions:
            if extension in registry:
                raise ValueError(f"Duplicate parser extension: {extension}")  # noqa: TRY003
            registry[extension] = adapter
    return registry
