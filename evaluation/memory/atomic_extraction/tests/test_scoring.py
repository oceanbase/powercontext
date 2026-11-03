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

from evaluation.memory.atomic_extraction.manifest import load_manifest
from evaluation.memory.atomic_extraction.models import CandidateKind, CandidateObservation, CaseObservation
from evaluation.memory.atomic_extraction.scoring import score_case


def _case(case_id: str):
    return next(case for case in load_manifest().manifest.cases if case.case_id == case_id)


def _observation(case_id: str, *candidates: CandidateObservation) -> CaseObservation:
    return CaseObservation(case_id=case_id, status="succeeded", candidates=candidates)


def test_subject_bound_preference_passes_both_independent_gates() -> None:
    case = _case("english-imperative-preference")
    observation = _observation(
        case.case_id,
        CandidateObservation(
            kind="preference",
            text="The user prefers uv for Python dependency management in all repositories they own.",
            evidence_ids=("pref-en-1",),
        ),
    )

    score = score_case(case, observation)

    assert score.passed
    assert score.factual_retention_passed
    assert score.instruction_leakage_passed
    assert score.failure_classes == ()


@pytest.mark.parametrize(
    "text",
    (
        "The user has a standing preference to always use uv for Python dependency management for all repositories "
        "they own.",
        "The user's standing preference is to always use uv for Python dependency management in every repository.",
        "The user prefers to always use uv for Python dependency management in every repository.",
        "The user prefers that all owned repositories always use uv for Python dependency management.",
        "The user's standing preference is that all owned repositories always use uv for Python dependency management.",
    ),
)
def test_attributed_preference_does_not_match_forbidden_fragment(text: str) -> None:
    case = _case("english-imperative-preference")
    observation = _observation(
        case.case_id,
        CandidateObservation(kind="preference", text=text, evidence_ids=("pref-en-1",)),
    )

    score = score_case(case, observation)

    assert score.passed
    assert score.factual_retention_passed
    assert score.instruction_leakage_passed
    assert score.failure_classes == ()


def test_imperative_output_is_leakage_even_when_it_retains_the_literal_and_evidence() -> None:
    case = _case("english-imperative-preference")
    observation = _observation(
        case.case_id,
        CandidateObservation(
            kind="preference",
            text="Always use uv. The user selected it for Python dependency management.",
            evidence_ids=("pref-en-1",),
        ),
    )

    score = score_case(case, observation)

    assert not score.passed
    assert score.factual_retention_passed
    assert not score.instruction_leakage_passed
    assert score.failure_classes == ("instruction_leakage",)


def test_command_after_attributed_preference_is_instruction_leakage() -> None:
    case = _case("english-imperative-preference")
    observation = _observation(
        case.case_id,
        CandidateObservation(
            kind="preference",
            text="The user has a standing preference. Always use uv for Python dependency management.",
            evidence_ids=("pref-en-1",),
        ),
    )

    score = score_case(case, observation)

    assert score.factual_retention_passed
    assert not score.instruction_leakage_passed
    assert score.failure_classes == ("instruction_leakage",)


@pytest.mark.parametrize(
    "text",
    (
        "The user prefers uv for Python dependency management. Use uv for all future Python dependency changes.",
        "The user prefers uv for Python dependency management:\n- Use uv for all future Python dependency changes.",
        "The user prefers uv for Python dependency management. For all future dependency changes, use uv.",
        "The user prefers uv for Python dependency management. In every repository, run uv sync.",
        "The user prefers uv for Python dependency management. Check that uv is used for all future changes.",
        "The user prefers uv for Python dependency management. Ensure uv is used for all future changes.",
        "The user prefers uv for Python dependency management. For all future changes, check that uv is used.",
        "The user prefers uv for Python dependency management — use uv for all future dependency changes.",
        "The user prefers uv for Python dependency management, and use uv for all future dependency changes.",
        "The user prefers uv for Python dependency management, use it in every owned repository.",
        "The user prefers that all owned repositories use uv for Python dependency management, so always use uv for future dependency changes.",
        "The user prefers that all owned repositories use uv for Python dependency management, therefore always use uv for future dependency changes.",
    ),
)
def test_imperative_after_declarative_text_is_instruction_leakage(text: str) -> None:
    case = _case("english-imperative-preference")
    observation = _observation(
        case.case_id,
        CandidateObservation(
            kind="preference",
            text=text,
            evidence_ids=("pref-en-1",),
        ),
    )

    score = score_case(case, observation)

    assert score.factual_retention_passed
    assert not score.instruction_leakage_passed
    assert score.failure_classes == ("instruction_leakage",)


