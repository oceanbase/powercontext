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

from __future__ import annotations

import json
from pathlib import Path

from powercontext.builtin.work.models import content_digest
from powercontext.http import PreparedHandoff
from powercontext.server.mapping import runtime_prepared_handoff


def test_dsh_prepared_digest_vectors_match_core() -> None:
    fixtures = (
        Path(__file__).resolve().parents[1]
        / "integrations/dsh/plugins/powercontext/tests/fixtures/handoff-digests.json"
    )
    for vector in json.loads(fixtures.read_text(encoding="utf-8")):
        prepared = runtime_prepared_handoff(PreparedHandoff.model_validate(vector["wire"]))
        assert content_digest(prepared) == vector["digest"]
