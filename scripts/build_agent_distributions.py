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

"""Assemble integrations that use an installed PowerContext client."""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "integrations/distribution"))

from agent_distribution import ROOT, assemble, hook_files, require_runners, write_package
from powercontext_integrations.resources import install_resources
from powercontext_integrations.targets import load_targets


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", action="append")
    parser.add_argument("--output", type=Path, default=ROOT / "build/agent-distributions")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--in-place", action="store_true", help="Generate resources in repository plugin directories.")
    parser.add_argument("--list", action="store_true", help="Report target behavior from the build profiles as JSON.")
    args = parser.parse_args()
    if not args.list and not args.in_place:
        require_runners()
    targets = load_targets()
    unknown = set(args.target or ()) - {target.target for target in targets}
    if unknown:
        parser.error(f"unknown targets: {sorted(unknown)}")
    targets = [target for target in targets if not args.target or target.target in args.target]
    if args.list:
        print(
            json.dumps(
                {
                    "languages": dict(Counter(target.language for target in targets)),
                    "targets": [target.model_dump(mode="json") for target in targets],
                },
                indent=2,
            )
        )
        return
    for target in targets:
        if args.in_place:
            install_resources(target.target, ROOT / target.source / target.resource_dir, preserve_mcp=False)
            for name, content in hook_files(target).items():
                path = ROOT / target.source / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
            continue
        files = assemble(target)
        write_package(args.output / target.target, files, check=args.check)
        print(f"{target.target}: {len(files)} files, {len(target.hooks)} native hooks")


if __name__ == "__main__":
    main()
