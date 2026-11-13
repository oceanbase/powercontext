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

"""SQL vector encodings remain readable through the reference driver."""

import warnings

import numpy as np
import pytest
from pyobvector import VECTOR
from pyobvector.util.vector import Vector
from sqlalchemy import bindparam, select
from sqlalchemy.dialects.mysql import dialect

from powercontext.builtin.persistence.oceanbase.vector import OceanBaseVector


def test_vector_encoding_preserves_float32_bits_and_reference_readback() -> None:
    rng = np.random.default_rng(261113)
    values = rng.integers(0, 2**32, size=8192, dtype=np.uint32).view(np.float32)
    values = values[np.isfinite(values)]
    values = np.concatenate((
        np.array([0, -0.0, np.finfo(np.float32).max, np.nextafter(np.float32(0), np.float32(1))], dtype=np.float32),
        values,
    ))
    sql_type = OceanBaseVector(len(values))
    encoded = sql_type.bind_processor(dialect())(values)
    decoded = VECTOR(len(values)).result_processor(dialect(), None)(encoded)
    assert np.array_equal(decoded.view(np.uint32), values.view(np.uint32))
    assert sql_type.get_col_spec() == f"VECTOR({len(values)})"


@pytest.mark.parametrize("form", ["list", "tuple", "native", "big_endian", "strided", "reversed", "vector"])
def test_vector_sql_literals_preserve_input_and_float32_values(form: str) -> None:
    array = np.array([1, -0.0, 0.125, -7.25], dtype=">f4")
    array.flags.writeable = False
    value = {
        "list": array.tolist(),
        "tuple": tuple(array.tolist()),
        "native": array.astype(np.float32),
        "big_endian": array,
        "strided": array[::2],
        "reversed": array[::-1],
        "vector": Vector(array),
    }[form]
    expected = Vector(value.to_numpy() if isinstance(value, Vector) else value)
    before = array.tobytes()
    sql_type = OceanBaseVector(expected.dim())
    statement = select(bindparam("embedding", value=value, type_=sql_type))
    compiled = str(statement.compile(dialect=dialect(), compile_kwargs={"literal_binds": True}))
    literal = compiled.split("'", 2)[1]
    assert Vector.from_text(literal).to_numpy().tobytes() == expected.to_numpy().tobytes()
    assert array.tobytes() == before and not array.flags.writeable


@pytest.mark.parametrize("value", [[np.nan], [np.inf, -np.inf], [1, np.nan, 2], []])
def test_special_values_retain_reference_encoding(value: list[float]) -> None:
    assert OceanBaseVector().bind_processor(dialect())(value) == VECTOR().bind_processor(dialect())(value)


def test_vector_subclasses_preserve_their_encoding_contract() -> None:
    class CustomVector(Vector):
        def to_text(self):
            return "[42.0]"

    masked = Vector(np.ma.array(np.arange(4, dtype=">f4"), mask=[False, True, False, False]))
    for value in (CustomVector([1]), masked):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            assert OceanBaseVector().bind_processor(dialect())(value) == VECTOR().bind_processor(dialect())(value)


def test_vector_dimensions_and_null_keep_the_driver_contract() -> None:
    sql_type = OceanBaseVector(3)
    encode = sql_type.bind_processor(dialect())
    assert encode(None) is None
    assert sql_type.result_processor(dialect(), None)(None) is None
    with pytest.raises(ValueError, match="expected 3 dimensions, not 2"):
        encode([1, 2])
    with pytest.raises(ValueError, match="expected ndim to be 1"):
        encode([[1, 2, 3]])
