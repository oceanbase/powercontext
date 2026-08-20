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

import pytest
from pydantic import ValidationError

from powercontext_eval.models import Arm, PowerContextRef


@pytest.mark.parametrize(
    ("raw", "kind", "value"),
    [
        ("latest", "latest", None),
        ("branch:main", "branch", "main"),
        ("tag:v0.1.0", "tag", "v0.1.0"),
        ("commit:0123456789abcdef0123456789abcdef01234567", "commit", "0123456789abcdef0123456789abcdef01234567"),
    ],
)
def test_powercontext_ref_parse_accepts_explicit_refs(raw: str, kind: str, value: str | None) -> None:
    ref = PowerContextRef.parse(raw)

    assert ref.kind == kind
    assert ref.value == value


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "main",
        "commit:0123456",
    ],
)
def test_powercontext_ref_parse_rejects_ambiguous_or_invalid_refs(raw: str) -> None:
    with pytest.raises(ValueError):
        PowerContextRef.parse(raw)


def test_powercontext_ref_is_frozen() -> None:
    ref = PowerContextRef.parse("latest")

    with pytest.raises(ValidationError):
        ref.value = "main"  # ty: ignore[invalid-assignment]


def test_powercontext_ref_strictly_rejects_non_string_values() -> None:
    with pytest.raises(ValidationError) as exc_info:
        PowerContextRef(kind="branch", value=123)  # ty: ignore[invalid-argument-type]

    [error] = exc_info.value.errors()
    assert error["loc"] == ("value",)
    assert error["type"] == "string_type"

    with pytest.raises(ValidationError):
        PowerContextRef(kind="branch", value=b"main")  # ty: ignore[invalid-argument-type]


@pytest.mark.parametrize(
    "payload",
    [
        {"kind": "latest", "value": "main"},
        {"kind": "branch"},
        {"kind": "tag"},
        {"kind": "commit", "value": "0123456"},
        {"kind": "commit"},
    ],
)
def test_powercontext_ref_constructor_rejects_invalid_kind_value_combinations(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        PowerContextRef(**payload)  # ty: ignore[invalid-argument-type]


@pytest.mark.parametrize(
    "payload",
    [
        {"kind": "latest", "value": "main"},
        {"kind": "branch"},
        {"kind": "tag"},
        {"kind": "commit", "value": "0123456"},
        {"kind": "commit"},
    ],
)
def test_powercontext_ref_model_validate_rejects_invalid_kind_value_combinations(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        PowerContextRef.model_validate(payload)


def test_arm_values_are_stable_strings() -> None:
    assert Arm.OFF == "off"
    assert Arm.ON == "on"
    assert str(Arm.OFF) == "off"
    assert str(Arm.ON) == "on"
