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

"""Topic search modes and embedding orchestration at the shared service boundary."""

import asyncio
import sys

import pytest
from pydantic import ValidationError

from powercontext.builtin.artifacts.memory import EmbeddingProfile, MemoryQueryEmbedding
from powercontext.builtin.artifacts.topic_memory import (
    TopicArtifactSearchRequest,
    TopicMemoryCapabilities,
    TopicMemorySearchResult,
)
from powercontext.builtin.inference import EmbeddingResult, InferenceUnavailableError, InvalidInferenceOutputError


class Embedding:
    profile = EmbeddingProfile(profile_id="topic-v1", model="test", dimension=2)

    def __init__(self, failure: Exception | None = None):
        self.failure = failure
        self.calls = 0

    async def embed(self, texts, /):
        self.calls += 1
        if self.failure is not None:
            raise self.failure
        return EmbeddingResult(vectors=((1.0, 0.0),))


def _service(*, embedding=None, capabilities=None, browse=None, calls=None, observer=None):
    from powercontext.builtin.runtime.topic_memory_search import TopicMemorySearcher

    async def search(scope_id, query, /, **kwargs):
        if calls is not None:
            calls.append(kwargs)
        return TopicMemorySearchResult(mode=kwargs["mode"], artifacts=() if "artifact_request" in kwargs else None)

    return TopicMemorySearcher(
        search=search, get=None, browse=browse, embedding_model=embedding, capabilities=capabilities, observer=observer
    )


@pytest.mark.parametrize("mode", ["text", "vector", "hybrid"])
def test_topic_service_dispatches_explicit_modes(mode):
    embedding = Embedding()
    calls = []
    capabilities = TopicMemoryCapabilities(fts=True, vector=True, hybrid=True, embedding_profile=embedding.profile)
    service = _service(embedding=embedding, capabilities=capabilities, calls=calls)
    result = asyncio.run(service.search("scope", TopicArtifactSearchRequest(query="needle", mode=mode)))
    assert result.mode == ("fts" if mode == "text" else mode)
    assert result.artifacts == ()
    assert embedding.calls == (0 if mode == "text" else 1)
    assert calls[0]["artifact_request"].mode == mode


@pytest.mark.parametrize(
    "fields",
    [
        {"mode": "vector"},
        {"mode": "hybrid"},
        {"admission": {"min_semantic_similarity": 0.3}},
        {"fusion": {"method": "rrf", "params": {"weights": {"topic_vector": 0}}}},
    ],
)
def test_topic_service_validates_vector_requirements_before_empty_scope(fields):
    calls = []

    async def browse(scope_id, *, limit, after):
        raise AssertionError("invalid request must fail before browsing")  # noqa: TRY003

    service = _service(capabilities=TopicMemoryCapabilities(fts=True), browse=browse, calls=calls)
    with pytest.raises(ValidationError):
        asyncio.run(service.search("scope", TopicArtifactSearchRequest.model_validate({"query": "needle", **fields})))
    assert calls == []


def test_topic_service_default_transient_fallback_and_explicit_mode_failure():
    calls = []
    observations = []
    failure = InferenceUnavailableError("embed", "temporarily unavailable")
    embedding = Embedding(failure)
    capabilities = TopicMemoryCapabilities(fts=True, vector=True, hybrid=True, embedding_profile=embedding.profile)
    service = _service(
        embedding=embedding,
        capabilities=capabilities,
        calls=calls,
        observer=lambda mode, fallback: observations.append((mode, fallback)),
    )
    result = asyncio.run(service.search("scope", TopicArtifactSearchRequest(query="needle")))
    assert result.mode == "fts"
    assert result.embedding_calls == 1
    assert observations == [("fts", True)]
    with pytest.raises(InferenceUnavailableError):
        asyncio.run(service.search("scope", TopicArtifactSearchRequest(query="needle", mode="hybrid")))
    assert len(calls) == 1


def test_topic_service_does_not_fallback_to_all_zero_text_weights():
    failure = InferenceUnavailableError("embed", "temporarily unavailable")
    embedding = Embedding(failure)
    service = _service(
        embedding=embedding,
        capabilities=TopicMemoryCapabilities(fts=True, vector=True, hybrid=True, embedding_profile=embedding.profile),
    )
    request = TopicArtifactSearchRequest.model_validate({
        "query": "needle",
        "fusion": {"method": "rrf", "params": {"weights": {"topic_fts": 0, "detail_fts": 0}}},
    })
    with pytest.raises(InferenceUnavailableError):
        asyncio.run(service.search("scope", request))


