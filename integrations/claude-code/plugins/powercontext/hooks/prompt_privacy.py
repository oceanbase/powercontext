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

# Generated from src/powercontext_integration_lifecycle/prompt_privacy.py; do not edit.
# Edit the source and run `make integration-lifecycle` instead.

"""Best-effort credential recognition for automatic host inputs."""

from __future__ import annotations

import re

_PRIVATE_KEY = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z0-9 ]*PRIVATE KEY-----|$)",
    re.IGNORECASE | re.ASCII,
)
_PATTERN = re.compile(
    # Headers can contain schemes, multiple cookies, and folded continuation lines.
    r"""(?<![\w-])["']?\b(?:authorization|proxy-authorization|cookie|set-cookie)\b["']?"""
    r"[ \t]*[:=][ \t]*[^\r\n]*(?:\r?\n[ \t]+[^\r\n]*)*"
    r"""|(?<![\w-])["']?\b(?:api[_ -]?key|access[_ -]?(?:key|token)|client[_ -]?secret"""
    r"""|secret(?:[_ -]?key)?|private[_ -]?key|password|passwd|passphrase|token|authorization|cookie)\b"""
    r"""["']?\s*[:=]\s*(?:"[^"\r\n]*"|'[^'\r\n]*'|`[^`\r\n]*`|[^\s,;}\]]+)"""
    r"|(?<![\w-])bearer\s+[A-Za-z0-9._~+/=-]{8,}(?![\w-])"
    r"|(?<![\w-])(?:sk-[A-Za-z0-9][A-Za-z0-9_-]{7,}|github_pat_[A-Za-z0-9_]{8,}"
    r"|gh[pousr]_[A-Za-z0-9_]{8,}|xox[baprs]-[A-Za-z0-9-]{8,})(?![\w-])",
    re.IGNORECASE | re.ASCII,
)


def scrub_secrets(text: str, authorization: str | None = None) -> str:
    """Replace recognized values in a read-only query; never use this to rewrite a captured event."""

    if authorization:
        _, _, credential = authorization.partition(" ")
        if credential:
            text = text.replace(credential, "[REDACTED]")
    # Remove whole PEM blocks before an earlier assignment/header can consume their BEGIN marker.
    return _PATTERN.sub("[REDACTED]", _PRIVATE_KEY.sub("[REDACTED]", text))
