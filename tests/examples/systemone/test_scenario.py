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

"""Generated code is judged by independent execution, including denied execution."""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from typing import Any

import pytest

from examples.systemone.scenario import verify_code

_VALID = """\
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation

def cents(text: str) -> int:
    try:
        amount = Decimal(text)
        if not amount.is_finite():
            raise ValueError("finite amount required")
        return int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    except InvalidOperation as error:
        raise ValueError("invalid amount") from error
"""


def test_actual_decimal_results_and_saved_source(tmp_path: Path) -> None:
    result = asyncio.run(verify_code(_VALID, tmp_path))

    assert result["passed"] == result["total"] == 10
    assert result["exit_code"] == 0
    assert result["pid"] > 0
    assert result["code_sha256"] == hashlib.sha256(_VALID.encode()).hexdigest()
    assert (tmp_path / "amount.py").read_text() == _VALID
    checks = {item["input"]: item for item in result["checks"]}
    assert checks["-1.005"]["actual"] == -101
    assert checks["NaN"]["actual"] == "ValueError"


def test_float_baseline_reports_real_rounding_and_validation_failures(tmp_path: Path) -> None:
    code = "def cents(text: str) -> int:\n    return round(float(text) * 100)\n"
    result = asyncio.run(verify_code(code, tmp_path))

    assert 0 < result["passed"] < result["total"]
    assert result["exit_code"] == 1
    checks = {item["input"]: item for item in result["checks"]}
    assert checks["1.005"]["actual"] == 100
    assert checks["1.005"]["passed"] is False
    assert checks["2.685"]["actual"] == 268
    assert checks["Infinity"]["actual"] == "OverflowError"


def test_broad_exception_handler_preserves_real_generated_amount_results(tmp_path: Path) -> None:
    code = """\
from decimal import Decimal, ROUND_HALF_UP

def cents(text: str) -> int:
    try:
        d = Decimal(text)
    except Exception:
        raise ValueError("invalid amount")
    if d.is_nan() or d.is_infinite():
        raise ValueError("invalid amount")
    return int((d * 100).to_integral_value(rounding=ROUND_HALF_UP))
"""
    result = asyncio.run(verify_code(code, tmp_path))

    assert result["passed"] == result["total"] == 10
    assert result["exit_code"] == 0
    assert (tmp_path / "amount.py").read_text() == code


def test_encoding_comments_cannot_change_the_validated_program(tmp_path: Path) -> None:
    code = '# coding: utf-7\n# +AAo-raise RuntimeError("encoding switched")+AAo-\n' + _VALID
    result = asyncio.run(verify_code(code, tmp_path))

    assert result["passed"] == result["total"] == 10
    assert result["exit_code"] == 0


@pytest.mark.parametrize(
    "code",
    [
        "import os\ndef cents(text):\n    return 1\n",
        "def cents(text):\n    return open(text).read()\n",
        "def cents(text):\n    return text.__class__\n",
        "def cents(text):\n    while True:\n        pass\n",
        "def cents(text):\n    return cents(text)\n",
        "from decimal import *\ndef cents(text):\n    return 1\n",
        "def cents(text):\n    return 10 ** 10000000\n",
        "print('run me')\ndef cents(text):\n    return 1\n",
        "def cents(text):\n    return [x for x in text]\n",
        "def cents(text):\n    int = open\n    return int(text)\n",
        "def cents(text):\n    reader = open\n    return reader\n",
        "def cents(text):\n    Exception = ValueError\n    return 1\n",
        "def cents(text):\n    return Exception(text)\n",
    ],
)
def test_unsupported_programs_are_saved_without_execution(code: str, tmp_path: Path) -> None:
    result = asyncio.run(verify_code(code, tmp_path))

    assert result["passed"] == 0
    assert result["pid"] == 0
    assert result["exit_code"] is None
    assert result["checks"] == []
    assert result["error"].startswith("Code was not executed:")
    assert (tmp_path / "amount.py").read_text() == code


def test_accepted_but_broken_function_reports_execution_failure(tmp_path: Path) -> None:
    code = "def cents(text: str) -> int:\n    return 1 // 0\n"
    result = asyncio.run(verify_code(code, tmp_path))

    assert result["passed"] == 0
    assert result["pid"] > 0
    assert result["exit_code"] == 1
    assert all(item["actual"] == "ZeroDivisionError" for item in result["checks"])


def test_cancelled_verification_reaps_the_real_child(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    original_spawn = asyncio.create_subprocess_exec
    children: list[asyncio.subprocess.Process] = []

    async def scenario() -> None:
        started = asyncio.Event()

        async def spawn(*args: Any, **kwargs: Any) -> asyncio.subprocess.Process:
            child = await original_spawn(*args, **kwargs)
            children.append(child)
            started.set()
            return child

        monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
        task = asyncio.create_task(verify_code(_VALID, tmp_path))
        await asyncio.wait_for(started.wait(), timeout=5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    assert children
    assert all(child.returncode is not None for child in children)
