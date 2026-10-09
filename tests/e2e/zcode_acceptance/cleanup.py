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

"""Remove one finished run's owned private state while retaining its reviewed evidence."""

import argparse
import json
import re
import shutil
import stat
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(".powercontext/zcode-acceptance"))
    parser.add_argument("--run-id", required=True)
    parser.add_argument(
        "--include-failed", action="store_true", help="Also clean a finished failed run after diagnosis."
    )
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9a-f]{32}", args.run_id):
        parser.error("invalid_run_id")
    output = args.output.resolve(strict=True)
    run = (output / args.run_id).resolve(strict=True)
    if run.parent != output or run.name != args.run_id:
        parser.error("run_path_outside_output")
    summary = json.loads((run / "summary.json").read_text(encoding="utf-8"))
    allowed = {"passed", "failed"} if args.include_failed else {"passed"}
    if (
        summary.get("schema") != "powercontext.zcode.acceptance-run.v1"
        or summary.get("run_id") != args.run_id
        or not summary.get("finished_at")
        or summary.get("overall_status") not in allowed
    ):
        parser.error("run_not_finished_or_not_authorized_for_cleanup")
    names = (
        "profile",
        "second-profile",
        "workspace",
        "second-workspace",
        "client-settings.json",
        "server.db",
        "server.db-wal",
        "server.db-shm",
        "second-server.db",
        "second-server.db-wal",
        "second-server.db-shm",
    )
    targets = [run / name for name in names if (run / name).exists()]
    # Validate every resolved target before any deletion, including Windows reparse points.
    for target in targets:
        attributes = getattr(target.lstat(), "st_file_attributes", 0)
        if target.resolve().parent != run or target.is_symlink() or attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            parser.error("cleanup_target_outside_run_or_reparse_point")
    for target in targets:
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
    print(f"Cleaned private state for {args.run_id}; summary and evidence retained.")


if __name__ == "__main__":
    main()
