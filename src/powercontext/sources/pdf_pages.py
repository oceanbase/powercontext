"""PDF page parsing, stable page Sources, and exact Memory citations."""

from __future__ import annotations

import hashlib
import io
import json
import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

from pypdf import PdfReader

from powercontext.artifacts import MemoryCitation
from powercontext.builtin.runtime import (
    GetMemoryEntryRequest,
    SearchMemoryRequest,
)
from powercontext.sources.definitions import AdapterSourceDefinition
from powercontext.sources.models import Source, SourceMaterialization
from powercontext.sources.projections import (
    TEXT_EVIDENCE_PROJECTION_KEY,
    TextEvidence,
)


class PDFDocumentInput(BaseModel):
    model_config = ConfigDict(frozen=True)

    document_id: str
    content: bytes

    @field_validator("document_id")
    @classmethod
    def validate_document_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("document_id must not be blank")
        if value != value.strip():
            raise ValueError("document_id must be trimmed")
        return value


class PDFPageInput(BaseModel):
    model_config = ConfigDict(frozen=True)

    document_id: str
    document_sha256: str
    page_number: int = Field(ge=1)
    text: str

    @field_validator("document_id")
    @classmethod
    def validate_document_id(cls, value: str) -> str:
        if not value.strip() or value != value.strip():
            raise ValueError("invalid document_id")
        return value

    @field_validator("document_sha256")
    @classmethod
    def validate_sha256(cls, value: str) -> str:
        if not re.fullmatch(r"[0-9a-f]{64}", value):
            raise ValueError("invalid document SHA256")
        return value


class PDFPageSource(Source):
    document_id: str
    document_sha256: str
    page_number: int = Field(ge=1)
    text: str


def parse_pdf_document(
    value: PDFDocumentInput,
) -> tuple[PDFPageInput, ...]:
    """Extract every physical PDF page atomically."""

    raw = value.content

    if not raw:
        raise ValueError("PDF content must not be empty")

    if b"%PDF-" not in raw[:1024]:
        raise ValueError("input is not a PDF document")

    digest = hashlib.sha256(raw).hexdigest()

    try:
        reader = PdfReader(io.BytesIO(raw), strict=False)

        if reader.is_encrypted:
            if not reader.decrypt(""):
                raise ValueError("PDF requires a non-empty password")

        pages = []

        for index, page in enumerate(reader.pages, start=1):
            text = page.extract_text() or ""

            pages.append(
                PDFPageInput(
                    document_id=value.document_id,
                    document_sha256=digest,
                    page_number=index,
                    text=text,
                )
            )

        if not pages:
            raise ValueError("PDF contains no physical pages")

        return tuple(pages)

    except ValueError:
        raise
    except Exception as error:
        raise ValueError("PDF parsing failed") from error


class PDFPageTextEvidenceProjection:
    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = PDFPageSource
    output_class = TextEvidence

    def project(self, source: PDFPageSource, /) -> TextEvidence:
        return TextEvidence(
            source_type="pdf-page",
            source_id=source.name,
            content=f"{source.document_id}\n{source.text}",
            metadata={
                "document_id": source.document_id,
                "document_sha256": source.document_sha256,
                "page_number": source.page_number,
            },
        )


class PDFPageSourceAdapter:
    input_class = PDFPageInput
    name = "pdf-page"
    source_class = PDFPageSource

    async def resolve(
        self,
        value: PDFPageInput,
        /,
    ) -> PDFPageSource:
        identity = json.dumps(
            [
                value.document_id,
                value.document_sha256,
                value.page_number,
            ],
            ensure_ascii=False,
            separators=(",", ":"),
        )

        identity_hash = hashlib.sha256(
            identity.encode("utf-8")
        ).hexdigest()

        return PDFPageSource(
            name=f"pdf-page:{identity_hash}",
            definition_version="1",
            materialization=SourceMaterialization.CAPTURED,
            document_id=value.document_id,
            document_sha256=value.document_sha256,
            page_number=value.page_number,
            text=value.text,
        )

    async def read(
        self,
        source: PDFPageSource,
        /,
    ) -> PDFPageInput:
        return PDFPageInput(
            document_id=source.document_id,
            document_sha256=source.document_sha256,
            page_number=source.page_number,
            text=source.text,
        )


PDF_PAGE_SOURCE_ADAPTER = PDFPageSourceAdapter()

PDF_PAGE_SOURCE_DEFINITION = AdapterSourceDefinition(
    adapter=PDF_PAGE_SOURCE_ADAPTER,
    version="1",
    projections=(PDFPageTextEvidenceProjection(),),
)


async def search_pdf_pages(
    memory,
    question: str,
    limit: int = 10,
) -> tuple[MemoryCitation, ...]:
    """Search scoped Memory and return exact, distinct PDF page citations."""

    if not isinstance(question, str) or not question.strip():
        raise ValueError("question must not be blank")

    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise ValueError("limit must be a positive integer")

    result = await memory.search(
        SearchMemoryRequest(
            query=question.strip(),
            limit=limit,
            mode="fts",
        )
    )

    citations = []
    seen_pages = set()

    for hit in result.hits:
        citation = MemoryCitation(
            memory_ref=hit.memory_ref,
            entry_id=hit.entry_id,
            entry_version_id=hit.entry_version_id,
        )

        record = await memory.get(
            GetMemoryEntryRequest(citation=citation)
        )

        page_refs = [
            source.source_id
            for source in record.entry.sources
            if source.source_type == "pdf-page"
        ]

        # One citation must identify exactly one physical page.
        if len(page_refs) != 1:
            continue

        page_id = page_refs[0]

        if page_id in seen_pages:
            continue

        seen_pages.add(page_id)
        citations.append(citation)

        if len(citations) >= limit:
            break

    return tuple(citations)
