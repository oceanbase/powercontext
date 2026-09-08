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

"""Small offline bridge CLI. No credentials are accepted as command-line flags."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from powercontext_datus.freeze import snapshot, verify_snapshot


async def _deliver(args: argparse.Namespace) -> dict[str, object]:
    from powercontext.client import PowerContextClient
    from powercontext.http import ArtifactReference
    from powercontext_datus.delivery import deliver_skill

    async with PowerContextClient(
        os.environ["POWERCONTEXT_BASE_URL"], token=os.environ.get("POWERCONTEXT_TOKEN")
    ) as client:
        return await deliver_skill(
            client,
            scope_id=args.scope,
            skill_root=args.skill_root,
            artifact=ArtifactReference(family="skill", artifact_id=args.artifact, revision=args.revision),
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    deliver = commands.add_parser("deliver", help="Install an exact approved package in an owned Skill root")
    deliver.add_argument("--scope", required=True)
    deliver.add_argument("--artifact", required=True)
    deliver.add_argument("--revision", required=True, type=int)
    deliver.add_argument("--skill-root", required=True, type=Path)
    capture = commands.add_parser("snapshot", help="Print a content/mode snapshot of isolated inputs")
    capture.add_argument("directory", type=Path)
    verify = commands.add_parser("verify", help="Fail if frozen input files drifted")
    verify.add_argument("directory", type=Path)
    verify.add_argument("manifest", type=Path)
    args = parser.parse_args()
    if args.command == "deliver":
        result = asyncio.run(_deliver(args))
    elif args.command == "snapshot":
        result = snapshot(args.directory)
    else:
        verify_snapshot(args.directory, json.loads(args.manifest.read_text(encoding="utf-8")))
        result = {"verified": True}
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
