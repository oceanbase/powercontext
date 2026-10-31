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

"""Catalog descriptions for generated and explicit Atomic Memory writes."""


def default_description(text: str) -> str:
    """Use a bounded single-line prefix for explicit writes without a supplied label."""
    line = " ".join(text.split())
    return line if len(line) <= 200 else line[:199].rstrip() + "…"


DESCRIPTION_RULES = """
Write description as a short, informative catalog label that helps an agent decide whether to read the full text.
Name the specific subject and topic, rather than restating the complete fact or merely changing its punctuation.
Prefer a compact phrase, usually within 60 characters; the hard limit is 200 characters and one nonblank line.
Use the same natural language as the text. Leave detailed values, lists and explanations in text unless essential
for distinguishing this memory from other topics. Keep qualifiers needed to avoid a misleading label; do not add facts.
For example, text "登录采用 JWT; 访问令牌有效期为 30 分钟; 刷新令牌有效期为 7 天。" can have
description "JWT 登录方案与令牌有效期", rather than repeating both durations.
For an already minimal fact, a similar description is acceptable; do not make the label vague merely to shorten it.
""".strip()