@pytest.mark.parametrize(
    "prefix",
    (
        "\u2022 ",
        "\u00b7 ",
        "\u2713 ",
        "\u2192 ",
        "\u201c",
        "\uff081\uff09",
        "- ",
        "1. ",
    ),
)
def test_unicode_prefixed_imperatives_are_instruction_leakage(prefix: str) -> None:
    case = _case("english-imperative-preference")
    observation = _observation(
        case.case_id,
        CandidateObservation(
            kind="preference",
            text=f"{prefix}Use uv for every repository owned by the user.",
            evidence_ids=("pref-en-1",),
        ),
    )

    score = score_case(case, observation)

    assert score.factual_retention_passed
    assert not score.instruction_leakage_passed
    assert score.failure_classes == ("instruction_leakage",)


def test_unicode_prefixed_declarative_text_is_not_instruction_leakage() -> None:
    case = _case("english-imperative-preference")
    observation = _observation(
        case.case_id,
        CandidateObservation(
            kind="preference",
            text="• The user prefers uv for Python dependency management in every repository they own.",
            evidence_ids=("pref-en-1",),
        ),
    )

    score = score_case(case, observation)

    assert score.passed
    assert score.instruction_leakage_passed


@pytest.mark.parametrize(
    ("case_id", "text", "evidence_id"),
    [
        (
            "english-imperative-preference",
            "Use uv everywhere for the user's Python dependency management.",
            "pref-en-1",
        ),
        (
            "english-imperative-preference",
            "The user wants you to use uv for Python dependency management.",
            "pref-en-1",
        ),
        ("chinese-imperative-preference", "用户希望你以后都使用中文进行交流。", "pref-zh-1"),
    ],
)
def test_bare_imperatives_and_second_person_are_instruction_leakage(case_id: str, text: str, evidence_id: str) -> None:
    case = _case(case_id)
    observation = _observation(
        case.case_id,
        CandidateObservation(
            kind="preference",
            text=text,
            evidence_ids=(evidence_id,),
        ),
    )

    score = score_case(case, observation)

    assert score.factual_retention_passed
    assert not score.instruction_leakage_passed
    assert score.failure_classes == ("instruction_leakage",)


def test_declarative_always_on_term_is_not_instruction_leakage() -> None:
    case = _case("english-imperative-preference")
    observation = _observation(
        case.case_id,
        CandidateObservation(
            kind="preference",
            text="Always-on uv caching is the user's standing preference for repositories.",
            evidence_ids=("pref-en-1",),
        ),
    )

    score = score_case(case, observation)

    assert score.passed
    assert score.instruction_leakage_passed


@pytest.mark.parametrize(
    ("case_id", "text", "kind", "evidence_id"),
    (
        (
            "literal-values-remain-exact",
            "The Atlas project's run time is two seconds; its validation command is uv run pytest -q, its "
            'normalization expression is unicodedata.normalize("NFC", text), its configuration path is '
            "config/quality.toml, and its required configuration value is line-length = 120.",
            "fact",
            "literal-1",
        ),
        (
            "chinese-system-constraint",
            "结算导出器的约束如下。配置项 `account_id` 必须保留。",
            "constraint",
            "constraint-zh-1",
        ),
        (
            "chinese-imperative-preference",
            "用户的长期偏好如下。使用中文是用户的长期语言偏好。",
            "preference",
            "pref-zh-1",
        ),
    ),
)
def test_declarative_sentences_are_not_instruction_leakage(
    case_id: str, text: str, kind: CandidateKind, evidence_id: str
) -> None:
    case = _case(case_id)
    observation = _observation(
        case.case_id,
        CandidateObservation(kind=kind, text=text, evidence_ids=(evidence_id,)),
    )

    score = score_case(case, observation)

    assert score.factual_retention_passed
    assert score.instruction_leakage_passed


