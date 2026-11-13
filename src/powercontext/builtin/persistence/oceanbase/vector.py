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

"""Float32 vector SQL encoding for the OceanBase persistence boundary."""

from typing import Any

import numpy as np
import orjson
from pyobvector import VECTOR
from pyobvector.util.vector import Vector
from sqlalchemy import String
from sqlalchemy.engine import Dialect
from typing_extensions import override


def _vector_text(value: Any, dimension: int | None) -> str | None:
    if value is None:
        return None
    vector = value if isinstance(value, Vector) else Vector(value)
    if dimension is not None and vector.dim() != dimension:
        raise ValueError(f"expected {dimension} dimensions, not {vector.dim()}")  # noqa: TRY003 -- Driver contract.
    # Preserve custom Vector formatting and ndarray subclass semantics, including
    # masks. Non-finite values retain the driver's spelling instead of JSON null.
    if type(vector) is not Vector:
        return vector.to_text()
    array = vector.to_numpy()
    if type(array) is not np.ndarray or not np.isfinite(array).all():
        return vector.to_text()
    # pyobvector stores big-endian float32. Convert without mutating its storage;
    # OPT_SERIALIZE_NUMPY avoids allocating and formatting one Python float per item.
    native = np.ascontiguousarray(array, dtype=np.float32)
    return orjson.dumps(native, option=orjson.OPT_SERIALIZE_NUMPY).decode("utf-8")


class OceanBaseVector(VECTOR):
    """Use native array serialization with the driver's dimension/readback contract."""

    cache_ok = True

    @override
    def bind_processor(self, dialect: Dialect):
        def process(value: Any) -> str | None:
            return _vector_text(value, self.dim)

        return process

    @override
    def literal_processor(self, dialect: Dialect):
        quote = String().literal_processor(dialect)

        def process(value: Any) -> str:
            return quote(_vector_text(value, self.dim))

        return process