def test_topic_service_legacy_profile_reuse_has_no_new_callback_keywords():
    embedding = Embedding()
    calls = []

    async def legacy(scope, query, /, *, limit, mode, query_vector, embedding_profile):
        calls.append((scope, query, query_vector, embedding_profile))
        return TopicMemorySearchResult(mode=mode)

    from powercontext.builtin.runtime.topic_memory_search import TopicMemorySearcher

    service = TopicMemorySearcher(search=legacy, get=None, embedding_model=embedding)
    result = asyncio.run(
        service.search_legacy(
            "scope",
            "needle",
            limit=4,
            query_embedding=MemoryQueryEmbedding(query_vector=(1.0, 0.0), embedding_profile=embedding.profile),
        )
    )
    assert result.embedding_calls == 0
    assert result.artifacts is None
    assert embedding.calls == 0
    assert calls[0][2] == (1.0, 0.0)


def test_topic_service_invalid_output_propagates_without_fallback():
    embedding = Embedding(InvalidInferenceOutputError("embed", "invalid shape"))
    service = _service(
        embedding=embedding,
        capabilities=TopicMemoryCapabilities(fts=True, vector=True, hybrid=True, embedding_profile=embedding.profile),
    )
    with pytest.raises(InvalidInferenceOutputError):
        asyncio.run(service.search("scope", TopicArtifactSearchRequest(query="needle")))


def test_topic_service_requires_declared_public_search_capabilities():
    service = _service()
    with pytest.raises(ValidationError):
        asyncio.run(service.search("scope", TopicArtifactSearchRequest(query="needle", mode="text")))


@pytest.mark.parametrize("mode", [None, "text", "vector", "hybrid"])
def test_topic_empty_scope_rejects_a_supplied_context_without_authority(mode):
    from powercontext.artifacts.search import ArtifactSearchExecutionContext
    from powercontext.builtin.runtime import BuiltinConfig, open_builtin_contexts
    from powercontext.builtin.runtime.topic_memory_search import TopicMemorySearcher
    from powercontext.server.authz import AccessDeniedError

    async def scenario():
        embedding = Embedding()
        async with open_builtin_contexts(BuiltinConfig(), embedding_model=embedding) as contexts:
            service = TopicMemorySearcher(
                search=contexts.search_topic_memories,
                browse=contexts.browse_topic_memories,
                embedding_model=embedding,
                capabilities=contexts.topic_memory_index.capabilities,
            )
            request = TopicArtifactSearchRequest.model_validate({
                "query": "needle",
                **({} if mode is None else {"mode": mode}),
            })
            with pytest.raises(AccessDeniedError):
                await service.search("scope", request, execution_context=ArtifactSearchExecutionContext())
            # Missing SDK context and an explicitly trusted local invocation retain local policy.
            assert (await service.search("scope", request)).hits == ()
            assert (
                await service.search(
                    "scope", request, execution_context=ArtifactSearchExecutionContext(trusted_local=True)
                )
            ).hits == ()
            with pytest.raises(AccessDeniedError):
                await service.search_legacy(
                    "scope", "needle", limit=4, execution_context=ArtifactSearchExecutionContext()
                )

    asyncio.run(scenario())


def test_topic_service_rejects_mismatched_embedding_profile_before_empty_scope():
    embedding = Embedding()
    profile = EmbeddingProfile(profile_id="other", model="other", dimension=2)

    async def browse(scope, *, limit, after):
        return ()

    service = _service(
        embedding=embedding,
        capabilities=TopicMemoryCapabilities(fts=True, vector=True, hybrid=True, embedding_profile=profile),
        browse=browse,
    )
    with pytest.raises(ValidationError):
        asyncio.run(service.search("scope", TopicArtifactSearchRequest(query="needle")))
    assert embedding.calls == 0