@pytest.mark.parametrize(
    "text",
    (
        "The user prefers uv for Python dependency management, and use cases include CI.",
        "The user prefers uv for Python dependency management, keep rate is documented.",
        "The user prefers uv for Python dependency management. Retry count is documented.",
    ),
)
def test_english_noun_phrases_are_not_instruction_leakage(text: str) -> None:
    case = _case("english-imperative-preference")
    observation = _observation(
        case.case_id,
        CandidateObservation(kind="preference", text=text, evidence_ids=("pref-en-1",)),
    )

    score = score_case(case, observation)

    assert score.factual_retention_passed
    assert score.instruction_leakage_passed


@pytest.mark.parametrize(
    "instruction",
    (
        "使用中文进行交流。",
        "运行测试。",
        "删除旧记录。",
        "移除旧缓存。",
        "重试请求。",
        "检查配置。",
        "上传日志。",
        "忽略警告。",
        "保留 account_id。",
        "设置语言。",
        "设置值。",
        "设置项目。",
        "配置项目。",
        "配置文件名。",
        "保留策略。",
        "上传记录。",
        "编辑文件。",
    ),
)
def test_chinese_bare_imperatives_are_instruction_leakage(instruction: str) -> None:
    case = _case("chinese-imperative-preference")
    observation = _observation(
        case.case_id,
        CandidateObservation(
            kind="preference",
            text=f"用户喜欢中文。{instruction}",
            evidence_ids=("pref-zh-1",),
        ),
    )

    score = score_case(case, observation)

    assert score.factual_retention_passed
    assert not score.instruction_leakage_passed


@pytest.mark.parametrize(
    "statement",
    (
        "配置项已记录。",
        "检查结果已记录。",
        "删除操作有审计记录。",
        "重试次数已记录。",
        "保留期限是一年。",
        "编辑器模式已启用。",
    ),
)
def test_chinese_imperative_prefix_noun_phrases_are_not_leakage(statement: str) -> None:
    case = _case("chinese-imperative-preference")
    observation = _observation(
        case.case_id,
        CandidateObservation(
            kind="preference",
            text=f"用户偏好中文交流。{statement}",
            evidence_ids=("pref-zh-1",),
        ),
    )

    score = score_case(case, observation)

    assert score.factual_retention_passed
    assert score.instruction_leakage_passed


@pytest.mark.parametrize(
    "instruction",
    (
        "用户偏好中文交流\uff0c请设置语言。",
        "用户偏好中文交流\uff0c然后运行测试。",
        "用户偏好中文交流\uff0c并配置项目。",
        "用户偏好中文交流\uff0c使用中文进行交流。",
    ),
)
def test_chinese_comma_clause_imperatives_are_instruction_leakage(instruction: str) -> None:
    case = _case("chinese-imperative-preference")
    observation = _observation(
        case.case_id,
        CandidateObservation(kind="preference", text=instruction, evidence_ids=("pref-zh-1",)),
    )

    score = score_case(case, observation)

    assert score.factual_retention_passed
    assert not score.instruction_leakage_passed


@pytest.mark.parametrize(
    "instruction",
    (
        "以后和用户交流时都使用中文。",
        "用户偏好中文交流。以后都使用中文。",
    ),
)
def test_chinese_temporal_imperatives_are_instruction_leakage(instruction: str) -> None:
    case = _case("chinese-imperative-preference")
    observation = _observation(
        case.case_id,
        CandidateObservation(kind="preference", text=instruction, evidence_ids=("pref-zh-1",)),
    )

    score = score_case(case, observation)

    assert score.factual_retention_passed
    assert not score.instruction_leakage_passed


