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

import io
import json
import sys
import threading
import time
from collections.abc import Generator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

_CONTEXT_BLOCK_BYTES = 20_000
_SCOPE_BODY = json.dumps({"scope_id": "scope-1"}).encode()
_DRIP_INTERVAL_SECONDS = 0.05
_DRIP_WRITES = 60


def _prepared(content: str | None = "prepared context", *, status: str = "ready") -> dict[str, object]:
    return {
        "schema": "powercontext.prepared-context.v1",
        "status": status,
        "content": content,
        "content_bytes": 0 if content is None else len(content.encode("utf-8")),
    }


def _host_message(question: str, *, context_bytes: int = _CONTEXT_BLOCK_BYTES) -> str:
    """Build the shape WorkBuddy submits: an injected context block plus the user query."""

    reminder = "<system-reminder>" + "x" * context_bytes + "</system-reminder>"
    return f"{reminder}\n<user_query>{question}</user_query>"


def _payload(prompt: str) -> dict[str, object]:
    return {
        "hook_event_name": "UserPromptSubmit",
        "cwd": "/workspace/project",
        "prompt": prompt,
    }


def _run_main(
    hook_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    payload: dict[str, object],
) -> tuple[str, str]:
    output = io.StringIO()
    errors = io.StringIO()
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    monkeypatch.setattr(sys, "stdout", output)
    monkeypatch.setattr(sys, "stderr", errors)
    assert hook_module.main() == 0
    return output.getvalue(), errors.getvalue()


def _stub_recall(
    hook_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    queries: list[str],
    *,
    content: str | None = "prepared context",
    captures: list[str] | None = None,
) -> None:
    def prepare(query: str, _scope: str, *, settings: object, deadline: float) -> dict[str, object]:
        queries.append(query)
        return _prepared(content)

    def capture(_payload: object, *, prompt: str, **_kwargs: object) -> dict[str, object]:
        if captures is not None:
            captures.append(prompt)
        return {"position": 1}

    monkeypatch.setattr(hook_module, "_prepare_context", prepare)
    monkeypatch.setattr(
        hook_module,
        "resolve_scope_id",
        lambda _cwd, **_kwargs: "git:github.com/oceanbase/powercontext",
    )
    monkeypatch.setattr(hook_module, "_capture_prompt", capture)


@contextmanager
def _serve(handler: type[BaseHTTPRequestHandler]) -> Generator[str, None, None]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=1)
        server.server_close()


