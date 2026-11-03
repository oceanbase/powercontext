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

"""Observable authorized next action in the disposable acceptance workspace."""

import json
from hashlib import sha256
from importlib import import_module
from pathlib import Path


def main() -> None:
    # The receiver supplies this module in the disposable workspace.
    increment = import_module("task").increment

    assert increment(0) == 1
    assert increment(-2) == -1
    assert increment(42) == 43
    result = {"status": "passed", "implementation_digest": sha256(Path("task.py").read_bytes()).hexdigest()}
    Path("receiver-action.json").write_text(json.dumps(result) + "\n", encoding="utf-8")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
