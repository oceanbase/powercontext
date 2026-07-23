from __future__ import annotations

import subprocess
import sys
from dataclasses import FrozenInstanceError

import pytest

import powercontext
from powercontext.inference import (
    EmbeddingResult,
    GenerationResult,
    InferenceError,
    InferenceUsage,
)


def test_core_package_exposes_framework_neutral_inference_api() -> None:
    assert {
        "EmbeddingModel",
        "EmbeddingResult",
        "GenerationResult",
        "InferenceError",
        "StructuredGenerator",
    } <= set(powercontext.__all__)
    assert issubclass(InferenceError, powercontext.PowerContextError)


def test_core_import_does_not_import_optional_pydantic_ai_dependency() -> None:
    process = subprocess.run(  # noqa: S603 - command arguments are fixed test literals.
        [
            sys.executable,
            "-c",
            "import sys; import powercontext; assert 'pydantic_ai' not in sys.modules",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert process.returncode == 0, process.stderr


def test_inference_values_are_immutable_and_keep_stable_result_shapes() -> None:
    usage = InferenceUsage(requests=1, input_tokens=10, output_tokens=2)
    generation = GenerationResult(output={"answer": 42}, usage=usage)
    embedding = EmbeddingResult(vectors=((1.0, 0.0),), usage=usage)

    assert generation.output == {"answer": 42}
    assert embedding.vectors == ((1.0, 0.0),)
    with pytest.raises(FrozenInstanceError):
        usage.requests = 2  # ty: ignore[invalid-assignment]
