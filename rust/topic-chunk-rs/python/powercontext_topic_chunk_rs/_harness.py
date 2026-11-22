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

"""Task-owned record/diff/bench/gate evidence, using explicit installed interpreters."""

from __future__ import annotations

import argparse
import contextlib
import gc
import hashlib
import importlib.util
import json
import runpy
import shutil
import statistics
import subprocess
import sys
import sysconfig
import tempfile
import time
import tracemalloc
import unicodedata
from importlib.metadata import version
from pathlib import Path
from typing import Any

if not __package__:
    __package__ = "powercontext_topic_chunk_rs"

ROOT = Path(__file__).resolve().parents[2]
REPOSITORY = ROOT.parents[1]
CORPUS = ROOT / "corpus"
REFERENCE_FILES = [
    "src/powercontext/builtin/artifacts/topic_memory/chunking.py",
    "src/powercontext/builtin/artifacts/topic_memory/models.py",
    "tests/builtin/artifacts/topic_memory/test_chunking.py",
    "tests/builtin/artifacts/topic_memory/test_models.py",
    "tests/builtin/persistence/test_topic_memory.py",
    "tests/e2e/test_topic_memory_generic_api.py",
    "uv.lock",
]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def qualified(value: Any) -> str:
    cls = type(value)
    return cls.__module__ + "." + cls.__qualname__


def encode(value: Any) -> Any:
    """Version1 lossless value codec; unknown objects fail recording."""
    if value is None or type(value) in (bool, int):
        return {"type": type(value).__name__, "value": value}
    if type(value) is str:
        if any(0xD800 <= ord(c) <= 0xDFFF for c in value):
            return {"type": "str32", "value": str.encode(value, "utf-32-le", "surrogatepass").hex()}
        if len(value) >= 256 and value == value[0] * len(value):
            return {"type": "str-repeat", "char": value[0], "count": len(value)}
        return {"type": "str", "value": value}
    if isinstance(value, BaseException):
        return {"type": "exception", "class": qualified(value), "text": encode(str(value)), "args": encode(value.args)}
    if type(value) in (tuple, list):
        return {"type": type(value).__name__, "items": [encode(item) for item in value]}
    if type(value) is dict:
        return {"type": "dict", "items": [[encode(key), encode(item)] for key, item in value.items()]}
    if hasattr(value, "model_dump"):
        return {"type": "model", "class": qualified(value), "fields": encode(value.model_dump())}
    message = f"No lossless codec for {qualified(value)}"
    raise TypeError(message)


def runtime() -> dict[str, Any]:
    if sys.version_info[:2] != (3, 14) or unicodedata.unidata_version != "16.0.0":
        message = "Target acceptance requires CPython3.14/Unicode16"
        raise RuntimeError(message)
    from powercontext.builtin.artifacts.topic_memory import chunking, models

    installed_sources = {}
    for module, relative in ((chunking, REFERENCE_FILES[0]), (models, REFERENCE_FILES[1])):
        path = Path(module.__file__).resolve()
        if not path.is_relative_to(sys.prefix) or digest(path) != digest(REPOSITORY / relative):
            message = "BLOCKED: installed core source differs from the fixed local reference"
            raise RuntimeError(message)
        installed_sources[relative] = digest(path)
    return {
        "installed_core_sources": installed_sources,
        "python": sys.version,
        "unicode": unicodedata.unidata_version,
        "soabi": sysconfig.get_config_var("SOABI"),
        "pydantic": version("pydantic"),
        "pydantic_core": version("pydantic-core"),
        "powercontext": version("powercontext"),
        "executable_sha256": digest(Path(sys.executable).resolve()),
    }


def cases() -> list[dict[str, Any]]:
    return json.loads((CORPUS / "cases.json").read_text())["cases"]


def materialize(case: dict[str, Any]) -> Any:
    if "invalid" in case:
        return case["invalid"]
    parts = []
    for part in case.get("parts", []):
        value = (
            bytes.fromhex(part["utf32le"]).decode("utf-32-le", "surrogatepass") if "utf32le" in part else part["text"]
        )
        parts.append(value * part.get("repeat", 1))
    return "".join(parts)[: case.get("limit", 10**9)]


