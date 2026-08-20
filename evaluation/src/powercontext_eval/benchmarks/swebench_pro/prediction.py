"""Official SWE-bench Pro prediction encoding."""

from __future__ import annotations

import json

from powercontext_eval.errors import PowerContextEvalError


class BinaryPatchError(PowerContextEvalError):
    """The MVP does not accept binary Git patches."""


def encode_predictions(instance_id: str, patch: str, prefix: str) -> str:
    """Encode exactly the official single-instance JSON array."""

    if not all(isinstance(value, str) and value for value in (instance_id, prefix)):
        raise ValueError("Prediction identity and prefix must be non-empty strings")
    if not isinstance(patch, str):
        raise TypeError("Patch must be text")
    return json.dumps(
        [{"instance_id": instance_id, "patch": patch, "prefix": prefix}],
        ensure_ascii=False,
        separators=(",", ":"),
    )
