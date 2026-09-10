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

import pytest

from powercontext.cli.inference_notice import write_inference_capability_notice


@pytest.mark.parametrize("generation_model,embedding_model", [(None, None), ("test", None), (None, "test")])
def test_notice_links_to_configuration_when_any_model_is_missing(
    capsys: pytest.CaptureFixture[str],
    generation_model: str | None,
    embedding_model: str | None,
) -> None:
    write_inference_capability_notice(
        generation_model=generation_model,
        embedding_model=embedding_model,
    )

    output = capsys.readouterr().out
    assert "Inference capability notice" in output
    assert (
        "未配置或未完整配置 PowerContext Server 推理模型（generation model 和 embedding model），\n"  # noqa: RUF001
        "可能影响部分制品功能。具体影响范围及配置方式请参考 PowerContext 官网配置说明：\n"  # noqa: RUF001
        "https://powercontext.oceanbase.io/en/docs/reference/configuration/"
    ) in output


def test_notice_is_silent_when_all_models_are_configured(capsys: pytest.CaptureFixture[str]) -> None:
    write_inference_capability_notice(
        generation_model="openai-chat:test-generation",
        embedding_model="openai:test-embedding",
    )

    assert capsys.readouterr().out == ""
