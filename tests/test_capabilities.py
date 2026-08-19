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

import pytest
from pydantic import ValidationError

from powercontext.http import Capabilities


def test_capabilities_require_the_complete_transport_shape() -> None:
    with pytest.raises(ValidationError):
        Capabilities.model_validate({
            "source_types": [],
            "artifact_families": [],
            "memory_extraction": False,
            "handoff_generation": False,
            "search_modes": [],
        })


def test_capabilities_reject_unknown_transport_fields() -> None:
    with pytest.raises(ValidationError):
        Capabilities.model_validate({
            "source_types": [],
            "artifact_families": [],
            "memory_extraction": False,
            "handoff_generation": False,
            "search_modes": [],
            "context_versions": [],
            "runtime_internal": True,
        })
