#!/usr/bin/env python3
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

"""Compare recorded processing results with the artifacts they name.

Exit status is 0 when every recorded result claim matches stored artifacts and
1 when any mismatch is found, so the check can gate a maintenance run.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy.ext.asyncio import create_async_engine

from powercontext.builtin.processing_reconciliation import reconcile


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", required=True, help="Async SQLAlchemy URL of the Server database")
    parser.add_argument("--scope", help="Limit the check to one Scope")
    return parser.parse_args(argv)


async def run(url: str, scope: str | None):
    engine = create_async_engine(url)
    try:
        async with engine.connect() as connection:
            return await reconcile(connection, scope_id=scope)
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    problems = asyncio.run(run(args.database_url, args.scope))
    for problem in problems:
        print(problem)
    print(f"{len(problems)} mismatch(es)" if problems else "No mismatches")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
