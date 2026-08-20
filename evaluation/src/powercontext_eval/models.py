"""Core immutable evaluation models."""

from enum import StrEnum
from re import fullmatch
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, model_validator


class Arm(StrEnum):
    """An evaluation arm."""

    OFF = "off"
    ON = "on"


class PowerContextRef(BaseModel):
    """An explicit, immutable PowerContext source reference."""

    model_config = ConfigDict(frozen=True, strict=True)

    kind: Literal["latest", "branch", "tag", "commit"]
    value: str | None = None

    @model_validator(mode="after")
    def validate_kind_value(self) -> Self:
        """Enforce the invariant shared by every construction path."""

        if self.kind == "latest":
            if self.value is not None:
                raise ValueError("Latest refs must not have a value")
            return self
        if not self.value:
            raise ValueError(f"{self.kind.title()} refs require a value")
        if self.kind == "commit" and fullmatch(r"[0-9a-fA-F]{40}", self.value) is None:
            raise ValueError("Commit refs must contain exactly 40 hexadecimal characters")
        return self

    @classmethod
    def parse(cls, raw: str) -> Self:
        """Parse an explicit reference without guessing ambiguous values."""

        if not isinstance(raw, str) or not raw:
            raise ValueError("PowerContext ref must not be empty")
        if raw == "latest":
            return cls(kind="latest")

        kind, separator, value = raw.partition(":")
        if separator != ":" or kind not in {"branch", "tag", "commit"} or not value:
            raise ValueError(f"Invalid PowerContext ref: {raw!r}")
        if kind == "commit" and fullmatch(r"[0-9a-fA-F]{40}", value) is None:
            raise ValueError("Commit refs must contain exactly 40 hexadecimal characters")

        return cls(kind=kind, value=value)
