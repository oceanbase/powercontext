from __future__ import annotations
import csv
import json
from dataclasses import dataclass
from typing import Tuple
from urllib.parse import quote
from io import StringIO

from powercontext.sources import Source, SourceDefinition, CAPTURED
from powercontext.evidence import TestEvidence
from powercontext.registry import SourceDefinitionRegistry


@dataclass(frozen=True)
class CSVReadInput:
    document_id: str
    primary_key: str
    primary_key_value: str
    columns: Tuple[str, ...]
    values: Tuple[str, ...]


def csv_rows(text: str):
    """赛题二：解析CSV字符串，严格遵循RFC4180标准，返回二维字符串列表"""
    reader = csv.reader(StringIO(text))
    return list(reader)
