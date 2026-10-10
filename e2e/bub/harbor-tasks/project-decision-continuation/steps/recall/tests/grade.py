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

"""Grade the recall answer without depending on the agent host.

The answer is structured, so the grader checks the values the agent asserts rather than matching keywords that a
contradictory or hedged answer could also contain. This file is uploaded only with the recall step's tests, so no
earlier session can read the expected answer.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

EXPECTED_DATABASE = "oceanbase"
EXPECTED_SHARD_COUNT = 12
ANSWER_KEYS = {"database", "shard_count"}
ASCII_WHITESPACE = " \t\r\n"


class DuplicateKeyError(ValueError):
    """A repeated key would let a later value silently override a contradictory earlier one."""


def score(answer: str) -> int:
    try:
        payload = json.loads(answer, object_pairs_hook=_unique_keys)
    except ValueError:
        return 0
    # Extra keys could carry a hedge or an alternative that the checked fields do not show.
    if not isinstance(payload, dict) or set(payload) != ANSWER_KEYS:
        return 0
    database = payload.get("database")
    shard_count = payload.get("shard_count")
    # The name is case-insensitive, but only ASCII case: a look-alike such as a fullwidth letter is a different name.
    return int(
        isinstance(database, str)
        and database.isascii()
        and database.strip(ASCII_WHITESPACE).lower() == EXPECTED_DATABASE
        and _integer(shard_count) == EXPECTED_SHARD_COUNT
    )


def _unique_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    keys = [key for key, _ in pairs]
    if len(keys) != len(set(keys)):
        raise DuplicateKeyError
    return dict(pairs)


def _integer(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    # ASCII digits only: a fullwidth or other look-alike digit is not accepted, as a look-alike letter is not.
    if isinstance(value, str) and value.isascii() and value.strip(ASCII_WHITESPACE).isdecimal():
        return int(value.strip(ASCII_WHITESPACE))
    return None


def main(answer_path: Path, reward_path: Path) -> None:
    answer = answer_path.read_text(encoding="utf-8", errors="replace") if answer_path.is_file() else ""
    reward_path.write_text(f"{score(answer)}\n", encoding="utf-8")


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
