
# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0.
"""Captured, row-level CSV sources with stable identity and text evidence."""

from __future__ import annotations

import csv
import io
import json
from typing import ClassVar
from urllib.parse import quote

from pydantic import BaseModel, model_validator

from powercontext.sources.models import Source, SourceMaterialization
from powercontext.sources.projections import TextEvidence

_SOURCE_NAME = "csv-row"
_MAX_ROW_SOURCE_ID_LENGTH = 256


class CSVRowValidationError(ValueError):
    """Validation failure for a captured CSV row or document."""

    def __init__(self, message: str) -> None:
        super().__init__(message)


class CSVRowNameError(CSVRowValidationError):
    def __init__(self) -> None:
        super().__init__("CSV row Source name must be 'csv-row'")


class CSVRowMaterializationError(CSVRowValidationError):
    def __init__(self) -> None:
        super().__init__("CSV row Source must be captured")


class CSVRowColumnCountError(CSVRowValidationError):
    def __init__(self) -> None:
        super().__init__("CSV column and value counts differ")


class CSVRowIdentityError(CSVRowValidationError):
    def __init__(self) -> None:
        super().__init__("CSV row Source ID does not match row identity")


class EmptyDocumentIDError(CSVRowValidationError):
    def __init__(self) -> None:
        super().__init__("document_id must not be empty")


class EmptyPrimaryKeyValueError(CSVRowValidationError):
    def __init__(self) -> None:
        super().__init__("primary_key_value must not be empty")


class SourceIDTooLongError(CSVRowValidationError):
    def __init__(self) -> None:
        super().__init__("CSV row Source ID exceeds 256 characters")


class EmptyPrimaryKeyError(CSVRowValidationError):
    def __init__(self) -> None:
        super().__init__("primary_key must not be empty")


class InvalidCSVEncodingError(CSVRowValidationError):
    def __init__(self) -> None:
        super().__init__("CSV content must be valid UTF-8")


class MissingCSVHeaderError(CSVRowValidationError):
    def __init__(self) -> None:
        super().__init__("CSV document has no header")


class InvalidCSVHeaderError(CSVRowValidationError):
    def __init__(self) -> None:
        super().__init__("Invalid CSV header")


class EmptyCSVHeaderError(CSVRowValidationError):
    def __init__(self) -> None:
        super().__init__("CSV headers must be non-empty")


class DuplicateCSVHeaderError(CSVRowValidationError):
    def __init__(self) -> None:
        super().__init__("CSV headers must be unique")


class MissingPrimaryKeyColumnError(CSVRowValidationError):
    def __init__(self) -> None:
        super().__init__("primary_key must exactly match a CSV header")


class CSVRowWidthError(CSVRowValidationError):
    def __init__(self) -> None:
        super().__init__("CSV row has a different column count")


class DuplicatePrimaryKeyError(CSVRowValidationError):
    def __init__(self) -> None:
        super().__init__("CSV contains duplicate primary-key values")


class MalformedCSVError(CSVRowValidationError):
    def __init__(self) -> None:
        super().__init__("Malformed CSV content")


class CSVDocumentInput(BaseModel):
    document_id: str
    primary_key: str
    content: bytes


class CSVRowInput(BaseModel):
    document_id: str
    primary_key: str
    primary_key_value: str
    columns: tuple[str, ...]
    values: tuple[str, ...]


class CSVRowSource(Source):
    source_id: str
    document_id: str
    primary_key: str
    primary_key_value: str
    columns: tuple[str, ...]
    values: tuple[str, ...]

    @model_validator(mode="after")
    def validate_row(self) -> CSVRowSource:
        if self.name != self.source_id:
            raise CSVRowIdentityError()
        if self.materialization != SourceMaterialization.CAPTURED:
            raise CSVRowMaterializationError()
        if len(self.columns) != len(self.values):
            raise CSVRowColumnCountError()
        if self.source_id != csv_row_source_id(
            self.document_id, self.primary_key_value
        ):
            raise CSVRowIdentityError()
        return self


def csv_row_source_id(document_id: str, primary_key_value: str) -> str:
    """Build a stable, length-bounded row ID from identity strings."""
    if (
        not isinstance(document_id, str)
        or not document_id
        or document_id != document_id.strip()
    ):
        raise EmptyDocumentIDError()
    if not isinstance(primary_key_value, str) or not primary_key_value.strip():
        raise EmptyPrimaryKeyValueError()

    source_id = (
        f"csv:{quote(document_id, safe='')}:"
        f"{quote(primary_key_value, safe='')}"
    )
    if len(source_id) > _MAX_ROW_SOURCE_ID_LENGTH:
        raise SourceIDTooLongError()
    return source_id