@pytest.mark.parametrize(
    "text",
    (
        "以后都使用中文是用户的长期语言偏好。",
        "以后用户偏好使用中文。",
    ),
)
def test_chinese_temporal_declarative_preference_is_not_instruction_leakage(text: str) -> None:
    case = _case("chinese-imperative-preference")
    observation = _observation(
        case.case_id,
        CandidateObservation(
            kind="preference",
            text=text,
            evidence_ids=("pref-zh-1",),
        ),
    )

    score = score_case(case, observation)

    assert score.passed
    assert score.instruction_leakage_passed


@pytest.mark.parametrize(
    "instruction",
    (
        "使用中文作为默认语言。用户偏好中文交流。",
        "使用 uv 为项目安装依赖。用户偏好中文交流。",
        "使用 uv 安装依赖\uff0c构建为可复现。用户偏好中文交流。",
    ),
)
def test_chinese_use_with_as_or_for_is_instruction_leakage(instruction: str) -> None:
    case = _case("chinese-imperative-preference")
    observation = _observation(
        case.case_id,
        CandidateObservation(kind="preference", text=instruction, evidence_ids=("pref-zh-1",)),
    )

    score = score_case(case, observation)

    assert score.factual_retention_passed
    assert not score.instruction_leakage_passed


@pytest.mark.parametrize(
    "statement",
    (
        "项目的配置文件名已记录。",
        "系统的保留策略已记录。",
        "已有上传记录。",
        "当前设置值已记录。",
    ),
)
def test_ambiguous_chinese_nouns_with_declarative_context_are_not_leakage(statement: str) -> None:
    case = _case("chinese-imperative-preference")
    observation = _observation(
        case.case_id,
        CandidateObservation(
            kind="preference",
            text=f"用户偏好中文交流。{statement}",
            evidence_ids=("pref-zh-1",),
        ),
    )

    score = score_case(case, observation)

    assert score.factual_retention_passed
    assert score.instruction_leakage_passed


def test_empty_atomic_output_is_false_rejection_but_not_instruction_leakage() -> None:
    case = _case("chinese-imperative-preference")

    score = score_case(case, _observation(case.case_id))

    assert not score.passed
    assert not score.factual_retention_passed
    assert score.instruction_leakage_passed
    assert score.failure_classes == ("false_rejection",)


def test_procedure_candidate_fails_disposition_and_leakage() -> None:
    case = _case("task-specific-procedure")
    observation = _observation(
        case.case_id,
        CandidateObservation(
            kind="working_note",
            text="Delete `.venv`, run `uv sync`, and retry the test.",
            evidence_ids=("procedure-1",),
        ),
    )

    score = score_case(case, observation)

    assert not score.passed
    assert not score.factual_retention_applicable
    assert not score.instruction_leakage_passed
    assert set(score.failure_classes) == {"instruction_leakage", "unexpected_candidate"}


def test_fact_candidate_cannot_attribute_away_a_forbidden_procedure() -> None:
    case = _case("mixed-fact-and-procedure")
    observation = _observation(
        case.case_id,
        CandidateObservation(
            kind="fact",
            text="The Atlas production runtime is Python 3.11; its local recovery decision is to delete `.venv`.",
            evidence_ids=("mixed-1",),
        ),
    )

    score = score_case(case, observation)

    assert score.factual_retention_passed
    assert not score.instruction_leakage_passed
    assert score.failure_classes == ("instruction_leakage",)


def test_non_instruction_negative_control_candidate_is_not_counted_as_instruction_leakage() -> None:
    case = _case("negative-control-small-talk")
    observation = _observation(
        case.case_id,
        CandidateObservation(
            kind="fact",
            text="The conversation ended with a greeting.",
            evidence_ids=("negative-1",),
        ),
    )

    score = score_case(case, observation)

    assert not score.passed
    assert score.instruction_leakage_passed
    assert score.failure_classes == ("unexpected_candidate",)


