from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class SourceMaterialization(StrEnum):
    """Describe where the value read for a Source comes from."""

    CAPTURED = "captured"
    REFERENCED = "referenced"


@dataclass(frozen=True, slots=True, kw_only=True)
class Source:
    """Base value for an adapter-owned Source description."""

    name: str
    materialization: SourceMaterialization
    description: str | None = None
