from __future__ import annotations
import csv
import json
from dataclasses import dataclass
from typing import Tuple
from urllib.parse import quote
from io import StringIO

from powercontext.sources import Source, SourceDefinition, CAPTURED
from powercontext.evidence import TextEvidence
from powercontext.registry import SourceDefinitionRegistry


@dataclass(frozen=True)
class CSVRowInput:
    document_id: str
    primary_key: str
    primary_key_value: str
    columns: Tuple[str, ...]
    values: Tuple[str, ...]


class CSVRowSource(Source):
    def __init__(self, input: CSVRowInput):
        self.input = input

    def to_evidence(self) -> TextEvidence:
        row_dict = dict(zip(self.input.columns, self.input.values))
        content = json.dumps(
            {
                "document_id": self.input.document_id,
                "primary_key": self.input.primary_key,
                "primary_key_value": self.input.primary_key_value,
                "columns": list(self.input.columns),
                "row": row_dict
            },
            ensure_ascii=False
        )
        metadata = {
            "source_type": "csv_row",
            "identity": f"{self.input.document_id}/{quote(self.input.primary_key_value)}"
        }
        return TextEvidence(
            source_type="csv_row",
            source_id=self.input.document_id,
            content=content,
            metadata=metadata
        )


CSV_ROW_DEFINITION = SourceDefinition(
    name="csv_row",
    version=1,
    input_class=CSVRowInput,
    source_class=CSVRowSource
)
SourceDefinitionRegistry.register(CSV_ROW_DEFINITION)


# ========== 赛题二 csv_rows 函数 ==========
def csv_rows(text: str):
    """
    赛题二：解析CSV字符串，严格遵循RFC4180标准
    :param text: CSV原始字符串
    :return: 二维字符串列表
    """
    reader = csv.reader(StringIO(text))
    out = []
    for line in reader:
        if line:
            out.append(line)
    return out
