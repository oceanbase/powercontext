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

"""Exercise the installed native wheel through the default search entry point."""

from __future__ import annotations

import importlib.machinery
import inspect
import sys
from importlib.metadata import distribution
from pathlib import Path

from powercontext import _lexical
from powercontext.builtin.artifacts.search import analyze_text, analyze_text_with_spans


def main() -> None:
    notices = distribution("powercontext").read_text("licenses/LICENSES/lex/notice.md")
    if not notices or "zstd-sys" not in notices or "Python Jieba 0.42.1" not in notices:
        raise SystemExit("The installed native wheel is missing its third-party notices")  # noqa: TRY003 - installed distribution acceptance.
    extension = Path(_lexical.__file__).resolve()
    if not extension.is_relative_to(Path(sys.prefix).resolve()):
        raise SystemExit(f"Expected the installed wheel extension, received {extension}")  # noqa: TRY003 - command diagnostics describe the failed release check.
    if not any(str(extension).endswith(suffix) for suffix in importlib.machinery.EXTENSION_SUFFIXES):
        raise SystemExit("The lexical analyzer is not a native Python extension")  # noqa: TRY003 - command diagnostics describe the failed release check.
    if not inspect.isbuiltin(analyze_text) or analyze_text is not _lexical.analyze_text:
        raise SystemExit("The default search entry point does not use the native analyzer")  # noqa: TRY003 - command diagnostics describe the failed release check.
    for text in (
        "ABC ab ABC",
        "Straße CAFÉ",
        "中文文本 数据库查询 南京市长江大桥",
        "ǰ j\u030c İ \u0131 ß ẞ Σ ς",
        "a\ud800b",
        "a\U0001e4ec\u0300",
        "a\U0001e5ee\u0300",
    ):
        expected = " ".join(term for term, _start, _end in analyze_text_with_spans(text))
        if analyze_text(text) != expected:
            raise SystemExit("The installed native analyzer differs from the span contract")  # noqa: TRY003 - command diagnostics describe the failed release check.
    print(f"Verified installed native analyzer on Python {sys.version.split()[0]}")


if __name__ == "__main__":
    main()
