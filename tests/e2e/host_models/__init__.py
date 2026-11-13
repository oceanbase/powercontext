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

"""Deterministic host model fakes, one module per provider wire format.

`ScriptedTurns` holds the wire-neutral script that every fake replays. A wire module translates that script into one
provider protocol; Chat Completions lives in `tests/e2e/zcode_acceptance/protocol.py`.
"""
