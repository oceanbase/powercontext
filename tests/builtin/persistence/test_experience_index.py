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

from __future__ import annotations

import asyncio
import math
import sqlite3
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select, text
from sqlalchemy.dialects import mysql
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.schema import CreateTable

from powercontext.builtin.artifacts.experience import Experience, ExperienceContent
from powercontext.builtin.artifacts.skill import Skill, SkillContent
from powercontext.builtin.persistence.artifact_governance import ArtifactLifecycleState
from powercontext.builtin.persistence.experience_index import (
    ensure_artifact_head_searchable_text,
    experience_search_hits,
    skill_search_hits,
)
from powercontext.builtin.persistence.oceanbase.experience_index import OceanBaseExperienceFTSIndex
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.persistence.tables import ARTIFACT_HEADS_TABLE, BUILTIN_TABLES
from powercontext.builtin.runtime import BuiltinConfig, open_builtin_contexts
from powercontext.builtin.sources import ContentCapture


def _experience(keyword: str, lesson: str) -> ExperienceContent:
    return ExperienceContent(
        situation=f"A generated client contains the stale marker {keyword}.",
        action="Regenerate the client and inspect the resulting diff.",
        outcome="The checked-in client agrees with the public contract.",
        lesson=lesson,
    )


def _skill() -> SkillContent:
    return SkillContent(
        name="generated-client-check",
        description="Use after changing the public HTTP contract.",
        instructions="Regenerate the client and inspect the diff.",
        validation=("make contract-test passes",),
    )


def test_artifact_head_search_projection_schema_is_mysql_compilable() -> None:
    statement = str(CreateTable(ARTIFACT_HEADS_TABLE).compile(dialect=mysql.dialect()))

    assert "searchable_text MEDIUMTEXT" in statement
    assert "FOREIGN KEY(scope_id, family, artifact_id, revision)" in statement
    assert "pc_experience_heads" not in {table.name for table in BUILTIN_TABLES}


