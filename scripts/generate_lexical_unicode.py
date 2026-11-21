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


"""Export CPython word, casefold and assignment tables for the native analyzer.

Run with each supported CPython version after changing its Unicode data.
"""

import json
import unicodedata
from pathlib import Path


def ranges(values):
    result = []
    start = None
    previous = -1
    for number in values:
        if start is None:
            start = number
        elif number != previous + 1:
            result.append([start, previous])
            start = number
        previous = number
    if start is not None:
        result.append([start, previous])
    return result


def main():
    version = unicodedata.unidata_version
    names = {"14.0.0": "14", "15.0.0": "15", "15.1.0": "15_1", "16.0.0": "16"}
    name = names[version]
    chars = range(0x110000)
    data = {
        "unicode": version,
        "casefold": {str(n): chr(n).casefold() for n in chars if chr(n).casefold() != chr(n)},
        "word_ranges": ranges(n for n in chars if chr(n).isalnum() or chr(n) == "_"),
        "assigned_ranges": ranges(n for n in chars if unicodedata.category(chr(n)) != "Cn"),
        "frequency_correction": 3,
    }
    root = Path(__file__).resolve().parents[1]
    path = root / "src/powercontext/builtin/artifacts/lexical/data" / f"unicode{name}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{path.name}: Unicode {version}")


if __name__ == "__main__":
    main()