def outcome(function: Any, case: dict[str, Any]) -> Any:
    from pydantic import ValidationError

    try:
        result = function() if case.get("missing") else function(materialize(case))
    except Exception as error:
        result = {"outcome": "error", "exception": encode(error)}
        if isinstance(error, ValidationError):
            result["details"] = encode(error.errors())
        return result
    return {"outcome": "return", "result": encode(result)}


def fingerprint() -> dict[str, str]:
    paths = [REPOSITORY / path for path in REFERENCE_FILES]
    paths += [
        ROOT / "tools" / "harness.py",
        ROOT / "python" / "powercontext_topic_chunk_rs" / "_harness.py",
        ROOT / "tools" / "generate_unicode_tables.py",
        ROOT / "src" / "unicode_tables.rs",
        CORPUS / "cases.json",
    ]
    return {str(path.relative_to(REPOSITORY)): digest(path) for path in paths}


def record() -> dict[str, Any]:
    if importlib.util.find_spec("powercontext_topic_chunk_rs") is not None:
        message = "Reference recording environment contains the native experiment"
        raise RuntimeError(message)
    from powercontext.builtin.artifacts.topic_memory.chunking import chunk_topic_memory_detail

    rows = [{"id": case["id"], "expected": outcome(chunk_topic_memory_detail, case)} for case in cases()]
    (CORPUS / "golden.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows)
    )
    manifest = {
        "schema": 1,
        "runtime": runtime(),
        "reference": fingerprint(),
        "golden_sha256": digest(CORPUS / "golden.jsonl"),
        "cases": len(rows),
        "normalization": "No traceback/addresses recorded; exception type/text/args and all Pydantic errors preserved. str-repeat encodes only verified uniform strings; expansion is char*count.",
    }
    (CORPUS / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    size = sum(path.stat().st_size for path in CORPUS.iterdir() if path.is_file())
    if size > 2 * 1024 * 1024:
        message = f"Corpus exceeds 2MiB: {size}"
        raise RuntimeError(message)
    return {"recorded": len(rows), "corpus_bytes": size}


def check_reference() -> dict[str, Any]:
    manifest = json.loads((CORPUS / "manifest.json").read_text())
    if (
        manifest["runtime"] != runtime()
        or manifest["reference"] != fingerprint()
        or manifest["golden_sha256"] != digest(CORPUS / "golden.jsonl")
    ):
        message = "BLOCKED: reference/runtime/corpus fingerprint drift"
        raise RuntimeError(message)
    return manifest


def check_native_artifact() -> None:
    from . import _adapter, _monitor, _native

    for module in (_adapter, _monitor):
        path = Path(module.__file__).resolve()
        expected = ROOT / "python" / "powercontext_topic_chunk_rs" / path.name
        if not path.is_relative_to(sys.prefix) or digest(path) != digest(expected):
            message = "BLOCKED: candidate is shadowed or differs from the fixed source"
            raise RuntimeError(message)
    if Path(_native.__file__).suffix not in (".so", ".pyd") or not Path(_native.__file__).is_relative_to(sys.prefix):
        message = "BLOCKED: candidate does not load an installed native extension"
        raise RuntimeError(message)


def verify(*, mutation: bool = False) -> dict[str, Any]:
    check_reference()
    check_native_artifact()
    from . import _native
    from ._monitor import NativeMonitor

    monitor = NativeMonitor()
    monitor.install()
    from powercontext.builtin.artifacts.topic_memory.chunking import chunk_topic_memory_detail as selected

    expected = [json.loads(line) for line in (CORPUS / "golden.jsonl").read_text().split("\n") if line]
    inputs = cases()
    if len(expected) != len(inputs):
        message = "Golden case count mismatch"
        raise RuntimeError(message)
    failures = []
    duplicates: set[str] = set()
    unique: set[str] = set()
    invalid = 0
    for case, row in zip(inputs, expected, strict=True):
        value = materialize(case)
        signature = hashlib.sha256(
            json.dumps([bool(case.get("missing")), encode(value)], ensure_ascii=False).encode()
        ).hexdigest()
        if signature in unique:
            duplicates.add(case["id"])
        unique.add(signature)
        actual = outcome(selected, case)
        if mutation and case["id"] == "plain-1":
            # Preserve native computation and its counters while corrupting a returned field.
            actual["result"]["items"][0]["fields"]["items"][0][1]["value"] = 7
        if row["id"] != case["id"] or row["expected"] != actual:
            failures.append(case["id"])
        if type(value) is not str or case.get("missing"):
            invalid += 1
    report = monitor.finish()
    report.update({
        "cases": len(inputs),
        "invalid_input_cases": invalid,
        "matched": len(inputs) - len(failures),
        "mismatch": failures,
        "duplicate_inputs": sorted(duplicates),
        "unique_input_values": len(unique),
        "binary_sha256": digest(Path(_native.__file__)),
        "golden_sha256": digest(CORPUS / "golden.jsonl"),
    })
    if failures:
        message = (
            "golden mismatch: " + ",".join(failures) + f"; N={monitor.n}, R={monitor.r}, F={monitor.f}, E={monitor.e}"
        )
        raise AssertionError(message)
    return report


def bench() -> dict[str, Any]:
    check_reference()
    check_native_artifact()
    from powercontext.builtin.artifacts.topic_memory.chunking import chunk_topic_memory_detail as p0
    from powercontext.builtin.artifacts.topic_memory.models import TopicMemoryChunk

    from . import _native
    from ._adapter import chunk_topic_memory_detail as native

    diagnostic = runpy.run_path(str(ROOT / "tools" / "python_diagnostic.py"))["chunk_topic_memory_detail"]
    rows = []
    for n in (1000, 8000, 125000):
        for kind, atom in [
            ("plain", "x"),
            ("markdown", "# Heading\n\nSentence. sentence。 \n\n- item one\n"),
            ("unicode", "汉字🙂é "),
            ("inert_lf", "x" * 79 + "\n"),
        ]:
            detail = (atom * ((n + len(atom) - 1) // len(atom)))[:n]
            payload = str.encode(detail, "utf-32-le", "surrogatepass")
            ranges = _native.chunk_ranges(payload)

            def construct(_: str, text: str = detail, spans: list[tuple[int, int]] = ranges) -> tuple[Any, ...]:
                return tuple(
                    TopicMemoryChunk(ordinal=i, start_offset=a, end_offset=b, text=text[a:b])
                    for i, (a, b) in enumerate(spans)
                )

            arms = {
                "P0": p0,
                "P1a": diagnostic,
                "P1b": lambda s: diagnostic(s, skip_block_scan=True),
                "R": native,
                "encode": lambda s: str.encode(s, "utf-32-le", "surrogatepass"),
                "native_core_and_return": lambda _, data=payload: _native.chunk_ranges(data),
                "slice_models": construct,
            }
            row: dict[str, Any] = {"n": n, "utf32_bytes": len(payload), "kind": kind}
            for label, function in arms.items():
                samples = []
                cpu = []
                for _ in range(7):
                    start = time.perf_counter_ns()
                    cpu_start = time.process_time_ns()
                    for _ in range(30):
                        function(detail)
                    cpu.append((time.process_time_ns() - cpu_start) / 30)
                    samples.append((time.perf_counter_ns() - start) / 30)
                row[label] = {
                    "median_ns": statistics.median(samples),
                    "min_ns": min(samples),
                    "max_ns": max(samples),
                    "cpu_median_ns": statistics.median(cpu),
                }
            rows.append(row)
    return {
        "runtime": runtime(),
        "rows": rows,
        "samples": "7 batches x30; warm, single thread; unprofiled same wheel",
        "binary_sha256": digest(Path(_native.__file__)),
        "parallel": "NOT_RUN: no concurrent chunking consumer; GIL retained",
    }


def range_dependency() -> dict[str, Any]:
    check_reference()
    check_native_artifact()
    from . import _native
    from ._adapter import chunk_topic_memory_detail

    original = _native.chunk_ranges
    before = _native.execution_stats()

    def corrupt(payload: bytes) -> list[tuple[int, int]]:
        spans = original(payload)
        return [(start, end - 1) for start, end in spans]

    vars(_native).update(chunk_ranges=corrupt)
    case = next(item for item in cases() if item["id"] == "plain-299")
    actual = outcome(chunk_topic_memory_detail, case)
    delta = _native.execution_stats() - before
    expected = next(
        json.loads(line)["expected"]
        for line in (CORPUS / "golden.jsonl").read_text().split("\n")
        if line and json.loads(line)["id"] == case["id"]
    )
    if delta != 1 or actual == expected:
        message = "Native range negative control did not exercise the intended path"
        raise RuntimeError(message)
    message = f"native range dependency mismatch; native_completions={delta}"
    raise AssertionError(message)


def probe(kind: str) -> dict[str, Any]:
    from powercontext.builtin.artifacts.topic_memory import chunking

    from . import _native
    from ._monitor import NativeMonitor, PathViolation

    saved = chunking._trimmed_span
    monitor = NativeMonitor()
    monitor.install()
    if kind == "swallowed-trap":
        for _ in range(2):
            with contextlib.suppress(PathViolation):
                saved("x", 0, 1)
    elif kind == "lost-profile":
        sys.setprofile(None)
    elif kind == "counter-spoof":
        counter = 0

        def spoof() -> int:
            nonlocal counter
            counter += 1
            return counter

        vars(_native).update(execution_stats=spoof, chunk_ranges=lambda _: [(0, 1)])
        with contextlib.suppress(PathViolation):
            monitor.route("x")
    return monitor.finish()


def resource_worker(kind: str, n: int) -> dict[str, Any]:
    import resource

    if kind == "python":
        from powercontext.builtin.artifacts.topic_memory.chunking import chunk_topic_memory_detail as function
    else:
        from ._adapter import chunk_topic_memory_detail as function
    scale = 1 if sys.platform == "darwin" else 1024
    baseline = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * scale
    atom = "# Heading\n\nSentence. sentence。 \n\n- item one\n"
    detail = (atom * ((n + len(atom) - 1) // len(atom)))[:n]
    start = time.perf_counter_ns()
    function(detail)
    cold = time.perf_counter_ns() - start
    tracemalloc.start()
    live = []
    for _ in range(2):
        for _ in range(20):
            function(detail)
        gc.collect()
        live.append(tracemalloc.get_traced_memory()[0])
    _, python_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * scale
    return {
        "kind": kind,
        "n": n,
        "first_entry_ns": cold,
        "baseline_hwm_bytes": baseline,
        "peak_hwm_bytes": peak,
        "hwm_increase_bytes": max(0, peak - baseline),
        "python_peak_bytes": python_peak,
        "python_live_after_batches": live,
        "limit_rss_increment_bytes": 64 * 1024 * 1024,
        "within_budget": peak - baseline <= 64 * 1024 * 1024 and cold <= 5_000_000_000,
    }


def run_command(command: list[str], output: Path, *, timeout: int = 60) -> dict[str, Any]:
    start = time.perf_counter_ns()
    result = subprocess.run(command, cwd=output.parent, capture_output=True, timeout=timeout, check=False)
    output.write_bytes(result.stdout + result.stderr)
    return {
        "command": command,
        "exit": result.returncode,
        "log": str(output),
        "sha256": digest(output),
        "wall_ns_including_startup": time.perf_counter_ns() - start,
    }


def resources(output: Path) -> dict[str, Any]:
    check_reference()
    output.mkdir(parents=True, exist_ok=True)
    commands = []
    rows = []
    for n in (1000, 8000, 125000):
        for kind in ("python", "native"):
            path = output / f"resource-{kind}-{n}.json"
            record = run_command(
                [
                    sys.executable,
                    "-I",
                    str(ROOT / "tools" / "harness.py"),
                    "resource-worker",
                    "--kind",
                    kind,
                    "--n",
                    str(n),
                ],
                path,
            )
            commands.append(record)
            if record["exit"]:
                message = "Resource worker failed: " + kind
                raise RuntimeError(message)
            rows.append(json.loads(path.read_text()))
    if not all(row["within_budget"] for row in rows):
        message = "Resource budget exceeded"
        raise AssertionError(message)
    return {
        "rows": rows,
        "commands": commands,
        "limitations": "Linux ru_maxrss is KiB and a high-water mark, not retained memory or a leak test. tracemalloc covers Python only. Cold entry excludes imports; subprocess wall includes startup. No production concurrency claim.",
    }


def gate(output: Path) -> dict[str, Any]:
    check_reference()
    output.mkdir(parents=True, exist_ok=True)
    commands = []
    script = str(ROOT / "tools" / "harness.py")
    python = sys.executable
    for name, args, needle in [
        ("diff", ["diff"], None),
        ("wrong-result", ["mutated-diff"], "golden mismatch: plain-1"),
        ("native-range", ["range-dependency"], "native range dependency mismatch; native_completions=1"),
        ("swallowed-trap", ["probe", "--probe", "swallowed-trap"], "reference_algorithm_entered"),
        ("lost-profile", ["probe", "--probe", "lost-profile"], "profile_hook_lost"),
        ("counter-spoof", ["probe", "--probe", "counter-spoof"], "native_binding_replaced"),
    ]:
        path = output / (name + ".log")
        record = run_command([python, "-I", script, *args], path)
        commands.append(record)
        valid = record["exit"] == 0 if needle is None else record["exit"] != 0 and needle in path.read_text()
        if not valid:
            message = "Task-owned gate failed: " + name
            raise AssertionError(message)
    from . import _native

    binary = Path(_native.__file__)
    original_hash = digest(binary)
    with tempfile.TemporaryDirectory(prefix="pc-topic-missing-") as temporary:
        package = Path(temporary) / "powercontext_topic_chunk_rs"
        shutil.copytree(binary.parent, package, ignore=shutil.ignore_patterns("*.so", "*.pyd", "__pycache__"))
        code = "import sys;sys.path.insert(0,sys.argv[1]);from powercontext_topic_chunk_rs._adapter import chunk_topic_memory_detail;chunk_topic_memory_detail('x')"
        path = output / "missing-native.log"
        record = run_command([python, "-I", "-c", code, temporary], path)
        commands.append(record)
        if record["exit"] == 0 or "_native" not in path.read_text() or "ImportError" not in path.read_text():
            message = "Missing-native negative control failed"
            raise AssertionError(message)
    if digest(binary) != original_hash:
        message = "Negative control modified original native binary"
        raise AssertionError(message)
    # Re-run the positive result after disposable negative controls.
    commands.append(run_command([python, "-I", script, "diff"], output / "restored-diff.log"))
    if commands[-1]["exit"]:
        message = "Positive replay failed after negative controls"
        raise AssertionError(message)
    return {
        "tool": "task-owned gate, not rust-anything",
        "commands": commands,
        "binary_sha256_before_after": original_hash,
        "result": "PASS",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=[
            "record",
            "diff",
            "bench",
            "mutated-diff",
            "probe",
            "gate",
            "resources",
            "resource-worker",
            "range-dependency",
        ],
    )
    parser.add_argument("--probe", choices=["swallowed-trap", "lost-profile", "counter-spoof"])
    parser.add_argument("--output", type=Path)
    parser.add_argument("--kind", choices=["python", "native"], default="native")
    parser.add_argument("--n", type=int, default=8000)
    args = parser.parse_args()
    if args.action in ("gate", "resources") and args.output is None:
        parser.error("--output is required")
    actions = {
        "gate": lambda: gate(args.output),
        "resources": lambda: resources(args.output),
        "resource-worker": lambda: resource_worker(args.kind, args.n),
        "range-dependency": range_dependency,
        "record": record,
        "diff": verify,
        "bench": bench,
        "mutated-diff": lambda: verify(mutation=True),
        "probe": lambda: probe(args.probe or "swallowed-trap"),
    }
    print(json.dumps(actions[args.action](), ensure_ascii=False, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
