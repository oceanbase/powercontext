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


"""Export versioned compatibility data and strict cases from the fixed reference."""

import hashlib
import json
import os
import random
import sys
import unicodedata
from pathlib import Path

import jieba

CRATE = Path(__file__).resolve().parents[1]
ROOT = CRATE.parents[2]
sys.path.insert(0, str(ROOT / "src"))


def unicode_tables():
    folds, ranges = {}, []
    start = None
    for number in range(0x110000):
        char = chr(number)
        if char.casefold() != char:
            folds[str(number)] = char.casefold()
        word = char.isalnum() or char == "_"
        if word and start is None:
            start = number
        elif not word and start is not None:
            ranges.append([start, number - 1])
            start = None
    if start is not None:
        ranges.append([start, 0x10FFFF])
    return folds, ranges


def main():
    from powercontext.builtin.artifacts.search import analyze_text

    if unicodedata.unidata_version != "16.0.0" or jieba.__version__ != "0.42.1":
        message = "This pilot records Unicode16 / jieba0.42.1 only"
        raise RuntimeError(message)
    dictionary = Path(jieba.__file__).with_name("dict.txt").read_bytes()
    digest = hashlib.sha256(dictionary).hexdigest()
    if digest != "7197c3211ddd98962b036cdf40324d1ea2bfaa12bd028e68faa70111a88e12a8":
        message = "Unexpected reference Jieba dictionary"
        raise RuntimeError(message)
    cargo = Path(os.environ.get("CARGO_HOME", Path.home() / ".cargo"))
    rust_dict = next((cargo / "registry/src").glob("*/jieba-rs-0.11.0/src/data/dict.txt")).read_bytes()

    def entries(data):
        return {line.split()[0]: line.split()[1:] for line in data.splitlines() if line.split()}

    if entries(dictionary) != entries(rust_dict):
        message = "Rust and Python effective dictionaries differ"
        raise RuntimeError(message)
    reference_total = sum(int(line.split()[1]) for line in dictionary.splitlines() if line.split())
    rust_total = sum(int(line.split()[1]) for line in rust_dict.splitlines() if line.split())
    folds, ranges = unicode_tables()
    data = {
        "unicode": unicodedata.unidata_version,
        "casefold": folds,
        "word_ranges": ranges,
        "dictionary_sha256": digest,
        "rust_dictionary_sha256": hashlib.sha256(rust_dict).hexdigest(),
        "reference_total": reference_total,
        "frequency_correction": reference_total - rust_total,
    }
    (CRATE / "data").mkdir(exist_ok=True)
    (CRATE / "data/unicode16.json").write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    texts = [
        "",
        "a",
        "ab",
        "abc",
        "pcu pch pcu123 pch123",
        "x" * 80,
        "x" * 81,
        "_ under_score",
        "don't can’t 'quoted'",  # noqa: RUF001 - intentional quote normalization input.
        "ǰ j\u030c İ ı ß ẞ K Σ ς",  # noqa: RUF001 - distinct Unicode casefold cases.
        "中文文本 数据库查询 南京市长江大桥",
        "\U00020000\U0002fa1f 中文",
        "a\ud800b",
        "中文\udfff文本",
        "\ud800\udc00",
        "a\ud800\u0301b",
        "word\u200cword\u200dword a\u203fb ² Ⅳ",
        "x\x00y\r\nz",
    ]
    texts += [char * size for char in ["é", "a", "_", "ᾲ"] for size in [2, 3, 13, 14, 20, 21, 40, 41, 80, 81]]
    texts += [
        chr(number) + "'xyz " + chr(number) for number in range(0x110000) if chr(number).casefold() != chr(number)
    ]
    rng = random.Random(90210)  # noqa: S311 - reproducible test corpus, not a security primitive.
    alphabet = [
        *"中文南京长江数据程序ABCabc0123_'-’ \r\n",  # noqa: RUF001 - quote boundary corpus.
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
    ]
    texts += ["".join(rng.choices(alphabet, k=rng.randrange(1, 100))) for _ in range(1000)]
    paths = []
    for locale in ["zh", "en"]:
        paths += sorted((ROOT / "docs" / locale / "docs").rglob("*.md"))[:12]
    texts += [p.read_text(encoding="utf-8")[:16000] for p in paths]
    texts = list(dict.fromkeys(texts))
    evidence = ROOT / "output/lexical-pilot"
    evidence.mkdir(exist_ok=True, parents=True)
    cases = [{"codepoints": list(map(ord, text)), "expected": {"ok": analyze_text(text)}} for text in texts]
    (evidence / "strict-cases.jsonl").write_text(
        "".join(json.dumps(c, ensure_ascii=True) + "\n" for c in cases), encoding="utf-8"
    )
    tool_cases = [
        {"fn": "analyze_text", "args": [text]} for text in texts if not any(0xD800 <= ord(c) <= 0xDFFF for c in text)
    ]
    (evidence / "inputs.jsonl").write_text(
        "".join(json.dumps(c, ensure_ascii=True) + "\n" for c in tool_cases), encoding="utf-8"
    )
    metadata = {k: v for k, v in data.items() if k not in ["casefold", "word_ranges"]}
    metadata.update({
        "source_sha256": hashlib.sha256(
            (ROOT / "src/powercontext/builtin/artifacts/search.py").read_bytes()
        ).hexdigest(),
        "cases": len(cases),
        "tool_inputs": len(tool_cases),
        "doc_paths": [p.relative_to(ROOT).as_posix() for p in paths],
    })
    (evidence / "reference.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in metadata.items() if k != "doc_paths"}, indent=2))


if __name__ == "__main__":
    main()