def _decode_csv_content(content: bytes) -> str:
    """Decode UTF-8 CSV content, accepting a leading BOM."""
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise InvalidCSVEncodingError() from error


def _read_csv_headers(reader: csv.reader, primary_key: str) -> tuple[str, ...]:
    """Read and validate the CSV header."""
    try:
        columns = next(reader)
    except StopIteration as error:
        raise MissingCSVHeaderError() from error
    except csv.Error as error:
        raise InvalidCSVHeaderError() from error

    if not columns or any(not column.strip() for column in columns):
        raise EmptyCSVHeaderError()
    if len(set(columns)) != len(columns):
        raise DuplicateCSVHeaderError()
    if primary_key not in columns:
        raise MissingPrimaryKeyColumnError()

    return tuple(columns)


def _collect_csv_rows(
    reader: csv.reader,
    value: CSVDocumentInput,
    names: tuple[str, ...],
) -> tuple[CSVRowInput, ...]:
    """Validate rows and reject duplicate identities."""
    key_index = names.index(value.primary_key)
    rows: list[CSVRowInput] = []
    identities: set[str] = set()

    try:
        for parsed in reader:
            if not parsed:
                continue

            if len(parsed) != len(names):
                raise CSVRowWidthError()

            key_value = parsed[key_index]
            source_id = csv_row_source_id(value.document_id, key_value)

            if source_id in identities:
                raise DuplicatePrimaryKeyError()

            identities.add(source_id)
            rows.append(
                CSVRowInput(
                    document_id=value.document_id,
                    primary_key=value.primary_key,
                    primary_key_value=key_value,
                    columns=names,
                    values=tuple(parsed),
                )
            )
    except csv.Error as error:
        raise MalformedCSVError() from error

    return tuple(rows)


def parse_csv_document(value: CSVDocumentInput) -> tuple[CSVRowInput, ...]:
    """Validate the whole document before returning any row input."""
    if (
        not value.document_id
        or value.document_id != value.document_id.strip()
    ):
        raise EmptyDocumentIDError()
    if not value.primary_key or not value.primary_key.strip():
        raise EmptyPrimaryKeyError()

    text = _decode_csv_content(value.content)
    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    names = _read_csv_headers(reader, value.primary_key)

    return _collect_csv_rows(reader, value, names)


class _CSVRowTextEvidenceProjection:
    name: ClassVar[str] = "powercontext.text-evidence"
    version: ClassVar[str] = "1"
    source_class: ClassVar[type[CSVRowSource]] = CSVRowSource
    output_class: ClassVar[type[BaseModel]] = TextEvidence

    def project(self, source: CSVRowSource) -> TextEvidence:
        row = dict(zip(source.columns, source.values, strict=True))
        return TextEvidence(
            source_type=_SOURCE_NAME,
            source_id=source.source_id,
            content=json.dumps(
                row, ensure_ascii=False, separators=(",", ":")
            ),
            metadata={
                "document_id": source.document_id,
                "primary_key": source.primary_key,
                "primary_key_value": source.primary_key_value,
                "columns": list(source.columns),
                "row": row,
            },
        )


class _CSVRowSourceDefinition:
    name: ClassVar[str] = _SOURCE_NAME
    version: ClassVar[str] = "1"
    input_class: ClassVar[type[CSVRowInput]] = CSVRowInput
    source_class: ClassVar[type[CSVRowSource]] = CSVRowSource
    projections = (_CSVRowTextEvidenceProjection(),)

    async def resolve(self, value: CSVRowInput, /) -> CSVRowSource:
        source_id = csv_row_source_id(
            value.document_id, value.primary_key_value
        )
        return CSVRowSource(
            name=source_id,
            definition_version=self.version,
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


CSV_ROW_SOURCE_DEFINITION = _CSVRowSourceDefinition()

__all__ = [
    "CSV_ROW_SOURCE_DEFINITION",
    "CSVDocumentInput",
    "CSVRowInput",
    "CSVRowSource",
    "csv_row_source_id",
    "parse_csv_document",
]
