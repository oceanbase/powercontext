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

"""Actual Hermes compression entry: rejected checkpoints preserve, accepted checkpoints permit replacement."""

from __future__ import annotations

import copy
import hashlib
import json
import sys

from tests.integration_conformance.hermes.model_stub import MODEL, MODEL_KEY, SUMMARY_MARKER, summary_model_stub
from tests.integration_conformance.hermes.support import Boundary, create_scope, provider_config, server


def _loopback_only(event, args):
    """Keep model/catalog traffic inside the disposable subprocess's local boundary."""
    allowed = {"127.0.0.1", "::1", "localhost", None}
    if event == "socket.connect":
        address = args[1]
        if isinstance(address, tuple) and address[0] not in allowed:
            raise RuntimeError("Hermes conformance permits only loopback connections")  # noqa: TRY003
    elif event == "socket.getaddrinfo" and args[0] not in allowed:
        raise RuntimeError("Hermes conformance permits only loopback name resolution")  # noqa: TRY003


def exercise_compaction(provider_type, directory):
    # This audit hook applies only to the isolated acceptance subprocess. Neither
    # the host's compression methods nor its MemoryManager methods are replaced.
    sys.addaudithook(_loopback_only)
    from agent.conversation_compression_checkpoint import (  # ty: ignore[unresolved-import]
        CompressionCheckpointUnavailable,
    )
    from agent.memory_manager import MemoryManager  # ty: ignore[unresolved-import]
    from hermes_constants import (  # ty: ignore[unresolved-import]
        reset_hermes_home_override,
        set_hermes_home_override,
    )
    from hermes_state import SessionDB  # ty: ignore[unresolved-import]
    from run_agent import AIAgent  # ty: ignore[unresolved-import]

    directory.mkdir(parents=True, exist_ok=True)
    home = directory / "profile"
    home.mkdir()
    messages = [
        {"role": role, "content": f"Synthetic compaction evidence {index} {role}. " + "cobalt " * 60}
        for index in range(12)
        for role in ("user", "assistant")
    ]
    fixture_hash = hashlib.sha256(json.dumps(messages, sort_keys=True).encode("utf-8")).hexdigest()
    boundary = Boundary()
    token = set_hermes_home_override(str(home))
    try:
        with server(directory / "pc-server", boundary) as client, summary_model_stub() as model_stub:
            (home / "config.yaml").write_text(
                json.dumps(
                    {
                        "model": {"context_length": 131072},
                        "compression": {
                            "enabled": True,
                            "checkpoint_required": True,
                            "in_place": True,
                            "micro_compact": False,
                            "threshold_tokens": 2000,
                            "protect_first_n": 1,
                            "protect_last_n": 3,
                            "tail_mode": "legacy",
                            "abort_on_summary_failure": True,
                        },
                        "memory": {"memory_enabled": False, "user_profile_enabled": False},
                        "auxiliary": {
                            "compression": {
                                "provider": "custom",
                                "model": MODEL,
                                "base_url": model_stub.base_url,
                                "api_key": MODEL_KEY,
                                "api_mode": "chat_completions",
                                "context_length": 131072,
                            }
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            scope = create_scope(client, "Hermes compression checkpoint rejection fixture")
            provider = provider_type(provider_config(client, scope))
            manager = MemoryManager()
            manager.add_provider(provider)
            manager.initialize_all("compaction-fixture", hermes_home=str(home), agent_identity="fixture")
            db = SessionDB(db_path=directory / "host-state.db")
            session_id = "compaction-fixture"
            db.create_session(session_id, source="cli")
            for message in messages:
                db.append_message(session_id, message["role"], message["content"])
            persisted = db.get_messages_as_conversation(session_id)
            # Resume through the host's supported read path so publication receives
            # the durable row identities, as a real resumed conversation does.
            messages = db.get_messages_as_conversation(session_id, include_row_ids=True)
            original = copy.deepcopy(messages)
            try:
                agent = AIAgent(
                    api_key=MODEL_KEY,
                    base_url=model_stub.base_url,
                    provider="custom",
                    api_mode="chat_completions",
                    model=MODEL,
                    quiet_mode=True,
                    enabled_toolsets=[],
                    session_db=db,
                    session_id=session_id,
                    memory_manager=manager,
                    skip_context_files=True,
                    skip_background_review=True,
                    cwd=str(home),
                )
                try:
                    assert agent._memory_manager is manager
                    assert agent.compression_checkpoint_required is True
                    boundary.fail_capture = True
                    try:
                        # The real entry used for manual compression, including its host
                        # facade, checkpoint gate and SessionDB boundary. No direct callback.
                        agent._compress_context(messages, "Synthetic system prompt.", approx_tokens=120_000, force=True)
                    except CompressionCheckpointUnavailable as error:
                        assert type(error.__cause__).__name__ == "PreCompressCheckpointError"
                    else:
                        raise AssertionError("Actual compression did not fail at the required checkpoint")  # noqa: TRY003
                    assert messages == original
                    assert db.get_messages_as_conversation(session_id) == persisted
                    assert agent.session_id == session_id
                    assert any(path == "/v1/sources/content" for _, path, _ in boundary.requests)
                    assert not boundary.accepted
                    assert model_stub.calls == 0
                    unchanged = SessionDB(db_path=directory / "host-state.db")
                    try:
                        assert unchanged.get_messages_as_conversation(session_id) == persisted
                    finally:
                        unchanged.close()

                    # The same real compression entry must make progress once capture
                    # is accepted, through the HTTP model stub and the actual host commit.
                    boundary.fail_capture = False
                    compressed, _ = agent._compress_context(
                        messages, "Synthetic system prompt.", approx_tokens=120_000, force=True
                    )
                    assert model_stub.calls > 0
                    assert len(compressed) < len(original)
                    assert SUMMARY_MARKER in json.dumps(compressed)
                    assert agent.session_id == session_id
                    compacted = db.get_messages_as_conversation(session_id)
                    assert SUMMARY_MARKER in json.dumps(compacted)
                    assert compacted != persisted
                    assert [(m["role"], m["content"]) for m in compacted] == [
                        (m["role"], m["content"]) for m in compressed
                    ]
                    assert boundary.accepted
                finally:
                    agent.close()
            finally:
                manager.shutdown_all()
                db.close()
        reopened = SessionDB(db_path=directory / "host-state.db")
        try:
            assert reopened.get_messages_as_conversation(session_id) == compacted
        finally:
            reopened.close()
    finally:
        reset_hermes_home_override(token)
    return {
        "status": "passed",
        "executed": True,
        "entry": "AIAgent._compress_context",
        "fixture": {"id": "hermes-compaction-checkpoint-control-v1", "sha256": fixture_hash},
        "checkpoint_rejection_blocks_replacement": True,
        "live_transcript_preserved": True,
        "persisted_transcript_preserved_on_rejection": True,
        "summary_model_requests_on_rejection": 0,
        "session_identity_preserved": True,
        "capture_requests": sum(path == "/v1/sources/content" for _, path, _ in boundary.requests),
        "successful_transcript_replacement": "passed",
        "replacement_survives_session_db_reopen": True,
        "summary_model_http_requests": model_stub.calls,
        "failure_category": None,
    }
