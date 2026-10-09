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

"""The invoice task and independent checks for the generated amount function.

The AST gate intentionally accepts only a small amount-parser language. Together
with an isolated interpreter, resource limits and a credential-free environment,
this limits the local demonstration's execution surface. It is not a general
Python sandbox and must not be exposed as a public code execution service.
"""

# ruff: noqa: RUF001

from __future__ import annotations

import ast
import asyncio
import hashlib
import json
import os
import sys
from contextlib import suppress
from pathlib import Path
from typing import Any

POLICY = (
    "invoice-rounding：账单导出的金额字符串必须用 Decimal 解析，按 ROUND_HALF_UP 四舍五入转换为整数分；"
    "退款负数使用同样的对称舍入规则。非法字符串、NaN、Infinity 必须抛出 ValueError。"
)
TASK = "请实现 cents(text: str) -> int，将金额文本转换为整数分，用于账单导出。只返回 amount.py 的 Python 源码。"
SCENARIO = {"title": "新同事接手账单导出", "task": TASK, "policy": POLICY, "query": "invoice-rounding"}

# These expectations belong to the example, never to generated tests or the judge.
_CASES: tuple[tuple[str, int | str], ...] = (
    ("0.01", 1),
    ("1.005", 101),
    ("2.675", 268),
    ("2.685", 269),
    ("-1.005", -101),
    ("19.995", 2000),
    ("invalid", "ValueError"),
    ("NaN", "ValueError"),
    ("Infinity", "ValueError"),
    ("-Infinity", "ValueError"),
)
_DECIMAL_NAMES = frozenset({
    "Decimal",
    "DecimalException",
    "InvalidOperation",
    "ROUND_HALF_UP",
    "ROUND_HALF_EVEN",
    "ROUND_DOWN",
    "ROUND_UP",
})
_EXCEPTION_NAMES = frozenset({"Exception"})
_CALL_NAMES = frozenset({
    "Decimal",
    "ValueError",
    "TypeError",
    "int",
    "float",
    "round",
    "abs",
    "isinstance",
    "str",
    "len",
})
_METHOD_NAMES = frozenset({
    "quantize",
    "is_finite",
    "is_nan",
    "is_infinite",
    "to_integral_value",
    "to_integral_exact",
    "copy_abs",
    "strip",
})
_ALLOWED_NODES = frozenset({
    ast.Module,
    ast.ImportFrom,
    ast.alias,
    ast.FunctionDef,
    ast.arguments,
    ast.arg,
    ast.Expr,
    ast.Constant,
    ast.Return,
    ast.Assign,
    ast.AnnAssign,
    ast.If,
    ast.IfExp,
    ast.Raise,
    ast.Try,
    ast.ExceptHandler,
    ast.Pass,
    ast.Name,
    ast.Load,
    ast.Store,
    ast.Call,
    ast.keyword,
    ast.Attribute,
    ast.UnaryOp,
    ast.UAdd,
    ast.USub,
    ast.Not,
    ast.BinOp,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.FloorDiv,
    ast.Mod,
    ast.BoolOp,
    ast.And,
    ast.Or,
    ast.Compare,
    ast.Eq,
    ast.NotEq,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
    ast.Is,
    ast.IsNot,
    ast.Tuple,
})


def _validate_source(code: str) -> None:
    if len(code) > 16_000:
        message = "Generated code exceeds the 16,000 character limit."
        raise ValueError(message)
    tree = ast.parse(code, filename="amount.py")
    functions = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)]
    if len(functions) != 1 or functions[0].name != "cents":
        message = "Provide exactly one function named cents."
        raise ValueError(message)
    function = functions[0]
    arguments = function.args
    if function not in tree.body or function.decorator_list or getattr(function, "type_params", ()):
        message = "cents must be a plain top-level function without decorators or type parameters."
        raise ValueError(message)
    if (
        len(arguments.posonlyargs + arguments.args) != 1
        or arguments.vararg
        or arguments.kwarg
        or arguments.kwonlyargs
        or arguments.defaults
        or arguments.kw_defaults
    ):
        message = "cents must accept exactly one positional argument without defaults."
        raise ValueError(message)
    if any(
        node is not function
        and not isinstance(node, ast.ImportFrom)
        and not (
            isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)
        )
        for node in tree.body
    ):
        message = "Only decimal imports, a docstring and cents are allowed at module scope."
        raise ValueError(message)
    nodes = list(ast.walk(tree))
    if len(nodes) > 600:
        message = "Generated code exceeds the syntax size limit."
        raise ValueError(message)
    local_names = {argument.arg for argument in arguments.posonlyargs + arguments.args}
    local_names.update(node.id for node in nodes if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store))
    local_names.update(node.name for node in nodes if isinstance(node, ast.ExceptHandler) and node.name)
    reserved = _CALL_NAMES | _DECIMAL_NAMES | _EXCEPTION_NAMES
    if local_names & reserved or any("__" in name for name in local_names):
        message = "Local names must not replace allowed builtins or decimal imports."
        raise ValueError(message)
    for node in nodes:
        _validate_node(node, local_names | reserved)


