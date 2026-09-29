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

"""Run the pinned LongMemEval-V2 harness with the PowerContext adapter registered."""

from __future__ import annotations

import sys
from importlib import import_module
from pathlib import Path

EVALUATION_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(EVALUATION_ROOT / "src"))
main = import_module("powercontext_eval.benchmarks.longmemeval_v2.harness_bootstrap").main


if __name__ == "__main__":
    main()
