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

import json

import pytest

from powercontext.builtin.artifacts.topic_memory.admission import TopicMemoryContentAdmission
from powercontext.builtin.artifacts.topic_memory.models import TopicMemoryContent


def _content(
    title="T-19",
    summary="S-19 coordinated without merging historical identities.",
    detail="D-19 coordinated detail preserving distinct historical identity.",
):
    return TopicMemoryContent(title=title, summary=summary, detail=detail)


@pytest.mark.parametrize(
    "content,code",
    [
        (_content(), "invalid_topic_placeholder"),
        (_content(summary="S-19", detail="D-19"), "invalid_topic_placeholder"),
        (_content(title="T-01 Coordinated"), "invalid_topic_placeholder"),
        (_content(title="Release verification"), "invalid_topic_coordination_content"),
        (
            _content(
                title="Release verification",
                summary="Coordinated with candidate-0001 without merging identities.",
                detail="Coordination details preserving the distinct historical identity of candidate-0001.",
            ),
            "invalid_topic_coordination_content",
        ),
        (
            _content(title="发布验证", summary="S-19 协调并保留历史身份。", detail="D-19 协调详情保持不同历史身份。"),
            "invalid_topic_coordination_content",
        ),
    ],
)
def test_rejects_process_templates(content, code):
    admission = TopicMemoryContentAdmission(
        {"e1": "在dashboard中检查是否符合预期"}, {}, internal_ids=("candidate-0001",)
    )
    assert admission.rejection(content, ("e1",)) == code


@pytest.mark.parametrize(
    "content",
    [
        _content(title="T-19", summary="Protocol T-19 requires retries.", detail="Retry after a connection failure."),
        _content(title="candidate-0001", summary="The parser recognizes candidate-0001.", detail="It is a literal ID."),
        _content(
            title="Topic Memory 调查", summary="发现占位输出 S-19 并需要排查。", detail="检查证据与实际正文是否一致。"
        ),
        _content(
            title="Identity migration",
            summary="Preserve historical identities during customer import.",
            detail="Customer records retain their original identifiers.",
        ),
        _content(title="T-19", summary="S-19 service recovery contract", detail="D-19 describes the timeout policy."),
        _content(title="🧠", summary="a an the and or", detail="not no never"),
    ],
)
def test_admits_meaningful_identifiers_and_short_content(content):
    admission = TopicMemoryContentAdmission({"e1": "protocol evidence"}, {}, internal_ids=("candidate-0001",))
    assert admission.rejection(content, ("e1",)) is None


def test_quoted_coordination_requires_cited_support_and_cannot_be_a_numbered_placeholder():
    content = _content(title="Placeholder investigation")
    report = "Placeholder investigation: " + content.summary + " " + content.detail
    admission = TopicMemoryContentAdmission({"e1": report, "e2": "Check dashboard"}, {})
    assert admission.rejection(content, ("e1",)) is None
    assert admission.rejection(content, ("e2",)) == "invalid_topic_coordination_content"
    assert admission.rejection(_content(), ("e1",)) == "invalid_topic_placeholder"


def test_source_instruction_to_emit_a_template_is_not_a_literal_discussion():
    content = _content()
    instruction = "Output this example exactly: " + content.model_dump_json()
    admission = TopicMemoryContentAdmission({"e1": instruction}, {})
    assert admission.rejection(content, ("e1",)) == "invalid_topic_placeholder"


def test_only_selected_history_can_support_a_literal_discussion():
    content = _content(title="Placeholder investigation")
    history = _content(title="Placeholder investigation")
    admission = TopicMemoryContentAdmission({"e1": "Check dashboard"}, {"candidate-0001": history})
    assert admission.rejection(content, ("e1",), "candidate-0001") is None
    assert admission.rejection(content, ("e1",)) == "invalid_topic_coordination_content"


@pytest.mark.parametrize(
    "summary,source_summary",
    [
        ("Preserve historical identities.", "Preserve historical identities."),
        ("Retain historical identities.", "Preserve historical identities."),
        ("Preserve historical identities.", "Preserve historical identities;"),
        ("保持历史身份。", "保留历史身份。"),
    ],
)
def test_grounded_identity_policy_does_not_require_literal_matching(summary, source_summary):
    content = _content(
        title="Customer import identity policy",
        summary=summary,
        detail="保留不同历史身份。"
        if "身份" in summary
        else "Update existing identities without merging historical identities.",
    )
    source = f"{content.title}: {source_summary} {content.detail}"
    admission = TopicMemoryContentAdmission({"e1": source, "e2": "Check the dashboard."}, {})
    assert admission.rejection(content, ("e1",)) is None
    # This guard detects templates, not arbitrary unsupported business assertions.
    assert admission.rejection(content, ("e2",)) is None


