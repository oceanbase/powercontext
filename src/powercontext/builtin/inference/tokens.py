"""Deployment-fixed token estimation for context accounting."""

from __future__ import annotations

from collections.abc import Callable

from pydantic import BaseModel, ConfigDict, Field

_CHARACTER_UNITS_PER_TOKEN = 4
_NON_ASCII_CHARACTER_UNITS = 4


class TokenEstimatorProfile(BaseModel):
    """Stable identity for one tokenization policy."""

    model_config = ConfigDict(frozen=True)

    estimator_id: str = Field(min_length=1, max_length=128)
    version: str = Field(min_length=1, max_length=64)


class TokenEstimator:
    """Count tokens with one deployment-fixed profile."""

    def __init__(self, profile: TokenEstimatorProfile, count: Callable[[str], int], /) -> None:
        self.profile = profile
        self._count = count

    def estimate(self, text: str, /) -> int:
        """Return a validated non-negative token estimate for one complete text."""

        tokens = self._count(text)
        if not isinstance(tokens, int) or isinstance(tokens, bool) or tokens < 0:
            raise ValueError("token estimator must return a non-negative integer")  # noqa: TRY003
        return tokens


def character_token_estimator() -> TokenEstimator:
    """Estimate tokens without loading a model-specific tokenizer.

    ASCII text uses the common approximation of four characters per token.
    Each non-ASCII character counts as one token to avoid systematically
    underestimating CJK-heavy content.
    """

    def count(text: str) -> int:
        units = sum(1 if character.isascii() else _NON_ASCII_CHARACTER_UNITS for character in text)
        return (units + _CHARACTER_UNITS_PER_TOKEN - 1) // _CHARACTER_UNITS_PER_TOKEN

    return TokenEstimator(
        TokenEstimatorProfile(
            estimator_id="character:weighted",
            version="1",
        ),
        count,
    )


__all__ = ["TokenEstimator", "TokenEstimatorProfile", "character_token_estimator"]
