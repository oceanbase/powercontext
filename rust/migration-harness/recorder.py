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

"""Reference recorder for the migration harness.

The recorder executes an explicit, non-empty selection of real PowerContext entry points
(``analyze_text`` / ``analyze_text_with_spans`` / ``lexical_terms_with_spans`` by default) on a fixed
corpus and stores the lossless observations plus the identity of everything that produced them:
CPython build, Unicode database version, Jieba dictionary identity, source fingerprint and dependency
fingerprint.

Recording must happen **before** any import substitution: the recorder imports the Python reference
and refuses to run if a candidate/native module is already loaded under the same name.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.metadata
import json
import platform
import sys
import unicodedata
from collections.abc import Callable
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:  # allow `python rust/migration-harness/recorder.py`
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from codec import SCHEMA_ID, encode_exception, encode_value, write_records

REPO_ROOT = Path(__file__).resolve().parents[2]

# The closed selection set. A record is only as good as the entry points behind it, so the set is
# fixed here and recorded per file; an empty selection is a hard error, never a silent Python fallback.
SELECTORS: dict[str, Callable[[str], Any]] = {}


def _register_default_selectors() -> None:
    search = importlib.import_module("powercontext.builtin.artifacts.search")

    SELECTORS.update({
        "analyze_text": search.analyze_text,
        "analyze_text_with_spans": search.analyze_text_with_spans,
        "lexical_terms_with_spans": search.lexical_terms_with_spans,
    })


def _portable(path: str | None) -> dict[str, Any]:
    """Describe a path without leaking the recording machine's layout into public evidence."""

    if not path:
        return {"present": False}
    resolved = Path(path).resolve()
    return {
        "present": True,
        "name": resolved.name,
        "sha256": hashlib.sha256(resolved.read_bytes()).hexdigest() if resolved.is_file() else None,
    }


def source_fingerprint() -> dict[str, Any]:
    """Fingerprint the package sources that produced the recording."""

    package = importlib.import_module("powercontext")
    root = Path(package.__file__).resolve().parent
    digest = hashlib.sha256()
    files = 0
    for path in sorted(root.rglob("*.py")):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(path.read_bytes())
        files += 1
    try:
        checkout = root.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        checkout = "installed"
    return {
        "package": package.__name__,
        "checkout": checkout,
        "python_files": files,
        "source_sha256": digest.hexdigest(),
    }


