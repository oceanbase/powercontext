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

"""Record real CPython and PowerContext public-entry Unicode behavior."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import re
import sys
import unicodedata
from collections.abc import Callable
from pathlib import Path
from typing import Any

import jieba
from fixture_codec import codepoints, decode_input, dump_json, load_cases, typed

import powercontext
from powercontext.builtin.artifacts import search
from powercontext.builtin.artifacts.topic_memory import (
    TopicMemoryContent,
    chunk_topic_memory_detail,
    prepare_topic_memory_projection,
)


def hashes(raw: bytes) -> dict[str, Any]:
    return {
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "lf_bytes": len(raw.replace(b"\r\n", b"\n")),
        "lf_sha256": hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest(),
    }


def error_record(error: Exception) -> dict[str, Any]:
    record: dict[str, Any] = {
        "type": type(error).__name__,
        "module": type(error).__module__,
        "message": str(error),
        "args": [typed(argument) if isinstance(argument, str) else str(argument) for argument in error.args],
    }
    if isinstance(error, UnicodeError):
        for name in ("encoding", "reason", "start", "end"):
            if hasattr(error, name):
                record[name] = getattr(error, name)
        if hasattr(error, "object"):
            record["object"] = typed(error.object) if isinstance(error.object, str) else {"hex": error.object.hex()}
    return record


def capture(call: Callable[[], object]) -> tuple[dict[str, Any], object]:
    try:
        value = call()
    except Exception as error:
        return {"status": "error", "error": error_record(error)}, None
    try:
        encoded = typed(value)
    except Exception as error:
        return {"status": "result_encoding_error", "error": error_record(error)}, None
    return {"status": "ok", "value": encoded}, value


def capture_raw(call: Callable[[], object]) -> tuple[dict[str, Any], object]:
    try:
        value = call()
    except Exception as error:
        return {"status": "error", "error": error_record(error)}, None
    return {
        "status": "ok",
        "python_type": f"{type(value).__module__}.{type(value).__qualname__}",
    }, value


def utf8_record(value: str, errors: str) -> dict[str, Any]:
    record, _ = capture(lambda: value.encode("utf-8", errors))
    return record


def character_properties(value: str) -> list[dict[str, Any]]:
    rows = []
    for character in value:
        rows.append({
            "codepoint": ord(character),
            "category": unicodedata.category(character),
            "assigned_by_generator_rule": unicodedata.category(character) != "Cn",
            "name": unicodedata.name(character, None),
            "combining": unicodedata.combining(character),
            "casefold": typed(character.casefold()),
            "nfc": typed(unicodedata.normalize("NFC", character)),
            "isalnum": character.isalnum(),
            "isdecimal": character.isdecimal(),
            "isdigit": character.isdigit(),
            "isnumeric": character.isnumeric(),
            "isspace": character.isspace(),
            "re_word": re.fullmatch(r"\w", character) is not None,
            "re_digit": re.fullmatch(r"\d", character) is not None,
            "re_whitespace": re.fullmatch(r"\s", character) is not None,
        })
    return rows


def normalized_prefix_bytes(value: str) -> list[int]:
    offsets = [0]
    for character in value:
        offsets.append(offsets[-1] + len(character.encode("utf-8", "surrogatepass")))
    return offsets


def span_observations(value: object) -> dict[str, Any]:
    normalized_record, normalized = capture(lambda: unicodedata.normalize("NFC", value).casefold())  # type: ignore[arg-type]
    raw_record, raw_terms = capture(lambda: search.lexical_terms_with_spans(value))  # type: ignore[arg-type]
    public_record, public_terms = capture(lambda: search.analyze_text_with_spans(value))  # type: ignore[arg-type]
    rows: list[dict[str, Any]] = []
    if raw_record["status"] == "ok" and public_record["status"] == "ok" and isinstance(normalized, str):
        byte_offsets = normalized_prefix_bytes(normalized)
        assert isinstance(raw_terms, tuple)  # noqa: S101 - invariant of the probed entry
        assert isinstance(public_terms, tuple)  # noqa: S101 - invariant of the probed entry
        for index, (word, start, end) in enumerate(raw_terms):
            public_term = public_terms[index] if index < len(public_terms) else None
            rows.append({
                "raw_term": typed(word),
                "public_term": typed(public_term) if public_term is not None else None,
                "codepoint_start": start,
                "codepoint_end": end,
                "public_codepoint_start": public_term[1] if public_term is not None else None,
                "public_codepoint_end": public_term[2] if public_term is not None else None,
                "utf8_surrogatepass_start": byte_offsets[start],
                "utf8_surrogatepass_end": byte_offsets[end],
            })
    return {
        "offset_basis": "NFC-then-casefold Python codepoints; byte columns are separate surrogatepass prefixes",
        "normalized": normalized_record,
        "normalized_utf8_strict": utf8_record(normalized, "strict") if isinstance(normalized, str) else None,
        "normalized_utf8_surrogatepass": utf8_record(normalized, "surrogatepass")
        if isinstance(normalized, str)
        else None,
        "lexical_terms_with_spans": raw_record,
        "analyze_text_with_spans": public_record,
        "rows": rows,
    }


def chunk_observations(value: object) -> dict[str, Any]:
    record, chunks = capture_raw(lambda: chunk_topic_memory_detail(value))  # type: ignore[arg-type]
    rows: list[dict[str, Any]] = []
    if record["status"] == "ok" and isinstance(value, str):
        byte_offsets = normalized_prefix_bytes(value)
        assert isinstance(chunks, tuple)  # noqa: S101 - invariant of the probed entry
        for chunk in chunks:
            rows.append({
                "ordinal": chunk.ordinal,
                "codepoint_start": chunk.start_offset,
                "codepoint_end": chunk.end_offset,
                "utf8_surrogatepass_start": byte_offsets[chunk.start_offset],
                "utf8_surrogatepass_end": byte_offsets[chunk.end_offset],
                "text": typed(chunk.text),
                "text_python_type": f"{type(chunk.text).__module__}.{type(chunk.text).__qualname__}",
                "policy_version": chunk.policy_version,
                "exact_original_slice": chunk.text == value[chunk.start_offset : chunk.end_offset],
            })
    return {
        "offset_basis": "original-input Python codepoints; byte columns are separate surrogatepass prefixes",
        "result": record,
        "rows": rows,
    }


def projection_record(content: object) -> dict[str, Any]:
    record, projection = capture_raw(lambda: prepare_topic_memory_projection(content))  # type: ignore[arg-type]
    return projection_summary(record, projection)


def projection_summary(record: dict[str, Any], projection: object) -> dict[str, Any]:
    if record["status"] != "ok":
        return {"result": record}
    chunks = [
        {
            "ordinal": chunk.ordinal,
            "codepoint_start": chunk.start_offset,
            "codepoint_end": chunk.end_offset,
            "text": typed(chunk.text),
            "policy_version": chunk.policy_version,
        }
        for chunk in projection.chunks
    ]
    return {
        "result": record,
        "topic_searchable_text": typed(projection.topic_searchable_text),
        "topic_embedding": typed(projection.topic_embedding),
        "chunk_embeddings": typed(projection.chunk_embeddings),
        "embedding_profile": typed(projection.embedding_profile),
        "chunks": chunks,
    }


def default_projection_observations() -> dict[str, Any]:
    content = TopicMemoryContent(title="😀", summary="ǰ Straße", detail="# H😀\r\n\r\nDurable recovery.")
    default = projection_record(content)
    explicit_record, explicit = capture_raw(
        lambda: prepare_topic_memory_projection(
            content,
            topic_embedding=None,
            chunk_embeddings=(),
            embedding_profile=None,
        )
    )
    explicit_summary = projection_summary(explicit_record, explicit)
    return {
        "content": {
            "title": typed(content.title),
            "summary": typed(content.summary),
            "detail": typed(content.detail),
        },
        "default": default,
        "explicit_none": explicit_summary,
        "default_equals_explicit_none": default == explicit_summary,
        "invalid_inputs": {
            identifier: projection_record(value)
            for identifier, value in (("none", None), ("zero", 0), ("false", False))
        },
    }


def case_observations(case: dict[str, Any]) -> dict[str, Any]:
    value = decode_input(case["input"])
    observations: dict[str, Any] = {
        "input_python_type": f"{type(value).__module__}.{type(value).__qualname__}",
        "analyze_text": capture(lambda: search.analyze_text(value))[0],  # type: ignore[arg-type]
        "spans": span_observations(value),
        "chunking": chunk_observations(value),
    }
    if isinstance(value, str):
        observations.update({
            "input_codepoints": codepoints(value),
            "codepoint_length": len(value),
            "utf16_code_units": len(value.encode("utf-16-le", "surrogatepass")) // 2,
            "utf8_strict": utf8_record(value, "strict"),
            "utf8_surrogatepass": utf8_record(value, "surrogatepass"),
            "nfc_before_casefold": capture(lambda: unicodedata.normalize("NFC", value).casefold())[0],
            "casefold_before_nfc": capture(lambda: unicodedata.normalize("NFC", value.casefold()))[0],
            "character_properties": character_properties(value),
        })
    return observations


def source_identities() -> dict[str, Any]:
    search_path = Path(search.__file__).resolve()
    from powercontext.builtin.artifacts.topic_memory import chunking

    tokenizer = search._tokenizer()
    tokenizer.initialize()
    dict_path = Path(tokenizer.get_dict_file().name).resolve()
    return {
        "powercontext_file": str(Path(powercontext.__file__).resolve()),
        "powercontext_sha256": hashes(Path(powercontext.__file__).read_bytes()),
        "search_file": str(search_path),
        "search_source": hashes(search_path.read_bytes()),
        "chunking_file": str(Path(chunking.__file__).resolve()),
        "chunking_source": hashes(Path(chunking.__file__).read_bytes()),
        "jieba_version": jieba.__version__,
        "jieba_file": str(Path(jieba.__file__).resolve()),
        "jieba_dictionary_file": str(dict_path),
        "jieba_dictionary": hashes(dict_path.read_bytes()),
        "jieba_total": tokenizer.total,
        "analyzer_version": search.TEXT_ANALYZER_VERSION,
        "hmm": False,
    }


def run(fixtures: Path) -> dict[str, Any]:
    fixture_identity, cases = load_cases(fixtures)
    return {
        "schema": "powercontext.unicode-contract.observations.v1",
        "evidence_kind": "REAL_CPYTHON_PUBLIC_ENTRY",
        "environment": {
            "executable": sys.executable,
            "executable_sha256": hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest(),
            "version": sys.version,
            "version_info": list(sys.version_info[:3]),
            "implementation": sys.implementation.name,
            "platform": platform.platform(),
            "maxunicode": sys.maxunicode,
            "unicodedata_version": unicodedata.unidata_version,
        },
        "fixtures": fixture_identity,
        "sources": source_identities(),
        "cases": [
            {"id": case["id"], "input": case["input"], "observations": case_observations(case)} for case in cases
        ],
        "default_projection": default_projection_observations(),
    }


def first_difference(actual: object, expected: object, path: str = "$") -> str:
    if type(actual) is not type(expected):
        return f"{path}: type {type(actual).__name__} != {type(expected).__name__}"
    if isinstance(actual, dict):
        if actual.keys() != expected.keys():
            return f"{path}: keys differ"
        for key in actual:
            if actual[key] != expected[key]:
                return first_difference(actual[key], expected[key], f"{path}.{key}")
    elif isinstance(actual, list):
        if len(actual) != len(expected):
            return f"{path}: length {len(actual)} != {len(expected)}"
        for index, (left, right) in enumerate(zip(actual, expected, strict=True)):
            if left != right:
                return first_difference(left, right, f"{path}[{index}]")
    return f"{path}: {actual!r} != {expected!r}"


def check(actual: dict[str, Any], expected: dict[str, Any]) -> None:
    for key in ("cases", "default_projection"):
        if actual[key] != expected[key]:
            message = f"target mismatch at {first_difference(actual[key], expected[key], key)}"
            raise AssertionError(message)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixtures", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--check", type=Path)
    arguments = parser.parse_args()
    actual = run(arguments.fixtures)
    dump_json(arguments.output, actual)
    if arguments.check:
        check(actual, json.loads(arguments.check.read_text(encoding="utf-8")))
    print(
        f"recorded {len(actual['cases'])} cases; CPython {'.'.join(map(str, actual['environment']['version_info']))}; "
        f"UCD {actual['environment']['unicodedata_version']}; output={arguments.output}"
    )


if __name__ == "__main__":
    main()
