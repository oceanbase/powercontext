from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from typing import Tuple
from urllib.parse import quote

from powercontext.sources import Source, SourceDefinition, CAPTURED
from powercontext.evidence import TextEvidence
from powercontext.sources.registry import SourceDefinitionRegistry


@dataclass(frozen=True)
class CSVRowInput:
    document_id: str
    primary_key: str
    primary_key_value: str
    columns: Tuple[str, ...]
    values: Tuple[str, ...]


def parse_csv_document(
    raw_bytes: bytes,
    document_id: str,
    primary_key: str
) -> Tuple[CSVRowInput, ...]:
    try:
        text = raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise ValueError("invalid utf-8 encoding") from e

    reader = csv.reader(
        text.splitlines(),
        delimiter=",",
        quotechar='"',
        strict=True
    )
    rows = list(reader)
    if not rows:
        raise ValueError("empty csv file")

    header = rows[0]
    for col_name in header:
        if len(col_name.strip()) == 0:
            raise ValueError("header contains blank column name")
    if len(set(header)) != len(header):
        raise ValueError("duplicate column name in header")
    if primary_key not in header:
        raise ValueError(f"primary key {primary_key!r} not found in header")

    pk_idx = header.index(primary_key)
    result_list: list[CSVRowInput] = []
    seen_pk_values: set[str] = set()

    for row in rows[1:]:
        all_blank = all(len(cell.strip()) == 0 for cell in row)
        if all_blank:
            continue
        if len(row) != len(header):
            raise ValueError("row length mismatches header width")

        pk_val = row[pk_idx]
        if len(pk_val.strip()) == 0:
            raise ValueError("primary key value is blank")
        if pk_val in seen_pk_values:
            raise ValueError(f"duplicate primary key value: {pk_val!r}")
        seen_pk_values.add(pk_val)

        row_input = CSVRowInput(
            document_id=document_id,
            primary_key=primary_key,
            primary_key_value=pk_val,
            columns=tuple(header),
            values=tuple(row)
        )
        result_list.append(row_input)

    return tuple(result_list)


class CSVRowSource(Source):
    materialization = CAPTURED

    def __init__(self, inp: CSVRowInput):
        self.input = inp
        part1 = quote(inp.document_id, safe="")
        part2 = quote(f"{inp.primary_key}={inp.primary_key_value}", safe="")
        identity = f"{part1}:{part2}"
        if len(identity) > 256:
            raise ValueError("source_id exceeds 256 char limit")
        self.identity = identity
        self.name = identity
        self.source_id = identity

    def evidence(self) -> TextEvidence:
        row_dict = dict(zip(self.input.columns, self.input.values))
        content = json.dumps(row_dict, ensure_ascii=False, separators=(",", ":"))
        metadata = {
            "document_id": self.input.document_id,
            "primary_key": self.input.primary_key,
            "primary_key_value": self.input.primary_key_value,
            "columns": list(self.input.columns),
            "row": row_dict
        }
        return TextEvidence(
            source_type="csv-row",
            source_id=self.identity,
            content=content,
            metadata=metadata
        )


CSV_ROW_DEFINITION = SourceDefinition(
    name="csv-row",
    version=1,
    input_class=CSVRowInput,
    source_class=CSVRowSource
)

SourceDefinitionRegistry.register(CSV_ROW_DEFINITION)
