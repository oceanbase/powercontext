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


"""Compare the installed default Rust analyzer with the retained Python span contract."""

import argparse
import hashlib
import importlib.machinery
import inspect
import json
import random
import sys
import unicodedata
from pathlib import Path

EVALUATION = Path(__file__).resolve().parents[1]
ROOT = EVALUATION.parents[2]


def python_reference(value):
    from powercontext.builtin.artifacts.search import analyze_text_with_spans

    return " ".join(term for term, _start, _end in analyze_text_with_spans(value))


def inputs():
    rows = [json.loads(line) for line in (EVALUATION / "cases.jsonl").read_text(encoding="utf-8").split("\n") if line]
    texts = [row["args"][0] for row in rows]
    texts += [
        "",
        "a\ud800b",
        "中文\udfff文本",
        "\ud800\udc00",
        "a\ud800\u0301b",
        "a\U0001e4ec\u0300",
        "a\U0001e5ee\u0300",
        "\u1c89\u1c8a",
        "ǰ",
    ]
    for boundary in ["㐀", "\u4dbf", "\u9fd6", "\u9fff", "\uf900", "\U00020000", "\U0002fa1f"]:
        texts += [f"丁丁丁{boundary}", f"{boundary}丁丁丁", f"丁丁丁{boundary}丁丁丁"]
    # Every casefold/decomposition and every word-range edge exercises the running
    # interpreter's Unicode contract, including code points newer than that version.
    for number in range(0x110000):
        char = chr(number)
        if char.casefold() != char or unicodedata.decomposition(char):
            texts.append(char + "'XYZ " + char)
    crate = ROOT / "src/powercontext/builtin/artifacts/lexical/data"
    edges = set()
    for table in sorted(crate.glob("unicode*.json")):
        data = json.loads(table.read_text(encoding="utf-8"))
        for start, end in data["word_ranges"]:
            edges.update([max(0, start - 1), start, end, min(0x10FFFF, end + 1)])
    texts += ["a" + chr(number) + "b" for number in sorted(edges)]
    rng = random.Random(90210)  # noqa: S311 - reproducible compatibility corpus.
    alphabet = [
        *"中文南京长江数据ABCabc0123_' ",
        "ǰ",
        "ß",
        "İ",
        "é",
        "\u0301",
        "\U00020000",
        "\ud800",
        "\udfff",
        "\u200d",
        "😀",
        "\U0001e4ec",
        "\U0001e5ee",
        "\u0300",
        "\u1c89",
    ]
    texts += ["".join(rng.choices(alphabet, k=rng.randrange(1, 100))) for _ in range(1000)]
    metadata = json.loads((EVALUATION / "results/reference.json").read_text(encoding="utf-8"))
    texts += [(ROOT / path).read_text(encoding="utf-8")[:16000] for path in metadata["doc_paths"]]
    return list(dict.fromkeys(texts))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    from powercontext import _lexical
    from powercontext.builtin.artifacts.search import analyze_text

    if not inspect.isbuiltin(analyze_text) or analyze_text is not _lexical.analyze_text:
        message = "Default analyzer must be the native function"
        raise AssertionError(message)
    path = Path(_lexical.__file__).resolve()
    if not any(str(path).endswith(suffix) for suffix in importlib.machinery.EXTENSION_SUFFIXES):
        message = "Analyzer module must be a native extension"
        raise AssertionError(message)
    texts = inputs()
    mismatches = []
    for number, text in enumerate(texts):
        actual = analyze_text(text)
        expected = python_reference(text)
        if actual != expected:
            mismatches.append({
                "case": number,
                "codepoints": list(map(ord, text)),
                "actual": actual,
                "expected": expected,
            })
    for value in [None, True, 1, 1.5, [], {}, b"x"]:
        try:
            analyze_text(value)
        except TypeError:
            pass
        else:
            message = "Native analyzer accepted a non-string"
            raise AssertionError(message)
    report = {
        "python": sys.version,
        "unicode": unicodedata.unidata_version,
        "native": str(path),
        "native_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "default_is_native": True,
        "cases": len(texts),
        "mismatches": mismatches,
        "non_string_type_errors": 7,
    }
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=True, indent=2))
    if mismatches:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
