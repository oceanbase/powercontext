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

"""Page-level Sources for complete, offline PDF documents.

Every physical page of the original file becomes its own ``CAPTURED`` Source so
a retrieval citation resolves to one page that can be verified against the
document digest and the physical page number. Text is taken from the document
itself; nothing is pre-parsed, and no page is skipped.
"""

from __future__ import annotations

import hashlib
import io
import re
from collections.abc import Sequence
from typing import Any
from urllib.parse import quote

from pydantic import BaseModel, model_validator

from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources import TEXT_EVIDENCE_PROJECTION_KEY, AdapterSourceDefinition, TextEvidence
from powercontext.sources.models import Source, SourceMaterialization

PDF_PAGE_SOURCE_NAME = "pdf-page"
PDF_PAGE_SOURCE_VERSION = "1"
PDF_PAGE_SOURCE_ID_PREFIX = "pdf"
_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "did",
        "do",
        "does",
        "for",
        "from",
        "had",
        "has",
        "have",
        "how",
        "in",
        "is",
        "it",
        "its",
        "of",
        "on",
        "or",
        "that",
        "the",
        "their",
        "then",
        "there",
        "these",
        "this",
        "to",
        "was",
        "were",
        "what",
        "when",
        "which",
        "who",
        "why",
        "with",
    }
)
_WORD = re.compile(r"[A-Za-z]+|\d[\d,.]*")


class PDFDocumentInput(BaseModel):
    """One complete original PDF and the caller's document identity."""

    document_id: str
    content: bytes

    @model_validator(mode="after")
    def validate_document(self) -> PDFDocumentInput:
        if not isinstance(self.document_id, str) or not self.document_id.strip():
            raise ValueError("document_id must be a non-empty string")  # noqa: TRY003
        if not isinstance(self.content, (bytes, bytearray, memoryview)):
            raise ValueError("pdf content must be bytes")  # noqa: TRY003
        return self


class PDFPageInput(BaseModel):
    """One physical page of one exact PDF revision."""

    document_id: str
    document_sha256: str
    page_number: int
    text: str

    @model_validator(mode="after")
    def validate_page(self) -> PDFPageInput:
        if not self.document_id.strip():
            raise ValueError("document_id must be a non-empty string")  # noqa: TRY003
        if not isinstance(self.page_number, int) or isinstance(self.page_number, bool) or self.page_number < 1:
            raise ValueError("page_number must be a positive integer")  # noqa: TRY003
        return self


class PDFPageSource(Source):
    """A captured page that can be read back and verified against its document."""

    source_id: str
    document_id: str
    document_sha256: str
    page_number: int
    text: str


def pdf_page_source_id(document_id: str, document_sha256: str, page_number: int) -> str:
    """Build the stable identity of one physical page of one PDF revision."""

    if not isinstance(document_id, str) or not document_id.strip():
        raise ValueError("document_id must be a non-empty string")  # noqa: TRY003
    if not isinstance(document_sha256, str) or len(document_sha256) != 64:
        raise ValueError("document_sha256 must be a SHA-256 hex digest")  # noqa: TRY003
    if not isinstance(page_number, int) or isinstance(page_number, bool) or page_number < 1:
        raise ValueError("page_number must be a positive integer")  # noqa: TRY003
    source_id = f"{PDF_PAGE_SOURCE_ID_PREFIX}:{quote(document_id, safe='')}:{document_sha256}:{page_number}"
    if len(source_id) > MAX_SOURCE_ID_LENGTH:
        folded = hashlib.sha256(document_id.encode("utf-8")).hexdigest()[:32]
        source_id = f"{PDF_PAGE_SOURCE_ID_PREFIX}:{folded}:{document_sha256}:{page_number}"
    if len(source_id) > MAX_SOURCE_ID_LENGTH:
        raise ValueError(f"pdf page identity must not exceed {MAX_SOURCE_ID_LENGTH} characters")  # noqa: TRY003
    return source_id


def parse_pdf_document(value: PDFDocumentInput, /) -> tuple[PDFPageInput, ...]:
    """Extract every physical page of the original PDF, in order."""

    content = bytes(value.content)
    if not content.strip():
        raise ValueError("pdf content must not be empty")  # noqa: TRY003
    digest = hashlib.sha256(content).hexdigest()
    reader = _open_reader(content)
    try:
        page_count = len(reader.pages)
    except Exception as error:  # noqa: BLE001 - any unreadable page tree is a corrupt document
        raise ValueError(f"pdf page tree is unreadable: {error}") from error
    if page_count < 1:
        raise ValueError("pdf document must contain at least one page")  # noqa: TRY003

    pages: list[PDFPageInput] = []
    for index in range(page_count):
        try:
            text = reader.pages[index].extract_text() or ""
        except Exception as error:  # noqa: BLE001 - do not return a partial document
            raise ValueError(f"pdf page {index + 1} could not be parsed: {error}") from error
        pages.append(
            PDFPageInput(
                document_id=value.document_id,
                document_sha256=digest,
                page_number=index + 1,
                text=text,
            )
        )
    return tuple(pages)


