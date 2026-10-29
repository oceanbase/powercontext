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

"""Row-level Source capture for offline delimiter-separated tables.

Every table row is materialized as its own ``CAPTURED`` Source so that a
retrieval citation resolves to one complete record instead of an unstable row
offset. Values are preserved exactly as read from the document: no trimming,
no type coercion, no reordering.
"""

from __future__ import annotations

import csv
import io
import json
from typing import Any, cast
from urllib.parse import quote

from pydantic import BaseModel, model_validator

from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources import TEXT_EVIDENCE_PROJECTION_KEY, AdapterSourceDefinition, TextEvidence
from powercontext.sources.models import Source, SourceMaterialization

CSV_ROW_SOURCE_NAME = "csv-row"
CSV_ROW_SOURCE_VERSION = "1"
CSV_ROW_SOURCE_ID_PREFIX = "csv"


class CSVDocumentInput(BaseModel):
    """A complete offline table document addressed by one primary-key column."""

    document_id: str
    primary_key: str
    content: bytes

    @model_validator(mode="after")
    def validate_document_input(self) -> CSVDocumentInput:
        _require_document_id(self.document_id)
        if not isinstance(self.primary_key, str) or not self.primary_key:
            raise ValueError("primary_key must be a non-empty string")  # noqa: TRY003
        return self


class CSVRowInput(BaseModel):
    """One fully decoded table row, retaining header order and raw values."""

    document_id: str
    primary_key: str
    primary_key_value: str
    columns: tuple[str, ...]
    values: tuple[str, ...]

    @model_validator(mode="after")
    def validate_row_shape(self) -> CSVRowInput:
        _require_document_id(self.document_id)
        if len(self.columns) != len(self.values):
            raise ValueError("columns and values must have the same length")  # noqa: TRY003
        if len(set(self.columns)) != len(self.columns):
            raise ValueError("columns must be unique")  # noqa: TRY003
        if self.primary_key not in self.columns:
            raise ValueError("primary_key must name one column")  # noqa: TRY003
        if not self.primary_key_value.strip():
            raise ValueError("primary_key_value must not be blank")  # noqa: TRY003
        return self


class CSVRowSource(Source):
    """Captured table row that can be read back as the exact original record."""

    source_id: str
    document_id: str
    primary_key: str
    primary_key_value: str
    columns: tuple[str, ...]
    values: tuple[str, ...]


def csv_row_source_id(document_id: str, primary_key_value: str) -> str:
    """Build the stable, delimiter-safe identity of one table row."""

    _require_document_id(document_id)
    if not isinstance(primary_key_value, str) or not primary_key_value.strip():
        raise ValueError("primary_key_value must not be blank")  # noqa: TRY003
    source_id = (
        f"{CSV_ROW_SOURCE_ID_PREFIX}:{quote(document_id, safe='')}:{quote(primary_key_value, safe='')}"
    )
    if len(source_id) > MAX_SOURCE_ID_LENGTH:
        raise ValueError(f"csv row identity must not exceed {MAX_SOURCE_ID_LENGTH} characters")  # noqa: TRY003
    return source_id


def parse_csv_document(value: CSVDocumentInput) -> tuple[CSVRowInput, ...]:
    """Decode and validate the whole document before returning any row."""

    text = _decode_utf8(value.content)
    records = _read_records(text)
    if not records:
        raise ValueError("csv document must not be empty")  # noqa: TRY003
    header = _validated_header(records[0])
    key_index = header.index(value.primary_key)
    width = len(header)

    rows: list[CSVRowInput] = []
    seen: set[str] = set()
    for record in records[1:]:
        if not record:
            continue
        if len(record) != width:
            raise ValueError("row width must match the header width")  # noqa: TRY003
        key_value = record[key_index]
        if not key_value.strip():
            raise ValueError("primary key value must not be blank")  # noqa: TRY003
        if key_value in seen:
            raise ValueError("primary key value must be unique within a document")  # noqa: TRY003
        seen.add(key_value)
        rows.append(
            CSVRowInput(
                document_id=value.document_id,
                primary_key=value.primary_key,
                primary_key_value=key_value,
                columns=header,
                values=tuple(record),
            )
        )
    return tuple(rows)


