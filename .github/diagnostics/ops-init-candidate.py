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

"""Independent, explicitly authorized personal Server operations."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from powercontext.ops.runtime import Ops, TargetBinding

__all__ = ["Ops", "TargetBinding"]


def __getattr__(name: str) -> object:
    if name in __all__:
        # CLI help and protocol validation do not need Server or backend initialization.
        from powercontext.ops.runtime import Ops, TargetBinding

        globals().update(Ops=Ops, TargetBinding=TargetBinding)
        return globals()[name]
    message = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(message)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
