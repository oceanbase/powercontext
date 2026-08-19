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

"""Immutable values produced by Triggers."""

from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel

StateT = TypeVar("StateT")
ActionT_co = TypeVar("ActionT_co", covariant=True)


class PolicyTransition(BaseModel, Generic[StateT, ActionT_co]):
    """The complete result of one pure Trigger activation."""

    state: StateT
    actions: tuple[ActionT_co, ...] = ()
