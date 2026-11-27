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

"""Export existing-format chunk hashes from an explicitly selected checkout."""

from __future__ import annotations

import argparse
import json
import sys
from importlib import import_module
from pathlib import Path

from evaluation.knowledge_base.fixtures import IMAGE_TEXT, generate, json_bytes, sha256

BASELINE = "e6e36a43b22240a827c9daec9798ccb256032608"
CHUNKING = {"max_chars": 360, "overlap": 40}
RECORD = Path(__file__).with_name("legacy-baseline.json")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("checkout", type=Path, help="Checkout whose legacy parse entry point will be exercised")
    parser.add_argument("--export", type=Path, help="Save the exported hashes instead of comparing with the baseline")
    args = parser.parse_args()
    sys.path.insert(0, str(args.checkout.resolve() / "plugins" / "knowledge-base"))
    parse = import_module("pc_knowledge_base.content").parse
    files, _ = generate()
    cases = {"service.md": files["service.md"], "service.markdown": files["service.md"]}
    cases.update({name: files[name] for name in IMAGE_TEXT})
    outputs = {name: parse(data, name, CHUNKING) for name, data in cases.items()}
    observed = {
        "baseline": BASELINE,
        "chunking": CHUNKING,
        "output_sha256": sha256(json_bytes(outputs)),
        "cases": {
            name: {"source_sha256": sha256(cases[name]), "output_sha256": sha256(json_bytes(result))}
            for name, result in outputs.items()
        },
    }
    if args.export:
        args.export.write_bytes(json_bytes(observed))
    elif observed != json.loads(RECORD.read_bytes()):
        raise SystemExit("Legacy chunk output differs from the baseline")  # noqa: TRY003
    print(f"Legacy chunks: {len(cases)} cases, sha256={observed['output_sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
