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

from powercontext.builtin.artifacts.profile.models import ProfileWriteContent, normalize_profile_markdown


def test_profile_markdown_is_canonicalized() -> None:
    content = ProfileWriteContent(content="\ufeff# 用户画像\r\n\r\ne\u0301\r\n\r\n")

    assert content.content == "# 用户画像\n\né\n"
    assert content.model_dump(by_alias=True) == {
        "restored_from_revision": None,
        "content": "# 用户画像\n\né\n",
    }


@pytest.mark.parametrize("value", ["", "\n\r\n", "\ufeff\n"])
def test_profile_markdown_rejects_blank_documents(value: str) -> None:
    with pytest.raises(ValidationError, match="must not be blank"):
        ProfileWriteContent(content=value)


def test_profile_markdown_enforces_the_encoded_byte_limit() -> None:
    with pytest.raises(ValueError, match="exceeds 256 KiB"):
        normalize_profile_markdown("界" * 100_000)
