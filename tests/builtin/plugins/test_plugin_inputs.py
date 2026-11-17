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

"""A plugin may receive each Source type in its own Projection."""

import pytest
from pydantic import ValidationError

from powercontext.plugins import Inputs
from powercontext.sources import SourceProjectionKey


def test_each_source_type_uses_its_own_projection_or_the_default():
    inputs = Inputs(
        source_types=("content", "upload", "edit"),
        projection="powercontext.text-evidence@1",
        projections={"upload": "kb.upload@1", "edit": "kb.edit@2"},
    )

    assert inputs.projection_key("upload") == SourceProjectionKey(name="kb.upload", version="1")
    assert inputs.projection_key("edit") == SourceProjectionKey(name="kb.edit", version="2")
    assert inputs.projection_key("content") == SourceProjectionKey(name="powercontext.text-evidence", version="1")


@pytest.mark.parametrize(
    "declaration",
    [
        # A type without a per-type Projection and no default.
        {"source_types": ("upload", "edit"), "projections": {"upload": "kb.upload@1"}},
        # A per-type Projection for a type the plugin does not consume.
        {"source_types": ("upload",), "projection": "kb.upload@1", "projections": {"other": "kb.other@1"}},
    ],
)
def test_declarations_without_a_projection_for_every_type_are_rejected(declaration):
    with pytest.raises(ValidationError):
        Inputs.model_validate(declaration)