def _validate_node(node: ast.AST, known_names: set[str] | frozenset[str]) -> None:
    if type(node) not in _ALLOWED_NODES:
        message = f"Unsupported syntax: {type(node).__name__}."
        raise ValueError(message)
    if isinstance(node, ast.ImportFrom) and (
        node.module != "decimal"
        or node.level
        or any(item.name not in _DECIMAL_NAMES or item.asname is not None for item in node.names)
    ):
        message = "Only explicit, unaliased imports from decimal are supported."
        raise ValueError(message)
    if isinstance(node, ast.Name) and ("__" in node.id or node.id not in known_names):
        message = f"Unsupported name: {node.id}."
        raise ValueError(message)
    if isinstance(node, ast.Attribute) and (node.attr not in _METHOD_NAMES or not isinstance(node.ctx, ast.Load)):
        message = f"Unsupported attribute: {node.attr}."
        raise ValueError(message)
    if isinstance(node, ast.Call):
        allowed = (isinstance(node.func, ast.Name) and node.func.id in _CALL_NAMES) or (
            isinstance(node.func, ast.Attribute) and node.func.attr in _METHOD_NAMES
        )
        if not allowed or any(keyword.arg is None for keyword in node.keywords):
            message = "Only the documented pure amount-parser calls are supported."
            raise ValueError(message)
    if isinstance(node, ast.Constant):
        _validate_literal(node.value)
    if isinstance(node, ast.Assign) and any(not isinstance(target, ast.Name) for target in node.targets):
        message = "Assignments must target local variable names."
        raise ValueError(message)
    if isinstance(node, ast.AnnAssign) and not isinstance(node.target, ast.Name):
        message = "Assignments must target local variable names."
        raise TypeError(message)


def _validate_literal(value: object) -> None:
    if not isinstance(value, str | int | float | bool | type(None)):
        message = "Unsupported literal."
        raise TypeError(message)
    if (isinstance(value, str) and len(value) > 2_000) or (isinstance(value, int) and value.bit_length() > 256):
        message = "Literal exceeds the amount-parser size limit."
        raise ValueError(message)


_RUNNER = """\
import json
import os
import sys
from pathlib import Path

try:
    import resource
except ImportError:
    resource = None
if resource is not None:
    resource.setrlimit(resource.RLIMIT_CPU, (2, 2))
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
sys.dont_write_bytecode = True
cases = json.loads(sys.argv[2])
checks = []
try:
    namespace = {"__name__": "generated_amount"}
    source = Path(sys.argv[1]).read_text(encoding="utf-8")
    exec(compile(source, sys.argv[1], "exec"), namespace)
    cents = namespace["cents"]
    for value, expected in cases:
        try:
            answer = cents(value)
        except Exception as error:
            actual = type(error).__name__
            passed = expected == "ValueError" and type(error) is ValueError
        else:
            passed = type(answer) is int and type(expected) is int and answer == expected
            if type(answer) is int and answer.bit_length() <= 256:
                actual = answer
            else:
                actual = type(answer).__name__ + " (expected a bounded int)"
        checks.append({"input": value, "expected": expected, "actual": actual, "passed": passed})
    result = {"passed": sum(item["passed"] for item in checks), "total": len(cases), "checks": checks}
except Exception as error:
    result = {"passed": 0, "total": len(cases), "checks": checks, "error": type(error).__name__}
result["pid"] = os.getpid()
print(json.dumps(result, ensure_ascii=False))
sys.exit(0 if result["passed"] == len(cases) else 1)
"""


async def _stop(process: asyncio.subprocess.Process) -> None:
    if process.returncode is None:
        with suppress(ProcessLookupError):
            process.kill()
    await process.wait()


async def verify_code(code: str, directory: Path) -> dict[str, Any]:
    """Execute supported generated source against host-owned cases in a new process.

    Rejected source is saved for inspection but is never executed. Its result has
    ``pid=0`` and ``exit_code=None``. A cancellation kills and reaps the child before
    propagating. The caller owns the directory and its retention/cleanup policy.
    """
    result: dict[str, Any] = {
        "passed": 0,
        "total": len(_CASES),
        "checks": [],
        "exit_code": None,
        "code_sha256": hashlib.sha256(code.encode()).hexdigest(),
        "pid": 0,
    }
    directory = await asyncio.to_thread(directory.resolve)
    await asyncio.to_thread(directory.mkdir, parents=True, exist_ok=True)
    source_path = directory / "amount.py"
    await asyncio.to_thread(source_path.write_bytes, code.encode("utf-8"))
    try:
        _validate_source(code)
    except (SyntaxError, ValueError, TypeError, RecursionError) as error:
        result["error"] = f"Code was not executed: {error}"
        return result
    runner_path = directory / "verify_amount.py"
    await asyncio.to_thread(runner_path.write_text, _RUNNER, encoding="utf-8")
    spawning = asyncio.create_task(
        asyncio.create_subprocess_exec(
            sys.executable,
            "-I",
            "-X",
            "utf8",
            str(runner_path),
            str(source_path),
            json.dumps(_CASES),
            cwd=directory,
            env={"PATH": os.defpath, "LANG": "C.UTF-8", "PYTHONIOENCODING": "utf-8"},
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    )
    try:
        process = await asyncio.shield(spawning)
    except asyncio.CancelledError:
        process = await spawning
        await _stop(process)
        raise
    result["pid"] = process.pid
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=5)
    except TimeoutError:
        await _stop(process)
        result.update(exit_code=process.returncode, error="Verification exceeded the 5 second deadline.")
        return result
    except asyncio.CancelledError:
        await _stop(process)
        raise
    result["exit_code"] = process.returncode
    try:
        details = json.loads(stdout)
    except (ValueError, UnicodeDecodeError):
        result["error"] = "Verification process did not return a result (resource limit or interpreter failure)."
        if stderr:
            result["stderr"] = stderr.decode(errors="replace")[-2_000:]
        return result
    result.update(details)
    return result