def dependency_fingerprint() -> dict[str, Any]:
    """Record the resolvable identity of every non-stdlib module the recording depends on."""

    identity: dict[str, Any] = {}
    for name in ("jieba", "pydantic", "jsonschema", "rfc8785"):
        try:
            module = importlib.import_module(name)
        except ImportError:
            identity[name] = {"present": False}
            continue
        try:
            version = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            version = "unknown"
        identity[name] = {"present": True, "version": version, **_portable(getattr(module, "__file__", None))}
    jieba = importlib.import_module("jieba")
    if hasattr(jieba, "get_dict_file"):
        dictionary = Path(str(jieba.get_dict_file().name))
        raw = dictionary.read_bytes()
        identity["jieba"]["dictionary"] = {
            "path": dictionary.name,
            "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
    return identity


REFERENCE_MODULE = "powercontext.builtin.artifacts.search"
REFERENCE_RELATIVE_PATH = "builtin/artifacts/search.py"
NATIVE_SUFFIXES = frozenset({".pyd", ".so", ".dylib", ".dll"})


def reference_identity() -> dict[str, Any]:
    """Return the identity of the reference module, or raise if it is not the packaged reference.

    A file suffix alone does not establish provenance: a hand-written ``candidate.py`` loaded under
    the reference module name also ends in ``.py``. The guard therefore requires the module file to
    be exactly the packaged reference path **and** the registered callables to be defined in that
    very file.
    """

    search = importlib.import_module(REFERENCE_MODULE)
    package = importlib.import_module("powercontext")
    package_root = Path(package.__file__).resolve().parent
    module_file = Path(getattr(search, "__file__", "") or "").resolve()
    expected = (package_root / REFERENCE_RELATIVE_PATH).resolve()
    if module_file.suffix in NATIVE_SUFFIXES:
        message = f"refusing to record with a non-source module: {module_file.suffix}"
        raise RuntimeError(message)
    if module_file != expected:
        message = f"refusing to record: the imported module is not the packaged reference ({module_file} != {expected})"
        raise RuntimeError(message)
    for name, function in SELECTORS.items():
        if getattr(function, "__module__", None) != REFERENCE_MODULE:
            message = f"refusing to record: selector {name!r} does not belong to {REFERENCE_MODULE}"
            raise RuntimeError(message)
        code_file = Path(getattr(getattr(function, "__code__", None), "co_filename", "")).resolve()
        if code_file != expected:
            message = f"refusing to record: selector {name!r} is defined in {code_file}, not the reference"
            raise RuntimeError(message)
    loaded = sys.modules.get("powercontext.artifacts.search")
    if loaded is not None and getattr(loaded, "__file__", "").endswith(tuple(NATIVE_SUFFIXES)):
        message = "refusing to record after a native candidate module was imported"
        raise RuntimeError(message)
    return {"module": REFERENCE_MODULE, "relative_path": REFERENCE_RELATIVE_PATH, "file": module_file.name}


def ensure_reference_only() -> None:
    """Refuse to record after a candidate/native module replaced the reference implementation."""

    reference_identity()


def _case_text(case: dict[str, Any]) -> str:
    """Resolve a case input, preferring an explicit code point list over JSON text.

    A JSON escape pair (``"a\\ud83d\\ude00b"``) is folded into one scalar by ``json.loads``, so the
    pair form cannot be expressed as JSON text. ``codepoints`` keeps it observable.
    """

    if "codepoints" in case:
        codepoints = case["codepoints"]
        if not isinstance(codepoints, list) or not codepoints:
            message = f"corpus case {case.get('id')!r} codepoints must be a non-empty array"
            raise ValueError(message)
        if not all(
            isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 0x10FFFF for value in codepoints
        ):
            message = f"corpus case {case.get('id')!r} codepoints must be integers in 0..0x10FFFF"
            raise ValueError(message)
        return "".join(chr(value) for value in codepoints)
    text = case.get("input")
    if not isinstance(text, str):
        message = f"corpus case {case.get('id')!r} input must be a str"
        raise TypeError(message)
    return text


def load_corpus(path: Path) -> list[dict[str, Any]]:
    document = json.loads(path.read_text(encoding="utf-8"))
    cases = document.get("cases")
    if not isinstance(cases, list) or not cases:
        message = "corpus must contain a non-empty cases array"
        raise ValueError(message)
    identifiers = [case.get("id") for case in cases]
    if len(set(identifiers)) != len(identifiers):
        message = "corpus case ids must be unique"
        raise ValueError(message)
    resolved = []
    for case in cases:
        resolved.append({
            "id": case["id"],
            "note": case["note"],
            "input": _case_text(case),
            "codepoints": [ord(character) for character in _case_text(case)],
        })
    return resolved


def observe(function: Callable[[str], Any], text: str) -> dict[str, Any]:
    """Call one entry point and capture either its lossless result or its structured exception."""

    try:
        result = function(text)
    except Exception as error:
        return {"outcome": "raised", "exception": encode_exception(error)}
    return {"outcome": "returned", "value": encode_value(result)}


def record(corpus: Path, selectors: list[str]) -> dict[str, Any]:
    if not selectors:
        message = "the selector set must be non-empty"
        raise ValueError(message)
    unknown = sorted(set(selectors) - set(SELECTORS))
    if unknown:
        message = f"unknown selectors: {unknown}"
        raise ValueError(message)
    ensure_reference_only()
    cases = load_corpus(corpus)
    records = []
    for case in cases:
        text = case["input"]
        observations = {selector: observe(SELECTORS[selector], text) for selector in selectors}
        records.append({
            "schema": SCHEMA_ID,
            "record": "case",
            "id": case["id"],
            "note": case["note"],
            "input": encode_value(text),
            "selectors": selectors,
            "observations": observations,
        })
    return {
        "schema": SCHEMA_ID,
        "record": "manifest",
        "corpus": {
            "path": corpus.as_posix(),
            "bytes": len(corpus.read_bytes()),
            "sha256": hashlib.sha256(corpus.read_bytes()).hexdigest(),
            "cases": len(cases),
        },
        "selectors": selectors,
        "reference_module": reference_identity(),
        "python": {
            "version": platform.python_version(),
            "implementation": platform.python_implementation(),
            "unicode": unicodedata.unidata_version,
            "platform": sys.platform,
        },
        "source": source_fingerprint(),
        "dependencies": dependency_fingerprint(),
        "native_loaded": False,
        "records": len(records),
        "_records": records,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Record the Python reference for the migration harness.")
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--selector", action="append", default=[])
    parser.add_argument("--manifest", type=Path, default=None)
    arguments = parser.parse_args()

    _register_default_selectors()
    selectors = arguments.selector or sorted(SELECTORS)
    manifest = record(arguments.corpus, selectors)
    records = manifest.pop("_records")
    for row in records:
        row["reference"] = {
            "python": manifest["python"]["version"],
            "unicode": manifest["python"]["unicode"],
            "source_sha256": manifest["source"]["source_sha256"],
        }
    write_records(arguments.output, records)
    manifest_path = arguments.manifest or arguments.output.with_name(arguments.output.stem + ".manifest.json")
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        f"recorded {len(records)} cases x {len(selectors)} selectors on CPython "
        f"{manifest['python']['version']} / UCD {manifest['python']['unicode']} -> {arguments.output}"
    )
    print(f"manifest -> {manifest_path}")


if __name__ == "__main__":
    main()