@pytest.mark.parametrize(
    "fields,path",
    [
        ({"mode": "text", "admission": {"min_semantic_similarity": 0.3}}, ("admission", "min_semantic_similarity")),
        ({"mode": "vector", "admission": {"lexical_coverage": 0.25}}, ("admission", "lexical_coverage")),
        (
            {"mode": "text", "fusion": {"method": "rrf", "params": {"weights": {"topic_vector": 0}}}},
            ("fusion", "params", "weights", "topic_vector"),
        ),
        ({"fusion": {"method": "unknown"}}, ("fusion", "method")),
    ],
)
def test_topic_service_reports_mode_parameter_conflicts_before_browsing(fields, path):
    embedding = Embedding()
    capabilities = TopicMemoryCapabilities(fts=True, vector=True, hybrid=True, embedding_profile=embedding.profile)
    service = _service(embedding=embedding, capabilities=capabilities)
    with pytest.raises(ValidationError) as error:
        asyncio.run(service.search("scope", TopicArtifactSearchRequest.model_validate({"query": "needle", **fields})))
    assert error.value.errors()[0]["loc"] == path


def test_topic_service_default_empty_scope_skips_embedding_but_validates_explicit_parameters():
    embedding = Embedding()
    calls = []

    async def browse(scope, *, limit, after):
        return ()

    capabilities = TopicMemoryCapabilities(fts=True, vector=True, hybrid=True, embedding_profile=embedding.profile)
    service = _service(embedding=embedding, capabilities=capabilities, browse=browse, calls=calls)
    result = asyncio.run(service.search("scope", TopicArtifactSearchRequest(query="needle")))
    assert result.mode == "fts"
    assert embedding.calls == 0
    with pytest.raises(ValidationError):
        asyncio.run(
            service.search(
                "scope",
                TopicArtifactSearchRequest.model_validate({
                    "query": "needle",
                    "fusion": {"method": "rrf", "params": {"weights": {"unknown": 0}}},
                }),
            )
        )


def test_topic_service_enforces_public_fts_query_term_limit_but_allows_vector_queries():
    embedding = Embedding()
    capabilities = TopicMemoryCapabilities(fts=True, vector=True, hybrid=True, embedding_profile=embedding.profile)
    service = _service(embedding=embedding, capabilities=capabilities)
    query = " ".join(f"term{index}" for index in range(65))
    with pytest.raises(ValidationError):
        asyncio.run(service.search("scope", TopicArtifactSearchRequest(query=query, mode="text")))
    result = asyncio.run(service.search("scope", TopicArtifactSearchRequest(query=query, mode="vector")))
    assert result.mode == "vector"


def test_topic_service_public_bare_embedding_timeout_is_a_service_failure():
    embedding = Embedding(TimeoutError("provider timeout"))
    service = _service(
        embedding=embedding,
        capabilities=TopicMemoryCapabilities(fts=True, vector=True, hybrid=True, embedding_profile=embedding.profile),
    )
    with pytest.raises(InferenceUnavailableError, match="embedding request timed out"):
        asyncio.run(service.search("scope", TopicArtifactSearchRequest(query="needle", mode="vector")))


def test_topic_service_text_fallback_runs_without_provider_exception_context():
    from powercontext.builtin.runtime.topic_memory_search import TopicMemorySearcher

    async def search(scope, query, /, **kwargs):
        assert sys.exception() is None
        return TopicMemorySearchResult(mode="fts")

    service = TopicMemorySearcher(
        search=search, embedding_model=Embedding(InferenceUnavailableError("embed", "provider unavailable"))
    )
    result = asyncio.run(service.search_legacy("scope", "needle", limit=2))
    assert result.mode == "fts"


def test_topic_service_missing_selected_exact_revision_is_a_contract_failure():
    from powercontext.artifacts import ArtifactRef
    from powercontext.artifacts.search import ArtifactSearchContractError, ArtifactSearchMatch
    from powercontext.builtin.artifacts.topic_memory import TopicMemorySearchHit
    from powercontext.builtin.persistence.errors import RepositoryNotFoundError
    from powercontext.builtin.runtime.topic_memory_search import TopicMemorySearcher

    ref = ArtifactRef(family="topic-memory", artifact_id="selected", revision=1)

    async def search(scope, query, /, **kwargs):
        return TopicMemorySearchResult(
            mode="fts",
            hits=(
                TopicMemorySearchHit(
                    artifact_ref=ref, title="Needle", summary="Needle", score=50, matched_by=("topic_fts",)
                ),
            ),
            matches=(ArtifactSearchMatch(ref, 0.5),),
        )

    async def get(scope, artifact_ref):
        raise RepositoryNotFoundError("artifact", artifact_ref)

    service = TopicMemorySearcher(search=search, get=get, capabilities=TopicMemoryCapabilities(fts=True))
    with pytest.raises(ArtifactSearchContractError):
        asyncio.run(service.search("scope", TopicArtifactSearchRequest(query="needle", mode="text")))
