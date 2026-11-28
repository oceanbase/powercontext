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

"""Require a complete, identity-bound native receipt after official diff or gate."""

from __future__ import annotations

import argparse
import json
from hashlib import sha256
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--stats", type=Path, required=True)
    parser.add_argument("--binary", type=Path, required=True)
    args = parser.parse_args()
    context = json.loads(args.context.read_text())
    receipts = list(args.stats.glob(f"{args.run_id}.*.json"))
    if len(receipts) != 1:
        message = "Expected exactly one candidate process receipt"
        raise RuntimeError(message)
    report = json.loads(receipts[0].read_text())
    valid = (
        report["run_id"] == args.run_id
        and receipts[0].name == f"{args.run_id}.{report['pid']}.json"
        and report["context_sha256"] == sha256(args.context.read_bytes()).hexdigest()
        and report["binary_sha256"] == sha256(args.binary.read_bytes()).hexdigest()
        and Path(report["native_path"]).resolve() == args.binary.resolve()
        and report["phase"] == "candidate"
        and report["complete"]
        and report["requests"] == context["requests"]
        and report["calls"] == len(context["requests"])
        and report["protocol_rejections"] == [context["protocol_rejection"]]
        and report["N"] == report["R"] == 63
        and report["F"] == report["E"] == 0
        and report["native_completions"] == 63
        and not report["violations"]
    )
    if not valid:
        message = "Incomplete, stale or incorrect native receipt"
        raise RuntimeError(message)
    print(
        json.dumps({
            "business_calls": report["calls"],
            "protocol_rejections": 1,
            "N": report["N"],
            "R": report["R"],
            "F": 0,
            "E": 0,
        })
    )


if __name__ == "__main__":
    main()
