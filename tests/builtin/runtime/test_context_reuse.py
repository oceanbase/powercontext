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

"""Exact prepared reuse against real persisted Atomic Memory lifecycle changes."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any

import pytest

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.atomic_memory.models import AtomicMemoryContent
from powercontext.builtin.artifacts.memory import EmbeddingProfile
from powercontext.builtin.inference import EmbeddingResult
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.records import ArtifactWrite
from powercontext.builtin.runtime import (
    BuiltinConfig,
    ContextAssembly,
    PrepareContextRequest,
    PreparedContext,
    PreparedContextWithReuse,
    open_builtin_runtime,
)
from powercontext.builtin.runtime.context_bindings import BindingCodec, ContextBindingError
from powercontext.builtin.runtime.context_reuse import (
    MAX_REUSE_EXPLANATION_BYTES,
    MAX_REUSE_EXPLANATION_CHANGES,
    ContextReuseChange,
    ContextReuseError,
    ContextReuseInvalidation,
    prepare_reuse,
    validate_reuse,
)
from powercontext.builtin.runtime.prepared_code import CodeEvidenceRef
from powercontext.builtin.runtime.prepared_context import PreparedContextBuild
from powercontext.builtin.scope import ScopeDraft
from powercontext.limits import MAX_ARTIFACT_ID_LENGTH


def _request(**changes):
    return PrepareContextRequest(
        query="alpha",
        include_reuse=True,
        assembly=ContextAssembly.model_validate({"sections": [{"family": "memory", "limit": 8}]}),
    ).model_copy(update=changes)


async def _scope(runtime):
    return await runtime.scopes.create(ScopeDraft(title="Reuse", summary="Exact context", idempotency_key="reuse"))


class _EmbeddingCalls:
    profile = EmbeddingProfile(profile_id="reuse-test", model="test", dimension=3, distance="l2", normalization="unit")

    def __init__(self):
        self.calls = 0

    async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        self.calls += 1
        return EmbeddingResult(vectors=((1.0, 0.0, 0.0),) * len(texts))


def test_reuse_preserves_final_value_and_revalidates_without_recalling(tmp_path):
    async def scenario():
        embedding = _EmbeddingCalls()
        async with open_builtin_runtime(BuiltinConfig(database=SQLiteConfig()), embedding_model=embedding) as runtime:
            scope = await _scope(runtime)
            assert runtime.scopes is not None
            assert runtime.atomic_memory is not None
            memory = runtime.atomic_memory.for_scope(scope.scope_id)
            created = await memory.create((
                AtomicMemoryContent(kind="fact", text="Alpha 中文约束; retain exact bytes."),
            ))
            scoped = runtime.context.for_scope(scope.scope_id)
            ordinary = await scoped.prepare(_request(include_reuse=False))
            assert isinstance(ordinary, PreparedContext)
            prepared = await scoped.prepare(_request(), binding_identity="user:alice")
            assert isinstance(prepared, PreparedContextWithReuse)
            assert prepared.context == ordinary
            assert prepared.context.content is not None
            assert created.primary.ref.artifact_id not in prepared.reuse.binding
            paid_embedding_calls = embedding.calls
            assert paid_embedding_calls > 0
            for _ in range(3):
                checked = await scoped.validate_reuse(prepared.reuse.binding, binding_identity="user:alice")
                assert checked.content_digest == sha256(prepared.context.content.encode("utf-8")).hexdigest()
                assert checked.content_bytes == prepared.context.content_bytes
                assert checked.ordered_roots == (created.primary.ref,)
                assert checked.trust == "historical-data"
            assert embedding.calls == paid_embedding_calls
            with pytest.raises(ContextBindingError, match="identity-mismatch"):
                await scoped.validate_reuse(prepared.reuse.binding, binding_identity="user:bob")

    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["revise", "description", "forget", "merge"])
def test_prepared_reuse_rejects_current_root_changes(change):
    async def scenario():
        async with open_builtin_runtime(BuiltinConfig(database=SQLiteConfig())) as runtime:
            scope = await _scope(runtime)
            assert runtime.scopes is not None
            assert runtime.atomic_memory is not None
            memory = runtime.atomic_memory.for_scope(scope.scope_id)
            created = await memory.create((AtomicMemoryContent(kind="fact", text="Alpha original constraint."),))
            scoped = runtime.context.for_scope(scope.scope_id)
            prepared = await scoped.prepare(_request())
            assert isinstance(prepared, PreparedContextWithReuse)
            primary = created.primary
            if change in {"revise", "description"}:
                await runtime.records.for_scope(scope.scope_id).replace_artifact(
                    "atomic-memory",
                    primary.ref.artifact_id,
                    '"revision:1"',
                    ArtifactWrite(
                        content={
                            "kind": "fact",
                            "text": "Alpha replacement constraint."
                            if change == "revise"
                            else primary.artifact.content.text,
                            "description": "Updated constraint summary.",
                        }
                    ),
                )
                if change == "description":
                    revised = await memory.get(primary.ref.artifact_id)
                    assert revised.artifact.content.text == primary.artifact.content.text
                    assert revised.artifact.content.description != primary.artifact.content.description
            elif change == "forget":
                await memory.forget(primary.ref.artifact_id, expected_revision=1, expected_state_version=0)
            else:
                other = await memory.create((AtomicMemoryContent(kind="fact", text="Another constraint."),))
                await memory.merge(
                    (primary.as_read(), other.primary.as_read()),
                    AtomicMemoryContent(kind="fact", text="Alpha merged constraint."),
                )
            with pytest.raises(ContextReuseError, match="source-changed"):
                await scoped.validate_reuse(prepared.reuse.binding)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("change", "reported"),
    [("revise", "revised"), ("forget", "forgotten"), ("merge", "merged"), ("undo_merge", "retired")],
)
def test_invalidated_root_is_named_without_blaming_the_unchanged_root(change, reported):
    async def scenario():
        async with open_builtin_runtime(BuiltinConfig(database=SQLiteConfig())) as runtime:
            scope = await _scope(runtime)
            assert runtime.scopes is not None
            assert runtime.atomic_memory is not None
            memory = runtime.atomic_memory.for_scope(scope.scope_id)
            steady = (await memory.create((AtomicMemoryContent(kind="fact", text="Alpha steady constraint."),))).primary
            first = await memory.create((AtomicMemoryContent(kind="fact", text="Alpha first input."),))
            second = await memory.create((AtomicMemoryContent(kind="fact", text="Alpha second input."),))
            target = (
                await memory.merge(
                    (first.primary.as_read(), second.primary.as_read()),
                    AtomicMemoryContent(kind="fact", text="Alpha combined constraint."),
                )
            ).primary
            scoped = runtime.context.for_scope(scope.scope_id)
            prepared = await scoped.prepare(_request())
            assert isinstance(prepared, PreparedContextWithReuse)
            roots = (await scoped.validate_reuse(prepared.reuse.binding)).ordered_roots
            assert sorted(ref.artifact_id for ref in roots) == sorted((steady.ref.artifact_id, target.ref.artifact_id))
            if change == "revise":
                await runtime.records.for_scope(scope.scope_id).replace_artifact(
                    "atomic-memory",
                    target.ref.artifact_id,
                    '"revision:1"',
                    ArtifactWrite(content={"kind": "fact", "text": "Alpha combined replacement."}),
                )
            elif change == "forget":
                await memory.forget(target.ref.artifact_id, expected_revision=1, expected_state_version=0)
            elif change == "merge":
                other = await memory.create((AtomicMemoryContent(kind="fact", text="Another constraint."),))
                await memory.merge(
                    (target.as_read(), other.primary.as_read()),
                    AtomicMemoryContent(kind="fact", text="Alpha final constraint."),
                )
            else:
                await memory.restore(target.ref.artifact_id, operation="undo_merge")
            with pytest.raises(ContextReuseError) as rejected:
                await scoped.validate_reuse(prepared.reuse.binding)
            # The decision and its code are unchanged; the explanation names only the root that changed.
            assert rejected.value.code == "context-reuse-source-changed"
            assert rejected.value.invalidation == ContextReuseInvalidation(
                attribution="partial",
                changes=(ContextReuseChange(subject="root", change=reported, root=target.ref),),
                truncated=False,
            )

    asyncio.run(scenario())


def test_denied_revalidation_reveals_nothing_about_changed_roots(tmp_path):
    from powercontext.artifacts.search import ArtifactSearchExecutionContext
    from powercontext.server.authz import AccessDeniedError, PrincipalRef
    from tests.e2e.test_access_control_regressions import AUDIT, _grant, _server
    from tests.e2e.test_access_control_regressions import _scope as http_scope
    from tests.e2e.test_atomic_memory_prepare_lifecycle import _change, _remember, _revoke

    async def scenario():
        async with _server(tmp_path) as (app, client, access):
            scope_id = await http_scope(client)
            changed = await _remember(client, scope_id, "Alpha private constraint.")
            await _remember(client, scope_id, "Beta unrelated note.")
            grant = await _grant(client, scope_id, "alice", "scope.viewer")
            context = ArtifactSearchExecutionContext(
                principal=PrincipalRef(type="user", id="alice"), access=access, audit=AUDIT
            )
            scoped = app.state.application.context.for_scope(scope_id)

            async def prepare(query):
                prepared = await scoped.prepare(_request(query=query), atomic_context=context, binding_identity="alice")
                assert isinstance(prepared, PreparedContextWithReuse)
                return prepared.reuse.binding

            async def rejection(binding):
                with pytest.raises((ContextReuseError, AccessDeniedError)) as rejected:
                    await scoped.validate_reuse(binding, atomic_context=context, binding_identity="alice")
                return rejected.value

            stale, fresh = await prepare("alpha"), await prepare("beta")
            await _change(client, scope_id, changed, "forget")
            explained = await rejection(stale)
            assert isinstance(explained, ContextReuseError)
            assert explained.invalidation is not None
            assert [item.change for item in explained.invalidation.changes] == ["forgotten"]
            await scoped.validate_reuse(fresh, atomic_context=context, binding_identity="alice")

            # Without current authority, a changed and an unchanged binding fail identically and explain nothing.
            await _revoke(client, grant)
            denied_stale, denied_fresh = await rejection(stale), await rejection(fresh)
            assert type(denied_stale) is type(denied_fresh) is AccessDeniedError
            assert denied_stale.args == denied_fresh.args
            assert not hasattr(denied_stale, "invalidation")

    asyncio.run(scenario())


class _Evidence:
    """One Atomic Memory dependency snapshot, as read when a binding is issued or revalidated."""

    def __init__(self, snapshot: list[dict[str, Any]]) -> None:
        self.snapshot = snapshot

    async def read_dependency_snapshot(self, roots, *, context=None, selected=None):
        return self.snapshot


class _UnchangedFiles:
    async def file_digests(self, paths):
        return dict.fromkeys(paths, "0" * 64)


def _root(artifact_id: str) -> ArtifactRef:
    return ArtifactRef(family="atomic-memory", artifact_id=artifact_id, revision=1)


def _artifact_node(ref: ArtifactRef, digest: str = "a") -> dict[str, Any]:
    state = {"state": "active", "state_version": 0, "merged_into_id": None}
    return {"kind": "artifact", "ref": ref.model_dump(mode="json"), "digest": digest * 64, "state": state}


def _source_node(source_id: str, digest: str = "a") -> dict[str, Any]:
    ref = {"source_type": "document", "source_id": source_id}
    return {"kind": "source", "ref": ref, "digest": digest * 64, "journal_position": 1}


def _revalidated(roots, recorded, current, *, cite_code=False) -> ContextReuseError:
    """Issue a real binding over recorded evidence; return the rejection of revalidating it against current evidence."""
    codec, now = BindingCodec(), datetime.now(UTC)
    cited = CodeEvidenceRef(
        scope_id="scope",
        fingerprint="fingerprint",
        path="src/private.py",
        file_sha256="0" * 64,
        start_line=1,
        end_line=1,
        snippet_sha256="1" * 64,
    )
    build = replace(
        PreparedContextBuild(
            context=PreparedContext(status="ready", content="Context", content_bytes=7), origins=roots
        ),
        code_origins=(cited,) if cite_code else (),
    )

    async def scenario():
        prepared = await prepare_reuse(
            _Evidence(recorded),  # ty: ignore[invalid-argument-type] only the dependency snapshot is read.
            "scope",
            build,
            build.context,
            codec=codec,
            binding_identity="local",
            now=now,
            context=None,
            code=_UnchangedFiles(),  # ty: ignore[invalid-argument-type] only cited file digests are read.
        )
        with pytest.raises(ContextReuseError) as rejected:
            await validate_reuse(
                _Evidence(current),  # ty: ignore[invalid-argument-type] only the dependency snapshot is read.
                "scope",
                prepared.reuse.binding,
                codec=codec,
                binding_identity="local",
                now=now,
                context=None,
            )
        return rejected.value

    return asyncio.run(scenario())


@pytest.mark.parametrize(("cite_code", "attribution"), [(False, "complete"), (True, "partial")])
def test_snapshot_changes_name_only_bound_roots_and_never_guess_ancestry(cite_code, attribution):
    first, second = _root("root-a"), _root("root-b")
    ancestor = ArtifactRef(family="atomic-memory", artifact_id="private-ancestor", revision=3)
    recorded = [
        _artifact_node(first),
        _artifact_node(ancestor),
        _source_node("private-kept"),
        _source_node("private-gone"),
        _artifact_node(second),
    ]
    current = [
        _artifact_node(first),
        _artifact_node(ancestor, "b"),
        _source_node("private-kept"),
        _artifact_node(second, "b"),
        _source_node("private-new"),
    ]
    rejected = _revalidated((first, second), recorded, current, cite_code=cite_code)
    assert rejected.code == "context-reuse-source-changed"
    # Cited code is compared after Memory, so a Memory change cannot claim the files were unchanged.
    assert rejected.invalidation == ContextReuseInvalidation(
        attribution=attribution,
        changes=(
            ContextReuseChange("root", "changed", second),
            ContextReuseChange("ancestor", "changed"),
            ContextReuseChange("source", "removed"),
            ContextReuseChange("source", "added"),
        ),
        truncated=False,
    )
    assert rejected.invalidation is not None
    assert "private" not in rejected.invalidation.to_json()


@pytest.mark.parametrize("identity", ["short", "long"])
def test_explanation_keeps_a_deterministic_prefix_within_count_and_byte_bounds(identity):
    def artifact_id(index: int) -> str:
        return f"root-{index:02d}" if identity == "short" else f"{index:02d}" + "記" * (MAX_ARTIFACT_ID_LENGTH - 2)

    roots = tuple(_root(artifact_id(index)) for index in range(MAX_REUSE_EXPLANATION_CHANGES + 4))
    recorded, current = [_artifact_node(ref) for ref in roots], [_artifact_node(ref, "b") for ref in roots]
    first, again = (_revalidated(roots, recorded, current) for _ in range(2))
    assert first.code == again.code == "context-reuse-source-changed"
    assert first.invalidation == again.invalidation
    explanation = first.invalidation
    assert explanation is not None
    assert explanation.attribution == "partial"
    assert explanation.truncated
    kept = len(explanation.changes)
    assert explanation.changes == tuple(ContextReuseChange("root", "changed", ref) for ref in roots[:kept])
    assert len(explanation.to_json().encode("utf-8")) <= MAX_REUSE_EXPLANATION_BYTES
    if identity == "short":
        assert kept == MAX_REUSE_EXPLANATION_CHANGES
    else:
        assert 0 < kept < MAX_REUSE_EXPLANATION_CHANGES


@pytest.mark.parametrize("current", ["malformed", "reordered"])
def test_unexplainable_snapshot_change_stays_rejected_and_explains_nothing(current):
    roots = (_root("root-a"), _root("root-b"))
    recorded = [_artifact_node(ref) for ref in roots]
    changed = [{"kind": "unknown"}, *recorded] if current == "malformed" else recorded[::-1]
    rejected = _revalidated(roots, recorded, changed)
    assert rejected.code == "context-reuse-source-changed"
    assert rejected.invalidation == ContextReuseInvalidation("unavailable", (), truncated=False)


def test_merged_memory_keeps_exact_inactive_ancestry_eligible():
    async def scenario():
        async with open_builtin_runtime(BuiltinConfig(database=SQLiteConfig())) as runtime:
            scope = await _scope(runtime)
            assert runtime.scopes is not None
            assert runtime.atomic_memory is not None
            memory = runtime.atomic_memory.for_scope(scope.scope_id)
            first = await memory.create((AtomicMemoryContent(kind="fact", text="Alpha first constraint."),))
            second = await memory.create((AtomicMemoryContent(kind="fact", text="Alpha second constraint."),))
            merged = await memory.merge(
                (first.primary.as_read(), second.primary.as_read()),
                AtomicMemoryContent(kind="fact", text="Alpha merged constraints."),
            )
            scoped = runtime.context.for_scope(scope.scope_id)
            prepared = await scoped.prepare(_request())
            assert isinstance(prepared, PreparedContextWithReuse)
            checked = await scoped.validate_reuse(prepared.reuse.binding)
            assert checked.ordered_roots == (merged.primary.ref,)
            assert prepared.context.content is not None
            assert "Alpha merged constraints." in prepared.context.content
            assert "Alpha first constraint." not in prepared.context.content

    asyncio.run(scenario())


@pytest.mark.parametrize("receipt", [False, True])
@pytest.mark.parametrize("empty_sections", [False, True])
def test_empty_prepare_envelope_remains_explicit_and_bound(receipt, empty_sections):
    async def scenario():
        async with open_builtin_runtime(BuiltinConfig(database=SQLiteConfig())) as runtime:
            scope = await _scope(runtime)
            assert runtime.scopes is not None
            assert runtime.atomic_memory is not None
            scoped = runtime.context.for_scope(scope.scope_id)
            request = _request(include_receipt=receipt)
            if empty_sections:
                request = request.model_copy(update={"assembly": ContextAssembly(sections=())})
            prepared = await scoped.prepare(request)
            assert isinstance(prepared, PreparedContextWithReuse)
            assert prepared.context.status == "empty"
            assert prepared.context.content is None
            assert prepared.context.content_bytes == 0
            if receipt:
                assert prepared.context.receipt is not None
                assert prepared.context.receipt.used_bytes == 0
                assert prepared.context.receipt.content_digest is None
                assert prepared.context.receipt.selected == ()
            else:
                assert prepared.context.receipt is None
            checked = await scoped.validate_reuse(prepared.reuse.binding)
            assert checked.content_bytes == 0
            assert checked.ordered_roots == ()
            # Without a configured code index, code is simply unavailable and the proof cites no code.
            with_code = await scoped.prepare(_request(include_code=True))
            assert isinstance(with_code, PreparedContextWithReuse)
            assert (await scoped.validate_reuse(with_code.reuse.binding)).ordered_roots == ()

    asyncio.run(scenario())


def test_reuse_binding_expires_without_extending_its_preparation():
    async def scenario():
        async with open_builtin_runtime(BuiltinConfig(database=SQLiteConfig())) as runtime:
            scope = await _scope(runtime)
            assert runtime.scopes is not None
            assert runtime.atomic_memory is not None
            scoped = runtime.context.for_scope(scope.scope_id)
            prepared = await scoped.prepare(_request())
            assert isinstance(prepared, PreparedContextWithReuse)
            runtime._clock = lambda: prepared.reuse.expires_at
            with pytest.raises(ContextBindingError, match="expired"):
                await scoped.validate_reuse(prepared.reuse.binding)

    asyncio.run(scenario())


def test_binding_requires_exact_deployment_generation_purpose_key_and_unexpired_ciphertext():
    now = datetime(2026, 10, 26, tzinfo=UTC)
    arguments: dict[str, Any] = {
        "key": b"x" * 32,
        "key_id": "v1",
        "deployment_id": "deployment-a",
        "generation": "generation-a",
    }
    codec = BindingCodec(**arguments)
    value = {"private_dependency": "secret identity"}
    token = codec.seal(value, purpose="context-reuse", expires_at=now + timedelta(minutes=1))
    assert "secret identity" not in token
    assert BindingCodec(**arguments).open(token, purpose="context-reuse", now=now) == value
    for changes in (
        {"key": b"y" * 32},
        {"key_id": "v2"},
        {"deployment_id": "other"},
        {"generation": "other"},
    ):
        options: dict[str, Any] = arguments | changes
        with pytest.raises(ContextBindingError, match="invalid"):
            BindingCodec(**options).open(token, purpose="context-reuse", now=now)
    with pytest.raises(ContextBindingError, match="invalid"):
        codec.open(token, purpose="before_send", now=now)
    with pytest.raises(ContextBindingError, match="expired"):
        codec.open(token, purpose="context-reuse", now=now + timedelta(minutes=1))
    with pytest.raises(ContextBindingError, match="invalid"):
        codec.open(token[:-5] + "AAAAA", purpose="context-reuse", now=now)
    with pytest.raises(ContextBindingError, match="too-large"):
        BindingCodec(max_token_bytes=256).seal(value | {"large": "a" * 256}, purpose="test", expires_at=now)


def test_ephemeral_bindings_cannot_be_reused_after_new_runtime_generation():
    now = datetime(2026, 10, 26, tzinfo=UTC)
    token = BindingCodec().seal({}, purpose="context-reuse", expires_at=now + timedelta(minutes=1))
    with pytest.raises(ContextBindingError, match="invalid"):
        BindingCodec().open(token, purpose="context-reuse", now=now)


def test_scope_reference_change_invalidates_old_binding_and_rejects_new_freezing():
    from powercontext.builtin.scope import ScopeMutation

    async def scenario():
        async with open_builtin_runtime(BuiltinConfig(database=SQLiteConfig())) as runtime:
            scope = await _scope(runtime)
            assert runtime.scopes is not None
            assert runtime.atomic_memory is not None
            other = await runtime.scopes.create(
                ScopeDraft(title="Other", summary="Shared evidence", idempotency_key="other")
            )
            scoped = runtime.context.for_scope(scope.scope_id)
            prepared = await scoped.prepare(_request())
            assert isinstance(prepared, PreparedContextWithReuse)
            with pytest.raises(ContextBindingError, match="identity-mismatch"):
                await runtime.context.for_scope(other.scope_id).validate_reuse(prepared.reuse.binding)
            await runtime.scopes.update(
                scope.scope_id,
                ScopeMutation(
                    expected_version=scope.version,
                    title=scope.title,
                    summary=scope.summary,
                    context_references=(other.scope_id,),
                ),
            )
            with pytest.raises(ContextReuseError, match="cross-scope-unsupported"):
                await scoped.validate_reuse(prepared.reuse.binding)
            with pytest.raises(ContextReuseError, match="cross-scope-unsupported"):
                await scoped.prepare(_request())
            assert isinstance(await scoped.prepare(_request(include_reuse=False)), PreparedContext)

    asyncio.run(scenario())


def test_reuse_rechecks_current_scope_authority_even_when_artifact_grant_remains(tmp_path):
    from powercontext.artifacts.search import ArtifactSearchExecutionContext
    from powercontext.server.authz import AccessDeniedError, PrincipalRef
    from tests.e2e.test_access_control_regressions import AUDIT, _grant, _server
    from tests.e2e.test_access_control_regressions import _scope as http_scope
    from tests.e2e.test_atomic_memory_prepare_lifecycle import _remember, _revoke

    async def scenario():
        async with _server(tmp_path) as (app, client, access):
            scope_id = await http_scope(client)
            record = await _remember(client, scope_id, "Alpha private constraint.")
            grant = await _grant(client, scope_id, "alice", "scope.viewer")
            await _grant(
                client,
                scope_id,
                "alice",
                "artifact.viewer",
                resource={
                    "type": "artifact",
                    "scope_id": scope_id,
                    "identity": {"family": "atomic-memory", "artifact_id": record["artifact"]["artifact_id"]},
                    "selector": None,
                },
            )
            context = ArtifactSearchExecutionContext(
                principal=PrincipalRef(type="user", id="alice"),
                access=access,
                audit=AUDIT,
            )
            scoped = app.state.application.context.for_scope(scope_id)
            prepared = await scoped.prepare(_request(), atomic_context=context, binding_identity="alice")
            assert isinstance(prepared, PreparedContextWithReuse)
            await scoped.validate_reuse(prepared.reuse.binding, atomic_context=context, binding_identity="alice")
            await _revoke(client, grant)
            with pytest.raises(AccessDeniedError):
                await scoped.validate_reuse(prepared.reuse.binding, atomic_context=context, binding_identity="alice")

    asyncio.run(scenario())


@pytest.mark.parametrize("mutation", ["forget", "revoke"])
def test_reuse_revalidation_keeps_authority_and_evidence_in_one_snapshot(tmp_path, monkeypatch, mutation):
    from powercontext.artifacts.search import ArtifactSearchExecutionContext
    from powercontext.builtin.runtime.atomic_memory import AtomicMemoryAccess
    from powercontext.server.authz import AccessDeniedError, PrincipalRef
    from tests.e2e.test_access_control_regressions import AUDIT, _grant, _server
    from tests.e2e.test_access_control_regressions import _scope as http_scope
    from tests.e2e.test_atomic_memory_prepare_lifecycle import _change, _finish_read, _Pause, _remember, _revoke

    async def scenario():
        async with _server(tmp_path) as (app, client, access):
            scope_id = await http_scope(client)
            record = await _remember(client, scope_id, "Alpha retained evidence.")
            grant = await _grant(client, scope_id, "alice", "scope.viewer")
            context = ArtifactSearchExecutionContext(
                principal=PrincipalRef(type="user", id="alice"), access=access, audit=AUDIT
            )
            scoped = app.state.application.context.for_scope(scope_id)
            prepared = await scoped.prepare(_request(), atomic_context=context, binding_identity="alice")
            assert isinstance(prepared, PreparedContextWithReuse)
            pause = _Pause()
            authorize = AtomicMemoryAccess.authorize

            async def paused(self, connection, scope_id, read_context, action, ref=None):
                result = await authorize(self, connection, scope_id, read_context, action, ref)
                if read_context is context and action == "read" and ref is None:
                    await pause.wait()
                return result

            async def mutate():
                if mutation == "forget":
                    await _change(client, scope_id, record, "forget")
                else:
                    await _revoke(client, grant)

            with monkeypatch.context() as patch:
                patch.setattr(AtomicMemoryAccess, "authorize", paused)
                pending = asyncio.create_task(
                    scoped.validate_reuse(prepared.reuse.binding, atomic_context=context, binding_identity="alice")
                )
                checked = await _finish_read(pending, pause, mutate)
            assert checked.content_digest == sha256((prepared.context.content or "").encode("utf-8")).hexdigest()
            error = ContextReuseError if mutation == "forget" else AccessDeniedError
            with pytest.raises(error):
                await scoped.validate_reuse(prepared.reuse.binding, atomic_context=context, binding_identity="alice")

    asyncio.run(scenario())


def test_ancestry_over_the_supported_bound_never_gets_a_partial_binding():
    from powercontext.builtin.runtime.context_reuse import MAX_REUSE_DEPTH

    async def scenario():
        async with open_builtin_runtime(BuiltinConfig(database=SQLiteConfig())) as runtime:
            scope = await _scope(runtime)
            assert runtime.scopes is not None
            assert runtime.atomic_memory is not None
            memory = runtime.atomic_memory.for_scope(scope.scope_id)
            current = (await memory.create((AtomicMemoryContent(kind="fact", text="Alpha root."),))).primary
            for number in range(MAX_REUSE_DEPTH + 1):
                other = (
                    await memory.create((AtomicMemoryContent(kind="fact", text=f"Alpha addition {number}."),))
                ).primary
                current = (
                    await memory.merge(
                        (current.as_read(), other.as_read()),
                        AtomicMemoryContent(kind="fact", text=f"Alpha combined through {number}."),
                    )
                ).primary
            scoped = runtime.context.for_scope(scope.scope_id)
            ordinary = await scoped.prepare(_request(include_reuse=False))
            assert isinstance(ordinary, PreparedContext)
            assert ordinary.status == "ready"
            with pytest.raises(ContextReuseError, match="dependency-limit"):
                await scoped.prepare(_request())

    asyncio.run(scenario())


def test_selected_unsupported_family_is_rejected_without_changing_ordinary_selection():
    async def scenario():
        async with open_builtin_runtime(BuiltinConfig(database=SQLiteConfig())) as runtime:
            scope = await _scope(runtime)
            assert runtime.scopes is not None
            assert runtime.atomic_memory is not None
            await runtime.records.for_scope(scope.scope_id).create_artifact(
                "experience",
                ArtifactWrite(
                    content={
                        "situation": "Alpha client needs repair.",
                        "action": "Alpha repair preserves the contract.",
                        "outcome": "Alpha tests pass.",
                        "lesson": "Alpha interfaces need tests.",
                    }
                ),
            )
            scoped = runtime.context.for_scope(scope.scope_id)
            request = _request(
                assembly=ContextAssembly.model_validate({"sections": [{"family": "experience", "limit": 2}]})
            )
            ordinary = await scoped.prepare(request.model_copy(update={"include_reuse": False}))
            assert isinstance(ordinary, PreparedContext)
            assert ordinary.status == "ready"
            assert ordinary.content is not None
            assert "Alpha interfaces need tests." in ordinary.content
            with pytest.raises(ContextReuseError, match="family-unsupported"):
                await scoped.prepare(request)

    asyncio.run(scenario())


def test_ineligible_persisted_source_cannot_be_authorized_by_an_old_artifact_binding():
    import json

    from sqlalchemy import select, update

    from powercontext.builtin.persistence.tables import SOURCES_TABLE

    async def scenario():
        async with open_builtin_runtime(BuiltinConfig(database=SQLiteConfig())) as runtime:
            scope = await _scope(runtime)
            assert runtime.scopes is not None
            assert runtime.atomic_memory is not None
            created = await runtime.atomic_memory.for_scope(scope.scope_id).create((
                AtomicMemoryContent(kind="fact", text="Alpha evidence must still exist."),
            ))
            scoped = runtime.context.for_scope(scope.scope_id)
            prepared = await scoped.prepare(_request())
            assert isinstance(prepared, PreparedContextWithReuse)
            # Emulate invalid evidence provenance in a restored store. The immutable
            # Artifact still exists, but the Source no longer qualifies for this target.
            source = created.primary.artifact.lineage.sources[0]
            async with runtime.primary_database.transaction() as connection:
                where = (
                    (SOURCES_TABLE.c.scope_id == scope.scope_id)
                    & (SOURCES_TABLE.c.source_type == source.source_type)
                    & (SOURCES_TABLE.c.source_id == source.source_id)
                )
                payload = json.loads(
                    (await connection.execute(select(SOURCES_TABLE.c.payload).where(where))).scalar_one()
                )
                payload["value"]["internal"]["target"]["revision"] = 999
                await connection.execute(
                    update(SOURCES_TABLE).where(where).values(payload=json.dumps(payload).encode("utf-8"))
                )
            with pytest.raises(ContextReuseError, match="evidence-unavailable") as rejected:
                await scoped.validate_reuse(prepared.reuse.binding)
            # Unreadable or ineligible evidence is never explained, so it cannot distinguish missing from denied.
            assert rejected.value.invalidation is None

    asyncio.run(scenario())
