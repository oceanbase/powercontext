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

"""Real SQLite and native SDK HTTP; provider responses and catalog are controlled."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from types import SimpleNamespace

import pytest

from evaluation.performance.input_encoding import __main__ as cli
from evaluation.performance.input_encoding import dataset, runner
from evaluation.performance.input_encoding.identity import KEY_FILENAME, supports_private_identity

pytestmark = pytest.mark.skipif(
    not supports_private_identity(), reason="Live evaluation requires POSIX private-file support"
)


@pytest.fixture
def upstream():
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(request)
            payload = json.dumps({
                "id": "input-resume-completion",
                "object": "chat.completion",
                "created": 0,
                "model": request["model"],
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": '{"proposal":null}'},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14},
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args) -> None:  # noqa: A002
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        yield server, requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def _snapshot(directory):
    return {str(path.relative_to(directory)): path.read_bytes() for path in directory.rglob("*") if path.is_file()}


@pytest.mark.parametrize("offline_prepared", [False, True])
def test_run_rejects_orphaned_results_without_reassigning_usage(
    tmp_path, monkeypatch, capsys, upstream, offline_prepared
):
    server, requests = upstream
    instance = SimpleNamespace(
        instance_id="fixture-case",
        repo="synthetic/repository",
        problem_statement="Reject expired tokens.",
        patch="+validate_expiration(token)\n",
        test_patch="+assert_expired_token_is_rejected()\n",
    )
    catalog = SimpleNamespace(instance_ids=(instance.instance_id,), require=lambda name: instance)
    monkeypatch.setattr(runner, "load_catalog", lambda path: catalog)
    monkeypatch.setattr(dataset, "load_catalog", lambda path: catalog)
    monkeypatch.setattr(dataset, "stability_ids", lambda value: (instance.instance_id,))
    source = tmp_path / "public-fixture.jsonl"
    source.write_text("public fixture\n")
    cases = tmp_path / "cases.json"
    cases.write_text(json.dumps([instance.instance_id]))
    output = tmp_path / "run"
    if offline_prepared:
        dataset.offline(source, output / "offline.json", iterations=1)
        (output / "attempts").mkdir()
        (output / "segments").mkdir()
    offline_before = _snapshot(output)
    environment = tmp_path / "private.env"
    environment.write_text(
        "POWERCONTEXT_SERVER_DATABASE_KIND=sqlite\n"
        "POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL=openai-chat:model-a\n"
        "POWERCONTEXT_SERVER_INFERENCE_GENERATION_STREAMING=disabled\n"
        f"POWERCONTEXT_SERVER_INFERENCE_GENERATION_BASE_URL=http://127.0.0.1:{server.server_port}/v1\n"
        "OPENAI_API_KEY=synthetic-input-resume-key\n"
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "input_encoding",
            "run",
            "--output",
            str(output),
            "--env-file",
            str(environment),
            "--dataset",
            str(source),
            "--case-ids",
            str(cases),
            "--repeats",
            "1",
            "--timeout",
            "30",
        ],
    )
    cli.main()
    assert [request["model"] for request in requests] == ["model-a"] * 3
    records = [json.loads(path.read_text()) for path in (output / "attempts").glob("*.json")]
    assert len(records) == 3
    assert {record["status"] for record in records} == {"no_proposal"}
    assert sum(record["usage"]["total_tokens"] for record in records) == 42
    assert json.loads((output / "manifest.json").read_text())["model"] == "openai-chat:model-a"
    assert (output / "experience.db").is_file()
    assert (output / "report.md").is_file()
    assert all((output / name).read_bytes() == value for name, value in offline_before.items())

    (output / "manifest.json").unlink()
    if offline_prepared:
        # Missing keys must not be regenerated for execution records without identity.
        (output / KEY_FILENAME).unlink()
    environment.write_text(environment.read_text().replace("model-a", "model-b"))
    before = _snapshot(output)
    capsys.readouterr()
    with pytest.raises(SystemExit) as captured:
        cli.main()
    assert captured.value.code == 2
    assert "original manifest" in capsys.readouterr().err
    assert [request["model"] for request in requests] == ["model-a"] * 3
    assert _snapshot(output) == before
    assert not (output / "manifest.json").exists()
