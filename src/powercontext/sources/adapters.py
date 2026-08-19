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

from typing import Protocol, TypeVar

from powercontext.sources.models import Source

InputT = TypeVar("InputT")
SourceT = TypeVar("SourceT", bound=Source)
ValueT_co = TypeVar("ValueT_co", covariant=True)


class SourceAdapter(Protocol[InputT, SourceT, ValueT_co]):
    """Resolve one exact input class and read one concrete Source class."""

    input_class: type[InputT]
    """The exact input class accepted by this adapter."""

    name: str
    """The stable registration name for this adapter."""

    source_class: type[SourceT]
    """The exact Source class produced and read by this adapter."""

    async def resolve(self, value: InputT, /) -> SourceT:
        """Resolve an adapter-native value without changing a catalog."""

        ...

    async def read(self, source: SourceT, /) -> ValueT_co:
        """Read the adapter-native value described by a Source."""

        ...
