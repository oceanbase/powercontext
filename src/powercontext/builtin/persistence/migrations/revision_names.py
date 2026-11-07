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

"""Software-release names without rewriting persisted migration identities."""

import re
from typing import TypedDict


class RevisionName(TypedDict):
    revision: str
    name: str
    software_version: str | None
    sequence: int | None


# These aliases are presentation metadata. Keep the original revision IDs in
# frozen resources, accepted plans, version tables and recovery evidence.
_LEGACY_NAMES = {
    "s1300": "v1_3_0_000",
    "r1400": "v1_4_0_000",
    "s1400": "v1_4_1_001",
    "s1401": "v1_4_1_002",
    "s1402": "v1_4_1_003",
}
_NAME = re.compile(r"v(0|[1-9][0-9]*)_(0|[1-9][0-9]*)_(0|[1-9][0-9]*)_([0-9]{3})")


def describe_revision(revision: str | None) -> RevisionName | None:
    """Describe a database state, not the running binary or its original source.

    Newly registered revisions use their release-based name directly. Unknown
    legacy/custom IDs remain opaque; their software version is never guessed.
    """
    if revision is None:
        return None
    name = _LEGACY_NAMES.get(revision, revision)
    match = _NAME.fullmatch(name)
    return {
        "revision": revision,
        "name": name,
        "software_version": ".".join(match.groups()[:3]) if match else None,
        "sequence": int(match[4]) if match else None,
    }
