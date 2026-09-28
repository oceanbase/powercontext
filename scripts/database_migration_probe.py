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

"""Exercise the RFC 1771 SQLite prototype against an explicitly chosen scratch DB.

This repository-only command does not load Server settings or a business runtime.
The four-table fixture is intentionally unable to adopt a complete deployment.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from powercontext.builtin.persistence.migrations import MigrationBundle, MigrationError, SQLiteMigrationRunner


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("status", "plan", "apply", "verify"))
    parser.add_argument("--sqlite-path", type=Path, required=True, help="Explicit disposable database path.")
    parser.add_argument("--plan-id")
    parser.add_argument("--resume")
    parser.add_argument("--maintenance-confirmed", action="store_true")
    parser.add_argument("--yes", action="store_true")
    args = parser.parse_args()
    bundle = MigrationBundle(Path(__file__).resolve().parents[1] / "tests/fixtures/database_migrations")
    runner = SQLiteMigrationRunner(args.sqlite_path, bundle)
    try:
        if args.action in {"status", "plan"}:
            result = runner.plan()
        elif args.action == "verify":
            result = runner.verify()
        else:
            plan = runner.plan()
            plan_id = args.plan_id
            accepted = args.yes
            if plan.state != "ready" and not accepted and sys.stdin.isatty():
                print(plan.model_dump_json(indent=2))
                accepted = input("Apply this plan to the scratch database? [y/N] ").strip().lower() == "y"
                plan_id = plan.plan_id
            result = runner.apply(
                plan_id=plan_id,
                accepted=accepted,
                maintenance_confirmed=args.maintenance_confirmed,
                resume=args.resume,
            )
        print(result.model_dump_json())
    except MigrationError as error:
        print(json.dumps({"error": error.code, "message": str(error)}), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