class CSVRowAdapter:
    """Resolve one row input into its captured Source and read it back."""

    input_class = CSVRowInput
    name = CSV_ROW_SOURCE_NAME
    source_class = CSVRowSource

    async def resolve(self, value: CSVRowInput, /) -> CSVRowSource:
        source_id = csv_row_source_id(value.document_id, value.primary_key_value)
        return CSVRowSource(
            name=source_id,
            definition_version=CSV_ROW_SOURCE_VERSION,
            materialization=SourceMaterialization.CAPTURED,
            source_id=source_id,
            document_id=value.document_id,
            primary_key=value.primary_key,
            primary_key_value=value.primary_key_value,
            columns=value.columns,
            values=value.values,
        )

    async def read(self, source: CSVRowSource, /) -> CSVRowInput:
        return CSVRowInput(
            document_id=source.document_id,
            primary_key=source.primary_key,
            primary_key_value=source.primary_key_value,
            columns=source.columns,
            values=source.values,
        )


def _row_object(columns: tuple[str, ...], values: tuple[str, ...]) -> dict[str, str]:
    return dict(zip(columns, values, strict=True))


class CSVRowTextEvidenceProjection:
    """Serialize one row as compact, order-preserving JSON evidence."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = CSVRowSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: CSVRowSource, /) -> TextEvidence:
        row = _row_object(source.columns, source.values)
        return TextEvidence(
            source_type=CSV_ROW_SOURCE_NAME,
            source_id=source.source_id,
            content=json.dumps(row, ensure_ascii=False, separators=(",", ":")),
            metadata={
                "document_id": source.document_id,
                "primary_key": source.primary_key,
                "primary_key_value": source.primary_key_value,
                "columns": list(source.columns),
                "row": cast(dict[str, Any], row),
            },
        )


CSV_ROW_SOURCE_ADAPTER = CSVRowAdapter()
CSV_ROW_SOURCE_DEFINITION = AdapterSourceDefinition(
    CSV_ROW_SOURCE_ADAPTER,
    version=CSV_ROW_SOURCE_VERSION,
    projections=(CSVRowTextEvidenceProjection(),),
)


def _require_document_id(document_id: object) -> None:
    if not isinstance(document_id, str) or not document_id:
        raise ValueError("document_id must be a non-empty string")  # noqa: TRY003
    if document_id != document_id.strip():
        raise ValueError("document_id must not contain leading or trailing whitespace")  # noqa: TRY003


def _decode_utf8(content: bytes) -> str:
    if not isinstance(content, (bytes, bytearray, memoryview)):
        raise ValueError("csv content must be bytes")  # noqa: TRY003
    try:
        # ``utf-8-sig`` accepts one optional byte-order mark and stays strict otherwise.
        return bytes(content).decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError("csv content must be valid UTF-8") from error


def _read_records(text: str) -> list[list[str]]:
    try:
        return list(csv.reader(io.StringIO(text, newline=""), strict=True))
    except csv.Error as error:
        raise ValueError(f"malformed csv document: {error}") from error


def _validated_header(record: list[str]) -> tuple[str, ...]:
    if not record:
        raise ValueError("csv document must start with a header record")  # noqa: TRY003
    for field in record:
        if not field.strip():
            raise ValueError("header fields must not be blank")  # noqa: TRY003
    if len(set(record)) != len(record):
        raise ValueError("header fields must be unique")  # noqa: TRY003
    return tuple(record)


__all__ = [
    "CSV_ROW_SOURCE_DEFINITION",
    "CSVRowInput",
    "CSVRowSource",
    "CSVDocumentInput",
    "csv_row_source_id",
    "parse_csv_document",
]
