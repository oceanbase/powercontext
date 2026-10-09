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


from typer.testing import CliRunner

from powercontext_eval_swebench_pro.cli import app


def test_codex_contract_smoke_is_an_executable_injectable_cli(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    def fake_contract_smoke(**kwargs: object) -> dict[str, object]:
        calls.append(kwargs)
        return {
            "off_prompt_sources": 0,
            "on_prompt_sources": 1,
            "status": "passed",
            "tokensflow": {
                "off": {"identity_match": True, "queue_caught_up": True},
                "on": {"identity_match": True, "queue_caught_up": True},
            },
        }

    monkeypatch.setattr("powercontext_eval_swebench_pro.service_cli.run_codex_contract_smoke", fake_contract_smoke)
    result = CliRunner().invoke(
        app,
        [
            "codex-contract-smoke",
            "--run-root",
            "/tmp/contract",
            "--task-image",
            "fixture:image",
            "--codex-bin",
            "/tools/codex",
            "--tokensflow-bin",
            "/tools/tokensflow",
            "--tokensflow-user-home",
            "/tokensflow-home",
            "--tokensflow-egress-network",
            "bridge",
            "--uv-bin",
            "/tools/uv",
            "--powercontext-source",
            "/source",
            "--powercontext-sha",
            "a" * 40,
            "--auth-json",
            "/auth.json",
            "--proxy-url",
            "http://127.0.0.1:18080",
        ],
    )

    assert result.exit_code == 0, result.output
    assert '"status": "passed"' in result.output
    assert '"queue_caught_up": true' in result.output
    assert "/tokensflow-home" not in result.output
    assert calls == [
        {
            "run_root": "/tmp/contract",
            "task_image": "fixture:image",
            "codex_bin": "/tools/codex",
            "tokensflow_bin": "/tools/tokensflow",
            "tokensflow_user_home": "/tokensflow-home",
            "tokensflow_egress_network": "bridge",
            "uv_bin": "/tools/uv",
            "powercontext_source": "/source",
            "powercontext_sha": "a" * 40,
            "auth_json": "/auth.json",
            "proxy_url": "http://127.0.0.1:18080",
            "prompt": "Reply with exactly OK.",
        }
    ]