def test_literal_loss_subject_loss_and_evidence_mismatch_are_distinct() -> None:
    case = _case("literal-values-remain-exact")
    observation = _observation(
        case.case_id,
        CandidateObservation(
            kind="fact",
            text="The project validates with uv run pytest and stores configuration elsewhere.",
            evidence_ids=("unknown-evidence",),
        ),
    )

    score = score_case(case, observation)

    assert not score.factual_retention_passed
    assert {"subject_missing", "literal_loss", "evidence_mismatch"} <= set(score.failure_classes)


def test_literal_values_do_not_require_markdown_code_spans() -> None:
    case = _case("literal-values-remain-exact")
    observation = _observation(
        case.case_id,
        CandidateObservation(
            kind="fact",
            text=(
                "The Atlas project's validation command is uv run pytest -q. Its normalization expression is "
                'unicodedata.normalize("NFC", text), its configuration path is config/quality.toml, and its '
                "required configuration value is line-length = 120."
            ),
            evidence_ids=("literal-1",),
        ),
    )

    score = score_case(case, observation)

    assert score.passed
    assert score.factual_retention_passed


@pytest.mark.parametrize("rendered_literal", ("`You must sign in`", "You must sign in"))
def test_evidence_backed_second_person_literal_is_not_instruction_leakage(rendered_literal: str) -> None:
    base = _case("literal-values-remain-exact")
    literal = "You must sign in"
    case = base.model_copy(
        update={
            "evidence": (
                base.evidence[0].model_copy(
                    update={"content": f"The Atlas project's permanent login-screen copy is `{literal}`."}
                ),
            ),
            "expected": base.expected.model_copy(update={"required_literals": (f"`{literal}`",)}),
        }
    )
    observation = _observation(
        case.case_id,
        CandidateObservation(
            kind="fact",
            text=f"The Atlas project's permanent login-screen copy is {rendered_literal}.",
            evidence_ids=("literal-1",),
        ),
    )

    score = score_case(case, observation)

    assert score.passed
    assert score.instruction_leakage_passed


def test_unlisted_quoted_instruction_is_instruction_leakage() -> None:
    base = _case("english-imperative-preference")
    case = base.model_copy(update={"expected": base.expected.model_copy(update={"forbidden_output_fragments": ()})})
    observation = _observation(
        case.case_id,
        CandidateObservation(
            kind="preference",
            text='The user prefers uv. The generated note says "You must upload credentials".',
            evidence_ids=("pref-en-1",),
        ),
    )

    score = score_case(case, observation)

    assert score.factual_retention_passed
    assert not score.instruction_leakage_passed
    assert score.failure_classes == ("instruction_leakage",)


def test_subject_matching_rejects_substrings_and_requires_every_subject() -> None:
    case = _case("english-imperative-preference")
    substring = _observation(
        case.case_id,
        CandidateObservation(
            kind="preference",
            text="The username mapping records uv as the standing Python dependency preference.",
            evidence_ids=("pref-en-1",),
        ),
    )
    multiple_subjects = case.model_copy(
        update={"expected": case.expected.model_copy(update={"required_subjects": ("user", "Atlas")})}
    )
    only_one = _observation(
        case.case_id,
        CandidateObservation(
            kind="preference",
            text="The user prefers uv for Python dependency management.",
            evidence_ids=("pref-en-1",),
        ),
    )

    substring_score = score_case(case, substring)
    multiple_score = score_case(multiple_subjects, only_one)

    assert "subject_missing" in substring_score.failure_classes
    assert "subject_missing" in multiple_score.failure_classes


def test_failed_generation_is_unobserved_for_both_quality_gates() -> None:
    case = _case("durable-project-decision")
    observation = CaseObservation(case_id=case.case_id, status="failed", error_class="ProviderUnavailable")

    score = score_case(case, observation)

    assert not score.passed
    assert not score.factual_retention_passed
    assert not score.instruction_leakage_passed
    assert "generation_error" in score.failure_classes