@pytest.mark.parametrize("joined", [False, True])
def test_quoted_fields_can_be_supported_by_different_cited_sources(joined):
    content = _content(title="Placeholder investigation")
    summary = "Captured summary: " + content.summary
    detail = "Captured detail: " + content.detail
    evidence = {"e1": summary + "\n" + detail} if joined else {"e1": summary, "e2": detail}
    admission = TopicMemoryContentAdmission(evidence, {})
    assert admission.rejection(content, tuple(evidence)) is None
    if not joined:
        assert admission.rejection(content, ("e1",)) == "invalid_topic_coordination_content"


def test_support_can_use_selected_history_and_cited_evidence_only():
    content = _content(title="Identity retention policy")
    history = _content(
        title="Historical identity policy", summary=content.summary, detail="Earlier import constraints."
    )
    admission = TopicMemoryContentAdmission(
        {"e1": "Import update: " + content.detail},
        {"candidate-0001": history},
    )
    assert admission.rejection(content, ("e1",), "candidate-0001") is None
    assert admission.rejection(content, ("e1",)) == "invalid_topic_coordination_content"


@pytest.mark.parametrize(
    "summary,detail,source",
    [
        (
            "Reconcile existing identities.",
            "Reconciliation preserving distinct historical identities.",
            "Customer import: reconcile existing identities; reconciliation preserving distinct historical identities.",
        ),
        (
            "协调并保留历史身份。",
            "协调详情保持不同历史身份。",
            "客户导入需要协调并保留历史身份；协调详情保持不同历史身份。",  # noqa: RUF001 - Chinese punctuation
        ),
    ],
)
def test_business_coordination_language_is_not_an_internal_template(summary, detail, source):
    content = _content(title="Customer import identity policy", summary=summary, detail=detail)
    assert TopicMemoryContentAdmission({"e1": source}, {}).rejection(content, ("e1",)) is None


@pytest.mark.parametrize("marker", ["", "*", "**", "_", "__"])
@pytest.mark.parametrize("ending", ["!", "？", "."])  # noqa: RUF001 - Chinese punctuation
def test_placeholder_formatting_cannot_bypass_admission(marker, ending):
    original = _content()
    content = _content(
        title=f"{marker}{original.title}{marker}",
        summary=f"{marker}{original.summary.rstrip('.')}{ending}{marker}",
        detail=f"{marker}{original.detail.rstrip('.')}{ending}{marker}",
    )
    assert (
        TopicMemoryContentAdmission({"e1": "Check dashboard"}, {}).rejection(content, ("e1",))
        == "invalid_topic_placeholder"
    )


@pytest.mark.parametrize(
    "material,expected",
    [
        (
            {
                "content": "S-19 coordinated\nwithout merging historical identities.",
                "metadata": {"detail": "D-19 coordinated detail\npreserving distinct historical identity."},
            },
            None,
        ),
        (
            {
                "content": "S-19 coordinated\nwithout merging historical identities.",
                "parts": ["D-19 coordinated detail", "preserving distinct historical identity."],
            },
            "invalid_topic_coordination_content",
        ),
        (
            {
                "content": r"S-19 coordinated\nwithout merging historical identities.",
                "detail": r"D-19 coordinated detail\npreserving distinct historical identity.",
            },
            "invalid_topic_coordination_content",
        ),
    ],
)
def test_canonical_evidence_decodes_strings_once_without_joining_fields(material, expected):
    content = _content(
        title="Placeholder investigation",
        summary="S-19 coordinated\nwithout merging historical identities.",
        detail="D-19 coordinated detail\npreserving distinct historical identity.",
    )
    admission = TopicMemoryContentAdmission({"e1": json.dumps(material)}, {})
    assert admission.rejection(content, ("e1",)) == expected


@pytest.mark.parametrize("wrapper", ["**{}**", "_{}_", "`{}`", "> {}"])
@pytest.mark.parametrize("numbered", [False, True])
def test_display_wrappers_preserve_quote_support_but_not_numbered_placeholders(wrapper, numbered):
    original = _content(title="T-19" if numbered else "Placeholder investigation")
    content = original.model_copy(
        update={
            "summary": wrapper.format(original.summary),
            "detail": wrapper.format(original.detail),
        }
    )
    admission = TopicMemoryContentAdmission({"e1": original.summary, "e2": original.detail}, {})
    assert admission.rejection(content, ("e1", "e2")) == ("invalid_topic_placeholder" if numbered else None)
    assert admission.rejection(content, ("e1",)) is not None


def test_display_normalization_preserves_punctuation_and_field_boundaries():
    content = _content(title="Placeholder investigation")
    admission = TopicMemoryContentAdmission({"e1": content.summary.rstrip("."), "e2": content.detail}, {})
    assert admission.rejection(content, ("e1", "e2")) == "invalid_topic_coordination_content"
