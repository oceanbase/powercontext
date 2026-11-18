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

"""Best-effort credential recognition for automatic host inputs."""

from __future__ import annotations

import re

# A boolean or null value after the separator is a setting, not a credential.
_NOT_A_SETTING = r"(?!\s*(?:true|false|yes|no|on|off|null|none)(?:[\s,;)\]}]|\.(?!\S)|$))"
# Applied in order, like the TypeScript source, so an earlier rule consumes the whole of what a later one would cut.
_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE | re.ASCII)
    for pattern in (
        r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z0-9 ]*PRIVATE KEY-----|$)",
        # Headers can contain schemes, multiple cookies, and folded continuation lines. A hyphenated prefix such as
        # Proxy-, Set-, or X-Forwarded-, or the CGI form HTTP_, names another header of the same kind.
        r"""(?<![\w-])["']?(?:HTTP(?:_[A-Za-z0-9]+)*_|(?:[A-Za-z0-9]+-)*)(?:authorization|cookie)\b["']?"""
        rf"[ \t]*[:=]{_NOT_A_SETTING}[ \t]*[^\r\n]*(?:\r?\n[ \t]+[^\r\n]*)*",
        # A key name may carry any prefix, as in OPENAI_API_KEY, X-Api-Key, --password, or dbPassword, but must end
        # with the marker, so max_tokens and password_min_length stay ordinary. Tokenizer special tokens such as
        # pad_token are configuration. A scheme word belongs to the value that follows it.
        r"""(?<![\w-])["']?(?!(?:bos|eos|pad|unk|sep|cls|mask)_token\b)[\w-]*?(?:api[_ -]?key"""
        r"""|access[_ -]?(?:key(?:[_-]id)?|token)|client[_ -]?secret|secret(?:[_ -]?key)?|private[_ -]?key|password"""
        rf"""|passwd|passphrase|token|authorization|cookie)\b["']?\s*[:=]{_NOT_A_SETTING}\s*"""
        r"""(?:(?:bearer|basic)[ \t]+)?(?:"[^"\r\n]*"|'[^'\r\n]*'|`[^`\r\n]*`|[^\s,;}\]]+)""",
        r"(?<![\w-])bearer\s+[A-Za-z0-9._~+/=-]{8,}(?![\w-])",
        r"(?<![\w-])(?:sk-[A-Za-z0-9][A-Za-z0-9_-]{7,}|[sr]k_(?:live|test)_[A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9_]{8,}"
        r"|gh[pousr]_[A-Za-z0-9_]{8,}|xox[baprs]-[A-Za-z0-9-]{8,})(?![\w-])",
    )
)


def scrub_secrets(text: str, authorization: str | None = None) -> str:
    """Replace recognized values in a read-only query; never use this to rewrite a captured event."""

    if authorization:
        _, _, credential = authorization.partition(" ")
        if credential:
            text = text.replace(credential, "[REDACTED]")
    # The private-key rule runs first, so a header or assignment cannot consume just a PEM block's BEGIN marker.
    for pattern in _PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text
