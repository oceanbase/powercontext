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

from pathlib import Path

import pytest

from powercontext.cli.inference_notice import write_inference_capability_notice


def test_notice_reports_only_the_missing_embedding_capability(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    environment = tmp_path / "powercontext.env"

    write_inference_capability_notice(
        generation_model="openai-chat:test-generation",
        embedding_model=None,
        env_file=environment,
    )

    output = capsys.readouterr().out
    assert "Inference capability notice" in output
    assert "未配置 embedding model" in output
    assert "未配置 generation model:" not in output
    assert str(environment.resolve()) in output


def test_notice_is_silent_when_all_models_are_configured(capsys: pytest.CaptureFixture[str]) -> None:
    write_inference_capability_notice(
        generation_model="openai-chat:test-generation",
        embedding_model="openai:test-embedding",
    )

    assert capsys.readouterr().out == ""
