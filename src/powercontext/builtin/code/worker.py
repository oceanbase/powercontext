# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software distributed
# under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
# CONDITIONS OF ANY KIND, either express or implied. See the License for the
# specific language governing permissions and limitations under the License.

"""Resource-limited parser worker; only accepts service-owned job manifests."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from powercontext.builtin.code.capture import json_bytes, write_private
from powercontext.builtin.code.errors import CodeError
from powercontext.builtin.code.extract import PythonExtractor, text_facts
from powercontext.builtin.code.languages import LANGUAGES
from powercontext.builtin.code.polyglot import PolyglotExtractor, grammar_key


def run(manifest_path: Path) -> None:
    import resource

    job = json.loads(manifest_path.read_bytes())
    memory = job["memory_bytes"]
    resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
    directory = manifest_path.parent
    extractors: dict[tuple[str, bool], PythonExtractor | PolyglotExtractor] = {}
    for number, entry in enumerate(job["files"]):
        print(f"begin:{number}", flush=True)
        content = (directory / "source" / entry["sha256"]).read_bytes()
        try:
            language = entry["language"]
            if language in LANGUAGES:
                key = grammar_key(entry["path"], language)
                if key not in extractors:
                    extractors[key] = (
                        PythonExtractor() if language == "python" else PolyglotExtractor(language, tsx=key[1])
                    )
                facts = extractors[key].extract(entry["path"], content, timeout=job["parse_seconds"])
            else:
                facts = text_facts(entry["path"], content)
        except CodeError as error:
            if error.code != "parse_timeout":
                raise
            facts = text_facts(entry["path"], content)
            facts["nodes"][0].update(language=entry["language"], parse_status="failed")
            facts["errors"] = [{"reason": "parse_timeout", "line": 1}]
        write_private(directory / "facts" / f"{entry['extraction_key']}.json", json_bytes(facts))
        print(f"end:{number}", flush=True)


if __name__ == "__main__":
    try:
        run(Path(sys.argv[1]))
    except (CodeError, OSError, ValueError, MemoryError):
        # Parent reports a stable failure and never forwards parser stderr/source.
        sys.exit(1)
