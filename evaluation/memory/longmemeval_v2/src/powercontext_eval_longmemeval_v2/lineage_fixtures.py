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

"""Versioned synthetic oracles, never inferred from Source trust or model text."""

from __future__ import annotations

from dataclasses import dataclass

FIXTURE_VERSION = "scope-local-lineage-fixtures-v2"


@dataclass(frozen=True)
class Entry:
    key: str
    text: str
    facts: tuple[str, ...]
    source: str | None = "evidence"
    previous: str | None = None


@dataclass(frozen=True)
class Query:
    text: str
    answers: tuple[str, ...]
    category: str = "literal"
    max_bytes: int = 8000


@dataclass(frozen=True)
class Case:
    category: str
    entries: tuple[Entry, ...]
    queries: tuple[Query, ...]
    held_out: bool = False


def fixture_cases() -> tuple[Case, ...]:
    """All cases fit the native 16-candidate bound; held-out thresholds never tune it."""
    repeated = "Atlas deployment endpoint is PORT8000."
    wrappers = (
        "PowerContext Server 地址",
        "PowerContext Server 地址是",
        "PowerContext Server 地址是什么",
        "PowerContext Server 地址是什么？",
        "的 PowerContext Server 地址是什么？",
        "我之前让你记住的 PowerContext Server 地址是什么？",
    )
    return (
        Case(
            "exact_concentration",
            (
                *(Entry(f"copy-{i:02}", repeated, ("PORT8000",)) for i in range(9)),
                Entry("region", "Atlas deployment region is REGIONWEST.", ("REGIONWEST",), "region"),
                Entry("owner", "Atlas deployment owner is TEAMBLUE.", ("TEAMBLUE",), "owner"),
            ),
            (Query("Atlas deployment", ("PORT8000", "REGIONWEST", "TEAMBLUE")),),
        ),
        Case(
            "common_evidence_paraphrases",
            (
                Entry("one", "Atlas deployment endpoint uses PORT8000.", ("PORT8000",)),
                Entry("two", "Atlas deployment endpoint runs on PORT8000.", ("PORT8000",)),
                Entry("three", "Atlas deployment endpoint listens at PORT8000.", ("PORT8000",)),
                Entry("four", "Atlas deployment endpoint is PORT8000.", ("PORT8000",)),
            ),
            (Query("Atlas deployment endpoint", ("PORT8000",)),),
        ),
        Case(
            "legitimate_shared_source",
            (
                Entry("port", repeated, ("PORT8000",)),
                Entry("region", "Atlas deployment region is REGIONWEST.", ("REGIONWEST",)),
                Entry("owner", "Atlas deployment owner is TEAMBLUE.", ("TEAMBLUE",)),
            ),
            (Query("Atlas deployment", ("PORT8000", "REGIONWEST", "TEAMBLUE")),),
        ),
        Case(
            "revision_history",
            (
                Entry("endpoint", "Atlas deployment endpoint was PORT7000.", ("PORT7000",), "old"),
                Entry("endpoint", repeated, ("PORT8000",), "new"),
                Entry(
                    "audit", "Atlas deployment historical endpoint was PORT7000.", ("PORT7000",), "old", "endpoint:1"
                ),
            ),
            (
                Query("Atlas deployment", ("PORT8000", "PORT7000")),
                Query("Atlas deployment endpoint", ("PORT8000",), "current_revision"),
            ),
        ),
        Case(
            "recurring_automation",
            tuple(
                Entry(
                    f"cycle-{i}",
                    f"Atlas deployment automation reported healthy at CYCLE{i}.",
                    (f"CYCLE{i}",),
                    f"cycle-{i}",
                )
                for i in range(6)
            ),
            (Query("Atlas deployment automation", tuple(f"CYCLE{i}" for i in range(6))),),
        ),
        Case(
            "unknown_evidence",
            (
                Entry("one", "Atlas deployment unknown entry has UNKNOWNONE.", ("UNKNOWNONE",), None),
                Entry("two", "Atlas deployment unknown entry has UNKNOWNTWO.", ("UNKNOWNTWO",), None),
            ),
            (Query("Atlas deployment unknown", ("UNKNOWNONE", "UNKNOWNTWO")),),
        ),
        Case(
            "artifact_only_evidence",
            (
                Entry("retained", "Retained ledger reference version HISTORYONE.", ("HISTORYONE",), None),
                Entry("retained", "Retained ledger reference version HISTORYTWO.", ("HISTORYTWO",), None),
                Entry(
                    "copy-a", "Atlas deployment artifact fact is ARTIFACTFACT.", ("ARTIFACTFACT",), None, "retained:1"
                ),
                Entry(
                    "copy-b", "Atlas deployment artifact fact is ARTIFACTFACT.", ("ARTIFACTFACT",), None, "retained:2"
                ),
            ),
            (Query("Atlas deployment artifact", ("ARTIFACTFACT",)),),
            True,
        ),
        Case(
            "query_form_baseline",
            (
                Entry(
                    "server",
                    "本项目的 PowerContext Server 地址固定在 127.0.0.1:8000；测试标记 SERVER8000。",
                    ("SERVER8000",),
                ),
            ),
            (
                *(Query(text, ("SERVER8000",), "cjk_positive") for text in wrappers),
                Query("PowerContext Server", ("SERVER8000",), "english_literal"),
                Query("量子香蕉烘焙配方", (), "negative"),
                Query("Where do I connect to the service?", (), "english_no_literal_evidence"),
            ),
        ),
        Case(
            "held_out_shared_source",
            (
                Entry("read", "Atlas deployment read endpoint is READ8100.", ("READ8100",)),
                Entry("write", "Atlas deployment write endpoint is WRITE8200.", ("WRITE8200",)),
                Entry("archive", "Atlas deployment archive endpoint is ARCHIVE8300.", ("ARCHIVE8300",)),
            ),
            (Query("Atlas deployment endpoint", ("READ8100", "WRITE8200", "ARCHIVE8300")),),
            True,
        ),
        Case(
            "held_out_byte_budget",
            tuple(
                Entry(
                    f"item-{i}",
                    f"Atlas deployment evidence {i}: " + "bounded detail " * 30 + f" ANSWER{i}.",
                    (f"ANSWER{i}",),
                    f"item-{i}",
                )
                for i in range(5)
            ),
            (Query("Atlas deployment evidence", tuple(f"ANSWER{i}" for i in range(5)), max_bytes=1000),),
            True,
        ),
    )
