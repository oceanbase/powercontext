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

"""Inspect trusted local plugin declarations outside the Server process."""

from __future__ import annotations

import json
import sys

from powercontext.source_management.config import RuntimeConfig
from powercontext.source_management.loading import load_runtime_plugin
from powercontext.sources import manifest_for_definition


def main() -> None:
    config = RuntimeConfig.model_validate_json(sys.stdin.buffer.read(2 * 1024 * 1024 + 1))
    loaded = load_runtime_plugin(config)
    print(
        json.dumps([
            manifest_for_definition(d).model_dump(mode="json", by_alias=True) for d in loaded.registry.definitions
        ])
    )


if __name__ == "__main__":
    main()