def test_recall_query_prefers_the_submitted_turn_over_the_joined_transcript(
    hook_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WorkBuddy joins every user message, so retrieval must use the turn in hand.

    The joined prompt of a long session repeats the injected context block of every earlier
    message. Retrieving with the whole join asked the server for a query beyond its bound,
    which left the recall empty without reporting anything.
    """

    transcript = "\n".join(_host_message(f"earlier turn {index}") for index in range(4))
    transcript += "\n" + _host_message("Which service chain should WorkBuddy use?")
    assert len(transcript) > 8192

    queries: list[str] = []
    _stub_recall(hook_module, monkeypatch, queries, content="recalled context")

    output, _ = _run_main(hook_module, monkeypatch, _payload(transcript))

    assert queries == ["Which service chain should WorkBuddy use?"]
    assert json.loads(output)["hookSpecificOutput"]["additionalContext"] == "recalled context"


def test_recall_query_falls_back_to_the_joined_prompt_within_the_request_bound(
    hook_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A host that stops marking the submitted turn still gets a bounded query."""

    joined = "earlier turn " * 3_000
    queries: list[str] = []
    _stub_recall(hook_module, monkeypatch, queries)

    _, errors = _run_main(hook_module, monkeypatch, _payload(joined))

    (query,) = queries
    assert len(query) <= 8192
    assert len(query.encode("utf-8")) <= 8192
    assert joined.strip().endswith(query)
    events = [json.loads(line) for line in errors.splitlines()]
    reduction = [event for event in events if event["event"] == "query_reduction"]
    assert len(reduction) == 1
    assert reduction[0]["source"] == "joined_prompt"
    assert reduction[0]["truncated"] is True


def test_recall_query_keeps_a_multibyte_prompt_within_the_byte_bound(
    hook_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-ASCII text costs several bytes per character, so both bounds have to hold."""

    joined = "记忆 " * 4_000
    queries: list[str] = []
    _stub_recall(hook_module, monkeypatch, queries)

    _run_main(hook_module, monkeypatch, _payload(joined))

    (query,) = queries
    assert len(query) < len(joined)
    assert len(query.encode("utf-8")) <= 8192
    assert joined.strip().endswith(query)


def test_recall_query_passes_a_plain_prompt_through_unchanged(
    hook_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queries: list[str] = []
    _stub_recall(hook_module, monkeypatch, queries)

    _, errors = _run_main(hook_module, monkeypatch, _payload("Which decisions apply?"))

    assert queries == ["Which decisions apply?"]
    assert errors == ""


def test_a_reduced_query_is_reported_on_stderr(
    hook_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The recall stays silent on failure, so a reduction is the diagnostic available."""

    transcript = "\n".join(_host_message(f"earlier turn {index}") for index in range(2))
    queries: list[str] = []
    _stub_recall(hook_module, monkeypatch, queries)

    _, errors = _run_main(hook_module, monkeypatch, _payload(transcript))

    events = [json.loads(line) for line in errors.splitlines()]
    reduction = [event for event in events if event["event"] == "query_reduction"]
    assert len(reduction) == 1
    assert reduction[0]["component"] == "powercontext.workbuddy.recall"
    assert reduction[0]["source"] == "user_query"
    assert reduction[0]["truncated"] is False


def test_recall_query_ignores_a_user_query_pair_quoted_in_prose(
    hook_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A prompt that mentions the tags is prose, not a host wrapper.

    Users and host-written summaries reach the prompt quoting this markup verbatim. Reading
    such a pair sent a fragment of the quoting sentence as the query, while the question the
    prompt actually asks went unretrieved.
    """

    prompt = "The release note says <user_query>example</user_query> verbatim, keep it."
    queries: list[str] = []
    _stub_recall(hook_module, monkeypatch, queries)

    _, errors = _run_main(hook_module, monkeypatch, _payload(prompt))

    assert queries == [prompt]
    assert errors == ""


def test_recall_query_keeps_the_current_question_when_a_turn_precedes_it(
    hook_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unmarked turn followed by the current question must not read as the wrapped turn.

    The pair closes before the question begins, so the question sits outside the wrapper and
    the element is not the one holding the submitted turn.
    """

    prompt = "<user_query>Explain SQLite backups</user_query>\nHow do I configure OceanBase?"
    queries: list[str] = []
    _stub_recall(hook_module, monkeypatch, queries)

    _run_main(hook_module, monkeypatch, _payload(prompt))

    (query,) = queries
    assert query.endswith("How do I configure OceanBase?")


def test_recall_query_falls_back_to_the_last_verified_turn_when_a_later_block_quotes_the_tags(
    hook_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The host appends blocks that carry no turn of their own, and they can quote the tags.

    Task notifications and summaries arrive in the join after the submitted turn. A pair
    quoted there is not the turn, so the turn before it is the one to retrieve against.
    """

    transcript = _host_message("缺陷 1 的 hook 侧防御也顺手做")
    summary = "<conversation_history_summary>\nThe hook reads `<user_query>` and `</user_query>`.\n"
    summary += "</conversation_history_summary>"
    queries: list[str] = []
    _stub_recall(hook_module, monkeypatch, queries)

    _run_main(hook_module, monkeypatch, _payload(f"{transcript}\n{summary}"))

    assert queries == ["缺陷 1 的 hook 侧防御也顺手做"]


def test_recall_query_keeps_the_current_turn_when_cb_summary_follows_it(
    hook_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WorkBuddy can append compacted summaries after the submitted turn."""

    current_turn = "current OceanBase question"
    summary = "<cb_summary>" + ("old summary " * 1_000) + "</cb_summary>"
    queries: list[str] = []
    _stub_recall(hook_module, monkeypatch, queries)

    _run_main(hook_module, monkeypatch, _payload(f"{_host_message(current_turn)}\n{summary}"))

    assert queries == [current_turn]


def test_recall_query_keeps_a_wrapped_turn_that_quotes_the_tags(
    hook_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A literal pair inside the wrapped turn is the turn's own text, not its boundary.

    Reading the inner pair would drop everything the user wrote before it.
    """

    turn = "Rewrite this line: <user_query>example</user_query> stays as is."
    queries: list[str] = []
    _stub_recall(hook_module, monkeypatch, queries)

    _run_main(hook_module, monkeypatch, _payload(_host_message(turn)))

    (query,) = queries
    assert turn in query


def test_recall_query_keeps_the_latest_wrapped_turn_when_it_quotes_the_tags_after_history(
    hook_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A literal pair inside the latest turn must not send recall back into history."""

    current_turn = "Which service chain should WorkBuddy use? Keep <user_query>example</user_query> verbatim."
    prompt = "\n".join([
        _host_message("Explain SQLite backups"),
        _host_message(current_turn),
    ])
    queries: list[str] = []
    _stub_recall(hook_module, monkeypatch, queries)

    _run_main(hook_module, monkeypatch, _payload(prompt))

    assert queries == [current_turn]


def test_recall_query_keeps_the_latest_wrapped_turn_when_a_fenced_example_quotes_the_tags(
    hook_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fenced example puts a literal pair at a line start inside the latest turn.

    The fence hides the pair from a sentence, so its opening tag looks like a wrapper opener
    while its closing tag is not at a host boundary. Pairing that opener with the host's own
    closing tag would send a fragment of the turn as the query.
    """

    current_turn = (
        "Which service chain should WorkBuddy use? Keep this example:\n```xml\n<user_query>example</user_query>\n```"
    )
    prompt = "\n".join([
        _host_message("Explain SQLite backups"),
        _host_message(current_turn),
    ])
    queries: list[str] = []
    _stub_recall(hook_module, monkeypatch, queries)

    _run_main(hook_module, monkeypatch, _payload(prompt))

    assert queries == [current_turn]


def test_recall_query_keeps_the_turn_when_a_literal_opener_inside_it_is_never_closed(
    hook_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unclosed literal opener leaves the turn's own element unbalanced.

    The turn is still the wrapper the host closed at the message boundary, so the text the user
    wrote survives instead of the reduction giving the turn up to the bounded fallback.
    """

    turn = "Explain how <user_query> is closed"
    queries: list[str] = []
    _stub_recall(hook_module, monkeypatch, queries)

    _run_main(hook_module, monkeypatch, _payload(_host_message(turn)))

    assert queries == [turn]


def test_recall_query_keeps_the_turn_when_a_fenced_example_leaves_its_pair_open(
    hook_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fenced example with no closing tag leaves the example's opener unbalanced too.

    The example is still inside the turn, so the turn is the element to read rather than the
    fragment between the example's opener and the host's closing tag.
    """

    current_turn = "Q? Keep this example:\n```xml\n<user_query>example\n```"
    prompt = "\n".join([
        _host_message("Explain SQLite backups"),
        _host_message(current_turn),
    ])
    queries: list[str] = []
    _stub_recall(hook_module, monkeypatch, queries)

    _run_main(hook_module, monkeypatch, _payload(prompt))

    assert queries == [current_turn]


def test_recall_query_keeps_the_turn_when_a_literal_pair_is_followed_by_a_host_block(
    hook_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pair the turn quotes can end where a host block opens, which no boundary check separates.

    Only the enclosing element tells the quoted pair apart from the wrapper, so the turn is read
    whole rather than from the quoted pair onwards.
    """

    current_turn = "Which service chain?\n<user_query>x</user_query>\n<system-reminder>block</system-reminder>"
    queries: list[str] = []
    _stub_recall(hook_module, monkeypatch, queries)

    _run_main(hook_module, monkeypatch, _payload(_host_message(current_turn)))

    assert queries == [current_turn]


def test_recall_query_ignores_a_line_isolated_pair_inside_a_host_summary(
    hook_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A summary quoting an earlier turn keeps that pair inside the element that quotes it.

    Such a pair takes a line of its own, so only the enclosing closing tag tells it apart from
    the host's wrapper. Reading it retrieves for the turn the summary quotes, which is exactly
    the turn the prompt is no longer asking about.
    """

    transcript = _host_message("你现在用哪个后端存储")
    summary = (
        "<conversation_history_summary>\n"
        "<previous_user_message>\n"
        "<user_query>\n"
        "旧问题 SQLite 怎么备份\n"
        "</user_query>\n"
        "</previous_user_message>\n"
        "</conversation_history_summary>"
    )
    queries: list[str] = []
    _stub_recall(hook_module, monkeypatch, queries)

    _run_main(hook_module, monkeypatch, _payload(f"{transcript}\n{summary}"))

    assert queries == ["你现在用哪个后端存储"]


def test_recall_query_rejects_a_summary_pair_followed_by_an_inner_sibling_element(
    hook_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A sibling tag inside a summary is not the next host message boundary."""

    transcript = _host_message("现在 OceanBase 怎么配置")
    summary = (
        "<conversation_history_summary>\n"
        "<previous_user_message>\n"
        "<user_query>\n"
        "旧问题 SQLite 怎么备份\n"
        "</user_query>\n"
        "<details>quoted metadata</details>\n"
        "</previous_user_message>\n"
        "</conversation_history_summary>"
    )
    queries: list[str] = []
    _stub_recall(hook_module, monkeypatch, queries)

    _run_main(hook_module, monkeypatch, _payload(f"{transcript}\n{summary}"))

    assert queries == ["现在 OceanBase 怎么配置"]


def test_capture_still_records_the_prompt_when_the_turn_reduces_to_nothing(
    hook_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Source records what the host submitted, independent of the recall a turn can ask.

    A turn that reduces to nothing has nothing to retrieve, and gating the capture on the
    query would silently drop the Source write for it.
    """

    prompt = "earlier context\n<user_query> </user_query>"
    queries: list[str] = []
    captures: list[str] = []
    _stub_recall(hook_module, monkeypatch, queries, captures=captures)

    _run_main(hook_module, monkeypatch, _payload(prompt))

    assert queries == []
    assert captures == [prompt]


def test_prompt_hook_fails_open_within_the_http_budget_when_recall_reads_drip(
    hook_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The recall/capture reads must stay inside the hook's own HTTP budget, not the host hook timeout."""

    paths: list[str] = []

    class DripRecallHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            paths.append(self.path)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            if self.path == "/v1/scope-bindings/resolve":
                self.send_header("Content-Length", str(len(_SCOPE_BODY)))
                self.end_headers()
                self.wfile.write(_SCOPE_BODY)
                self.wfile.flush()
                return
            self.end_headers()
            for _ in range(_DRIP_WRITES):
                try:
                    self.wfile.write(b" ")
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    return
                time.sleep(_DRIP_INTERVAL_SECONDS)

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            pass

    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(
            json.dumps({
                "hook_event_name": "UserPromptSubmit",
                "prompt": "what did we decide about the recall budget?",
                "cwd": str(tmp_path),
                "session_id": "session-1",
            })
        ),
    )
    output = io.StringIO()
    monkeypatch.setattr(sys, "stdout", output)

    with _serve(DripRecallHandler) as server_url:
        settings = hook_module.WorkBuddyPluginSettings(
            server_url=server_url,
            request_timeout_seconds=0.3,
            http_budget_seconds=1.0,
        )
        started = time.monotonic()
        assert hook_module.main(settings) == 0
        elapsed = time.monotonic() - started

    assert elapsed < 1.5
    assert "/v1/scope-bindings/resolve" in paths
    assert "/v1/context/prepare" in paths
    assert json.loads(output.getvalue())["hookSpecificOutput"]["additionalContext"] == ""


def test_prompt_hook_refuses_redirects(
    hook_module: ModuleType,
) -> None:
    """Sharing the bounded opener must not let a redirect carry the hook's credentials elsewhere."""

    target_headers: list[dict[str, str]] = []

    class TargetHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            target_headers.append(dict(self.headers))
            self.send_response(200)
            self.end_headers()

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            pass

    with _serve(TargetHandler) as target_url:

        class RedirectHandler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                self.rfile.read(int(self.headers.get("Content-Length", "0")))
                self.send_response(302)
                self.send_header("Location", f"{target_url}/stolen")
                self.end_headers()

            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
                pass

        with _serve(RedirectHandler) as source_url:
            settings = hook_module.WorkBuddyPluginSettings(
                server_url=source_url,
                authorization="Bearer secret-token",
            )
            with pytest.raises(RuntimeError):
                hook_module._post_json(
                    "/v1/context/prepare",
                    {"scope_id": "scope-1", "query": "what did we decide?"},
                    settings=settings,
                    deadline=time.monotonic() + 1.0,
                )

    assert target_headers == []
