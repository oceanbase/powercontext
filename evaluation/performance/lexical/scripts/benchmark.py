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

import hashlib
import importlib
import json
import statistics
import sys
import time
from pathlib import Path

CRATE = Path(__file__).resolve().parents[1]
ROOT = CRATE.parents[2]
sys.path[:0] = [str(ROOT / "src"), str(CRATE / "python")]


def main():
    _native = importlib.import_module("pc_lexical_pilot._native")
    if _native.__file__ is None:
        message = "Native module has no build identity"
        raise RuntimeError(message)

    from powercontext.builtin.artifacts.search import analyze_text

    metadata = json.loads((ROOT / "output/lexical-pilot/reference.json").read_text(encoding="utf-8"))
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
            if analyze_text(text) != _native.analyze_text(text):
                message = "Do not time mismatching behavior"
                raise AssertionError(message)
        timings = {"python": [], "rust": []}
        for number in range(9):
            order = [("python", analyze_text), ("rust", _native.analyze_text)]
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
    (ROOT / "output/lexical-pilot/benchmark.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