def test_sqlite_startup_upgrades_legacy_artifact_heads_without_searchable_text(tmp_path) -> None:
    database = tmp_path / "legacy.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            CREATE TABLE pc_artifact_heads (
                scope_id VARCHAR(256) NOT NULL,
                family VARCHAR(128) NOT NULL,
                artifact_id VARCHAR(128) NOT NULL,
                revision INTEGER NOT NULL,
                PRIMARY KEY (scope_id, family, artifact_id)
            )
            """
        )

    async def scenario() -> None:
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{database}"))
        for _ in range(2):
            async with (
                open_builtin_contexts(config) as contexts,
                contexts.database.transaction() as connection,
            ):
                columns = tuple((await connection.exec_driver_sql("PRAGMA table_info('pc_artifact_heads')")).mappings())
                assert tuple(column["name"] for column in columns).count("searchable_text") == 1

    asyncio.run(scenario())


def test_oceanbase_startup_upgrades_legacy_artifact_heads_with_mediumtext() -> None:
    connection = SimpleNamespace(
        dialect=SimpleNamespace(name="mysql"),
        scalar=AsyncMock(return_value=0),
        exec_driver_sql=AsyncMock(),
    )

    asyncio.run(ensure_artifact_head_searchable_text(cast(AsyncConnection, connection)))

    assert [call.args[0] for call in connection.exec_driver_sql.await_args_list] == [
        "ALTER TABLE pc_artifact_heads ADD COLUMN searchable_text MEDIUMTEXT NULL",
        "ALTER TABLE pc_artifact_heads ADD COLUMN lifecycle_state VARCHAR(16) NOT NULL DEFAULT 'active'",
        "ALTER TABLE pc_artifact_heads ADD COLUMN replacement_artifact_id VARCHAR(128) NULL",
        "ALTER TABLE pc_artifact_heads ADD COLUMN governance_generation BIGINT NOT NULL DEFAULT 0",
    ]


def test_sqlite_experience_fts_tracks_only_approved_current_heads_and_rebuilds() -> None:
    async def scenario() -> None:
        async with open_builtin_contexts(BuiltinConfig(database=SQLiteConfig())) as contexts:
            context = await contexts.get("project")
            async with contexts.database.transaction() as connection:
                experience_tables = set(
                    (
                        await connection.execute(
                            text(
                                "SELECT name FROM sqlite_master "
                                "WHERE name IN ('pc_experience_heads', 'pc_experience_fts_index', "
                                "'pc_experience_fts', 'pc_artifact_fts')"
                            )
                        )
                    ).scalars()
                )
            assert experience_tables == {"pc_artifact_fts"}

            first_source, _ = await context.sources.capture(
                ContentCapture(source_id="task-1", content="The first client repair passed.")
            )
            first_source_ref = context.sources.catalog.as_ref(first_source)
            review = contexts.review("project")
            candidate = await review.propose_experience(
                _experience("hamsterlegacy", "Run client generation before contract validation."),
                sources=(first_source_ref,),
                artifacts=(),
                target=None,
                reason=None,
            )

            assert await contexts.search_experience("project", "hamsterlegacy", 8) == ()

            approved = await review.approve(candidate.candidate_id, candidate.version)
            assert approved.result_artifact is not None
            first_outcome = await contexts.search_experience_outcome("project", "hamsterlegacy", 8)
            assert tuple(hit.artifact_ref for hit in first_outcome.hits) == (approved.result_artifact,)
            assert first_outcome.admission is not None
            assert first_outcome.admission.admitted == 1
            assert first_outcome.admission.retrieved >= 1
            scored_hit = first_outcome.hits[0]
            assert scored_hit.retrieval_score is not None
            assert scored_hit.channel_scores is not None
            raw = scored_hit.channel_scores["text"]
            assert raw.metric == "sqlite_bm25"
            assert raw.raw < 0
            assert raw.higher_is_better is False
            assert scored_hit.retrieval_score == -raw.raw / (1 - raw.raw)
            async with contexts.database.transaction() as connection:
                excluded = await contexts.experience_index.search(
                    connection, "project", "hamsterlegacy", 8, min_score=1, require_scores=True
                )
            assert excluded.hits == ()
            assert excluded.admission is not None
            assert excluded.admission.admitted == 1
            assert await contexts.search_experience("other-project", "hamsterlegacy", 8) == ()
            assert await contexts.search_experience("project", "situation outcome", 8) == ()

            second_source, _ = await context.sources.capture(
                ContentCapture(source_id="task-2", content="The corrected client repair passed.")
            )
            replacement = await review.propose_experience(
                _experience("falconcurrent", "Inspect generated changes before contract validation."),
                sources=(context.sources.catalog.as_ref(second_source),),
                artifacts=(approved.result_artifact,),
                target=approved.result_artifact,
                reason="The newer task evidence supersedes the stale marker.",
            )
            replaced = await review.approve(replacement.candidate_id, replacement.version)
            assert replaced.result_artifact is not None
            assert replaced.result_artifact.revision == 2
            assert await contexts.search_experience("project", "hamsterlegacy", 8) == ()
            current = await contexts.search_experience("project", "falconcurrent", 8)
            assert tuple(hit.artifact_ref for hit in current) == (replaced.result_artifact,)

            skill_candidate = await review.propose_skill(
                _skill(),
                sources=(context.sources.catalog.as_ref(second_source),),
                artifacts=(),
                target=None,
                reason=None,
            )
            skill_approval = await review.approve(skill_candidate.candidate_id, skill_candidate.version)
            assert skill_approval.result_artifact is not None
            skill_hits = await contexts.search_skills("project", "regenerate client", 8)
            assert tuple(hit.artifact_ref for hit in skill_hits) == (skill_approval.result_artifact,)
            assert skill_hits[0].retrieval_score is not None
            assert skill_hits[0].channel_scores is not None
            assert skill_hits[0].channel_scores["text"].metric == "sqlite_bm25"
            governance = await contexts.update_skill_lifecycle(
                "project",
                skill_approval.result_artifact.artifact_id,
                0,
                ArtifactLifecycleState.DEPRECATED,
                None,
            )
            assert governance.governance_generation == 1
            assert await contexts.search_skills("project", "regenerate client", 8) == ()
            deprecated = await contexts.list_skills("project", True, 8)
            assert deprecated[0][1].lifecycle_state is ArtifactLifecycleState.DEPRECATED
            reactivated = await contexts.update_skill_lifecycle(
                "project",
                skill_approval.result_artifact.artifact_id,
                1,
                ArtifactLifecycleState.ACTIVE,
                None,
            )
            assert reactivated.governance_generation == 2
            assert tuple(hit.artifact_ref for hit in await contexts.search_skills("project", "regenerate", 8)) == (
                skill_approval.result_artifact,
            )

            async with contexts.database.transaction() as connection:
                experience_searchable_text = await connection.scalar(
                    select(ARTIFACT_HEADS_TABLE.c.searchable_text).where(
                        ARTIFACT_HEADS_TABLE.c.scope_id == "project",
                        ARTIFACT_HEADS_TABLE.c.family == Experience.family,
                    )
                )
                skill_searchable_text = await connection.scalar(
                    select(ARTIFACT_HEADS_TABLE.c.searchable_text).where(
                        ARTIFACT_HEADS_TABLE.c.scope_id == "project",
                        ARTIFACT_HEADS_TABLE.c.family == Skill.family,
                    )
                )
                assert experience_searchable_text is not None
                assert "falconcurrent" in experience_searchable_text
                assert skill_searchable_text is not None
                assert "regenerate" in skill_searchable_text

                await connection.execute(
                    ARTIFACT_HEADS_TABLE
                    .update()
                    .where(ARTIFACT_HEADS_TABLE.c.family == Experience.family)
                    .values(searchable_text=None)
                )
                await connection.exec_driver_sql("DELETE FROM pc_artifact_fts")
            assert await contexts.search_experience("project", "falconcurrent", 8) == ()

            async with contexts.database.transaction() as connection:
                await contexts.experience_index.initialize(connection)
            rebuilt = await contexts.search_experience("project", "falconcurrent", 8)
            assert tuple(hit.artifact_ref for hit in rebuilt) == (replaced.result_artifact,)

    asyncio.run(scenario())


def _experience_row(artifact_id: str, raw_score: float, metric: str, *, keyword: str = "client") -> dict[str, object]:
    return {
        "artifact_id": artifact_id,
        "revision": 1,
        "content": _experience(keyword, "Validate the generated contract.").model_dump_json().encode(),
        "raw_score": raw_score,
        "score_metric": metric,
    }


@pytest.mark.parametrize("metric,raw", [("sqlite_bm25", -2.0), ("oceanbase_match", 2.0)])
def test_experience_scores_normalize_backend_direction_and_preserve_raw(metric: str, raw: float) -> None:
    outcome = experience_search_hits([_experience_row("one", raw, metric)], "client", 8, "project", require_scores=True)

    assert outcome.hits[0].retrieval_score == pytest.approx(2 / 3)
    assert outcome.hits[0].channel_scores is not None
    channel = outcome.hits[0].channel_scores["text"]
    assert channel.raw == raw
    assert channel.metric == metric
    assert channel.higher_is_better is (metric == "oceanbase_match")


@pytest.mark.parametrize("raw", [0.0, 1e308])
def test_experience_scores_accept_zero_and_large_finite_relevance(raw: float) -> None:
    hit = experience_search_hits(
        [_experience_row("one", raw, "oceanbase_match")], "client", 8, "project", require_scores=True
    ).hits[0]

    assert hit.retrieval_score == (0 if raw == 0 else 1)
    assert hit.retrieval_score is not None
    assert math.isfinite(hit.retrieval_score)


@pytest.mark.parametrize(
    "metric,raw,error_name",
    [
        ("sqlite_bm25", 1.0, "InvalidSearchScore"),
        ("oceanbase_match", -1.0, "InvalidSearchScore"),
        ("oceanbase_match", math.inf, "InvalidSearchScore"),
        ("oceanbase_match", math.nan, "InvalidSearchScore"),
        ("unknown", 1.0, "UnsupportedScoreMetric"),
    ],
)
def test_experience_scores_reject_invalid_relevance(metric: str, raw: float, error_name: str) -> None:
    from powercontext.builtin.artifacts import search

    with pytest.raises(getattr(search, error_name)):
        experience_search_hits([_experience_row("one", raw, metric)], "client", 8, "project", require_scores=True)


def test_experience_threshold_keeps_equal_scores_and_counts_lexical_admission() -> None:
    rows = [
        _experience_row("rejected", math.nan, "oceanbase_match", keyword="unrelated"),
        _experience_row("equal", 1.0, "oceanbase_match"),
        _experience_row("below", 0.25, "oceanbase_match"),
    ]
    # The invalid score belongs to a lexically rejected row and is never normalized.
    rows[0]["content"] = (
        ExperienceContent(situation="unrelated", action="ignore", outcome="absent", lesson="none")
        .model_dump_json()
        .encode()
    )
    outcome = experience_search_hits(rows, "client", 3, "project", min_score=0.5, require_scores=True)

    assert tuple(hit.artifact_ref.artifact_id for hit in outcome.hits) == ("equal",)
    assert outcome.admission is not None
    assert (outcome.admission.retrieved, outcome.admission.admitted) == (3, 2)


def test_experience_legacy_stops_at_limit_and_accepts_unscored_custom_rows() -> None:
    rows = [_experience_row("one", 2.0, "oceanbase_match"), _experience_row("two", 1.0, "oceanbase_match")]
    for row in rows:
        del row["raw_score"]
        del row["score_metric"]
    outcome = experience_search_hits(rows, "client", 1, "project")

    assert tuple(hit.artifact_ref.artifact_id for hit in outcome.hits) == ("one",)
    assert outcome.hits[0].retrieval_score is None
    assert outcome.admission is not None
    assert (outcome.admission.retrieved, outcome.admission.admitted) == (1, 1)
    with pytest.raises(RuntimeError):
        experience_search_hits(rows, "client", 1, "project", require_scores=True)


def test_skill_threshold_uses_same_score_and_admission_contract() -> None:
    from powercontext.builtin.artifacts.search import AdmissionFloor

    rows = [
        {
            "artifact_id": "equal",
            "revision": 1,
            "content": _skill().model_dump_json().encode(),
            "raw_score": -1.0,
            "score_metric": "sqlite_bm25",
        },
        {
            "artifact_id": "below",
            "revision": 1,
            "content": _skill().model_dump_json().encode(),
            "raw_score": -0.25,
            "score_metric": "sqlite_bm25",
        },
    ]
    hits = skill_search_hits(
        rows,
        "client unrelated absent",
        3,
        admission=AdmissionFloor(lexical_min_matched_terms=1),
        min_score=0.5,
        require_scores=True,
    )

    assert tuple(hit.artifact_ref.artifact_id for hit in hits) == ("equal",)
    assert hits[0].retrieval_score == 0.5
    assert hits[0].channel_scores is not None
    assert hits[0].channel_scores["text"].raw == -1


def test_oceanbase_search_selects_match_raw_and_preserves_candidate_window() -> None:
    rows = [_experience_row("one", 2.0, "oceanbase_match")]
    connection = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(mappings=lambda: rows)))
    outcome = asyncio.run(
        OceanBaseExperienceFTSIndex().search(
            cast(AsyncConnection, connection), "project", "client", 8, require_scores=True
        )
    )
    statement = connection.execute.await_args.args[0]
    compiled = statement.compile(dialect=mysql.dialect())
    sql = str(compiled)

    assert "AS raw_score" in sql
    assert "MATCH (pc_artifact_heads.searchable_text) AGAINST" in sql
    assert "ORDER BY MATCH" in sql
    assert " DESC, pc_artifact_heads.artifact_id, pc_artifact_heads.revision" in sql
    assert 32 in compiled.params.values()
    assert outcome.hits[0].channel_scores is not None
    assert outcome.hits[0].channel_scores["text"].metric == "oceanbase_match"