class PDFPageAdapter:
    """Resolve one page input into its captured Source and read it back."""

    input_class = PDFPageInput
    name = PDF_PAGE_SOURCE_NAME
    source_class = PDFPageSource

    async def resolve(self, value: PDFPageInput, /) -> PDFPageSource:
        source_id = pdf_page_source_id(value.document_id, value.document_sha256, value.page_number)
        return PDFPageSource(
            name=source_id,
            definition_version=PDF_PAGE_SOURCE_VERSION,
            materialization=SourceMaterialization.CAPTURED,
            source_id=source_id,
            document_id=value.document_id,
            document_sha256=value.document_sha256,
            page_number=value.page_number,
            text=value.text,
        )

    async def read(self, source: PDFPageSource, /) -> PDFPageInput:
        return PDFPageInput(
            document_id=source.document_id,
            document_sha256=source.document_sha256,
            page_number=source.page_number,
            text=source.text,
        )


class PDFPageTextEvidenceProjection:
    """Expose the page body with the document identity that locates it."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = PDFPageSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: PDFPageSource, /) -> TextEvidence:
        return TextEvidence(
            source_type=PDF_PAGE_SOURCE_NAME,
            source_id=source.source_id,
            content=f"{source.document_id}\n{source.text}",
            metadata={
                "document_id": source.document_id,
                "document_sha256": source.document_sha256,
                "page_number": source.page_number,
            },
        )


PDF_PAGE_SOURCE_ADAPTER = PDFPageAdapter()
PDF_PAGE_SOURCE_DEFINITION = AdapterSourceDefinition(
    PDF_PAGE_SOURCE_ADAPTER,
    version=PDF_PAGE_SOURCE_VERSION,
    projections=(PDFPageTextEvidenceProjection(),),
)


async def search_pdf_pages(
    memory: Any,
    question: str,
    limit: int = 10,
) -> tuple[Any, ...]:
    """Return exact page citations for one natural-language question.

    Several offline query formulations are issued against the same scoped
    Memory and their rankings are fused, so a question phrased with filler words
    still reaches pages that only share its rare terms or figures.
    """

    from powercontext.builtin.runtime.models import SearchMemoryRequest

    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise ValueError("limit must be a positive integer")  # noqa: TRY003
    if not isinstance(question, str) or not question.strip():
        raise ValueError("question must be a non-empty string")  # noqa: TRY003

    fused: dict[str, float] = {}
    citations: dict[str, Any] = {}
    per_query = min(100, max(limit * 4, 40))
    for variant in _query_variants(question):
        page = await memory.search(SearchMemoryRequest(query=variant, mode="fts", limit=per_query))
        for rank, hit in enumerate(page.hits, start=1):
            key = hit.entry_id
            fused[key] = fused.get(key, 0.0) + 1.0 / (60 + rank)
            citations.setdefault(key, _citation(hit))

    ordered = sorted(fused, key=lambda key: (-fused[key], key))
    return tuple(citations[key] for key in ordered[:limit])


def _citation(hit: Any) -> Any:
    from powercontext.builtin.artifacts.memory.models import MemoryCitation

    return MemoryCitation(
        memory_ref=hit.memory_ref,
        entry_id=hit.entry_id,
        entry_version_id=hit.entry_version_id,
    )


def _query_variants(question: str) -> tuple[str, ...]:
    normalized = " ".join(question.split())
    variants = [normalized]
    words = [word for word in _WORD.findall(normalized)]
    content = [word for word in words if word.casefold() not in _STOPWORDS]
    if content:
        keywords = " ".join(dict.fromkeys(content))
        if keywords and keywords != normalized:
            variants.append(keywords)
        figures = [word for word in content if any(char.isdigit() for char in word)]
        if figures and len(figures) < len(content):
            variants.append(" ".join(dict.fromkeys(figures)))
    return tuple(dict.fromkeys(variants))


def _open_reader(content: bytes) -> Any:
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(content), strict=False)
    except Exception as error:  # noqa: BLE001 - any unreadable file is a validation error
        raise ValueError(f"pdf document is not readable: {error}") from error
    if reader.is_encrypted:
        try:
            unlocked = reader.decrypt("")
        except Exception as error:  # noqa: BLE001 - an unusable password is a validation error
            raise ValueError(f"pdf document cannot be read without a password: {error}") from error
        if not unlocked:
            raise ValueError("pdf document requires a non-empty password")  # noqa: TRY003
    return reader


def _pages(pages: Sequence[Any]) -> Sequence[Any]:
    return pages


__all__ = [
    "PDFDocumentInput",
    "PDFPageInput",
    "PDFPageSource",
    "PDF_PAGE_SOURCE_DEFINITION",
    "parse_pdf_document",
    "pdf_page_source_id",
    "search_pdf_pages",
]
