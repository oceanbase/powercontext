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


"""Compare the same public text in one thread, including the native call boundary."""

import argparse
import hashlib
import json
import statistics
import sys
import time
from pathlib import Path

CRATE = Path(__file__).resolve().parents[1]
ROOT = CRATE.parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "output/lexical-production/benchmark.json")
    args = parser.parse_args()
    from powercontext.builtin.artifacts.search import analyze_text_with_spans

    def python_reference(value):
        return " ".join(term for term, _start, _end in analyze_text_with_spans(value))

    from powercontext import _lexical as _native

    if _native.__file__ is None:
        message = "Native module has no build identity"
        raise RuntimeError(message)

    from powercontext.builtin.artifacts.search import analyze_text

    metadata = json.loads((CRATE / "results/reference.json").read_text(encoding="utf-8"))
    groups = {
        locale: [
            (ROOT / path).read_text(encoding="utf-8")[:16000]
            for path in metadata["doc_paths"]
            if path.startswith(f"docs/{locale}/")
        ]
        for locale in ["zh", "en"]
    }
    groups["short_query_1000"] = ["PowerContext Server 地址是什么？"] * 1000  # noqa: RUF001 - actual query punctuation.
    rows = []
    for name, texts in groups.items():
        for text in texts:
            if python_reference(text) != analyze_text(text):
                message = "Do not time mismatching behavior"
                raise AssertionError(message)
        timings = {"python": [], "rust": []}
        for number in range(9):
            order = [("python", python_reference), ("rust", analyze_text)]
            for side, function in order[:: 1 if number % 2 else -1]:
                started = time.perf_counter()
                outputs = [function(text) for text in texts]
                timings[side].append((time.perf_counter() - started) * 1000)
                if not outputs:
                    message = "Empty benchmark"
                    raise AssertionError(message)
        medians = {side: statistics.median(values) for side, values in timings.items()}
        rows.append({
            "group": name,
            "inputs": len(texts),
            "characters": sum(map(len, texts)),
            "median_ms": medians,
            "speedup": medians["python"] / medians["rust"],
            "samples_ms": timings,
        })
    report = {
        "threads": 1,
        "build": "release",
        "rounds": 9,
        "bridge_included": True,
        "scope": "warm offline corpus, not HTTP/backend/host latency",
        "native_sha256": hashlib.sha256(Path(_native.__file__).read_bytes()).hexdigest(),
        "python": sys.version,
        "results": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
