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

from powercontext.builtin.inference.models import InferenceUsage
from powercontext.builtin.inference.usage import approved_usage, collect_approved_usage, record_approved_usage


def test_provider_collections_keep_nested_attempts_isolated_and_unknown_tokens_unknown() -> None:
    first = InferenceUsage(requests=1, input_tokens=10, output_tokens=20)
    second = InferenceUsage(requests=1, input_tokens=None, output_tokens=30, incomplete=True)
    with collect_approved_usage() as outer:
        record_approved_usage(first)
        with collect_approved_usage() as inner:
            record_approved_usage(second)
        record_approved_usage(first)
    assert approved_usage(inner) == second
    assert approved_usage(outer).requests == 2
    assert approved_usage(outer).input_tokens == 20
    assert approved_usage([first, second]).input_tokens is None
    assert approved_usage([first, second]).output_tokens == 50
