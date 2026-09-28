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

"""Supply saved authorization to Codex's native HTTP MCP client over its private pipe."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from settings import _server_url_from_mcp_configuration, _stored_authorization


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--credential-file", type=Path, required=True)
    args = parser.parse_args()
    try:
        authorization = _stored_authorization(
            _server_url_from_mcp_configuration(), credential_file=args.credential_file
        )
    except (OSError, ValueError):
        return 1
    print(json.dumps({"Authorization": authorization} if authorization else {}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
