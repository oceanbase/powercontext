from __future__ import annotations

import base64
import hashlib

import pytest
from pydantic import ValidationError

from powercontext_eval.artifacts import ArmState
from powercontext_eval.benchmarks.swebench_pro.gold_overrides import (
    SOURCE559_DATASET_PATCH_SHA256,
    SOURCE559_INSTANCE_ID,
    SOURCE559_REFERENCE_PATCH,
    SOURCE559_REFERENCE_PATCH_SHA256,
    GoldValidationOverrideError,
    select_gold_validation,
)
from powercontext_eval.report import ArmReport, GoldValidationAudit, ReportBundle, render_report


def _source559_dataset_patch() -> str:
    return "source559-original-patch"


SOURCE595_INSTANCE_ID = (
    "instance_ansible__ansible-de5858f48dc9e1ce9117034e0d7e76806f420ca8-v1055803c3a812189a1133297f7f5468579283f86"
)
SOURCE595_SELECTED_TEST_FILES = (
    '["test/integration/targets/ansible-galaxy-collection/library/setup_collections.py", '
    '"test/units/galaxy/test_api.py"]'
)
SOURCE595_EFFECTIVE_TEST_FILES = '["test/units/galaxy/test_api.py"]'


SOURCE559_REAL_DATASET_PATCH = base64.b64decode(
    "ZGlmZiAtLWdpdCBhL2xpYi9jbGllbnQva2V5YWdlbnQuZ28gYi9saWIvY2xpZW50L2tleWFnZW50LmdvCmluZGV4IDM3ZDdiZmJjMzUxZmEuLmUxZGJjMGZkZjc1N2QgMTAwNjQ0Ci0tLSBhL2xpYi9jbGllbnQva2V5YWdlbnQuZ28KKysrIGIvbGliL2NsaWVudC9rZXlhZ2VudC5nbwpAQCAtMTksNiArMTksNyBAQCBwYWNrYWdlIGNsaWVudAogaW1wb3J0ICgKIAkiY29udGV4dCIKIAkiY3J5cHRvL3N1YnRsZSIKKwkiY3J5cHRvL3g1MDkiCiAJImZtdCIKIAkiaW8iCiAJIm5ldCIKQEAgLTU1NCwzICs1NTUsMTggQEAgZnVuYyAoYSAqTG9jYWxLZXlBZ2VudCkgY2VydHNGb3JDbHVzdGVyKGNsdXN0ZXJOYW1lIHN0cmluZykgKFtdc3NoLlNpZ25lciwgZXJyb3IKIAl9CiAJcmV0dXJuIGNlcnRzLCBuaWwKIH0KKworLy8gQ2xpZW50Q2VydFBvb2wgcmV0dXJucyB4NTA5LkNlcnRQb29sIGNvbnRhaW5pbmcgdHJ1c3RlZCBDQS4KK2Z1bmMgKGEgKkxvY2FsS2V5QWdlbnQpIENsaWVudENlcnRQb29sKGNsdXN0ZXIgc3RyaW5nKSAoKng1MDkuQ2VydFBvb2wsIGVycm9yKSB7CisJcG9vbCA6PSB4NTA5Lk5ld0NlcnRQb29sKCkKKwlrZXksIGVyciA6PSBhLkdldEtleShjbHVzdGVyKQorCWlmIGVyciAhPSBuaWwgeworCQlyZXR1cm4gbmlsLCB0cmFjZS5XcmFwKGVycikKKwl9CisJZm9yIF8sIGNhUEVNIDo9IHJhbmdlIGtleS5UTFNDQXMoKSB7CisJCWlmICFwb29sLkFwcGVuZENlcnRzRnJvbVBFTShjYVBFTSkgeworCQkJcmV0dXJuIG5pbCwgdHJhY2UuQmFkUGFyYW1ldGVyKCJmYWlsZWQgdG8gcGFyc2UgVExTIENBIGNlcnRpZmljYXRlIikKKwkJfQorCX0KKwlyZXR1cm4gcG9vbCwgbmlsCit9CmRpZmYgLS1naXQgYS9saWIvc3J2L2FscG5wcm94eS9sb2NhbF9wcm94eS5nbyBiL2xpYi9zcnYvYWxwbnByb3h5L2xvY2FsX3Byb3h5LmdvCmluZGV4IGM5ZGYyN2Y4OGZhNzQuLjQzZWRkODEzZTkwYzUgMTAwNjQ0Ci0tLSBhL2xpYi9zcnYvYWxwbnByb3h5L2xvY2FsX3Byb3h5LmdvCisrKyBiL2xpYi9zcnYvYWxwbnByb3h5L2xvY2FsX3Byb3h5LmdvCkBAIC0xMDksMTMgKzEwOSwxNCBAQCBmdW5jIE5ld0xvY2FsUHJveHkoY2ZnIExvY2FsUHJveHlDb25maWcpICgqTG9jYWxQcm94eSwgZXJyb3IpIHsKIC8vIFNTSFByb3h5IGlzIGVxdWl2YWxlbnQgb2YgYHNzaCAtbyAnRm9yd2FyZEFnZW50IHllcycgLXAgcG9ydCAgJXJAaG9zdCAtcyBwcm94eTolaDolcGAgYnV0IGVzdGFibGlzaGVkIFNTSAogLy8gY29ubmVjdGlvbiB0byBSZW1vdGVQcm94eUFkZHIgaXMgd3JhcHBlZCB3aXRoIFRMUyBwcm90b2NvbC4KIGZ1bmMgKGwgKkxvY2FsUHJveHkpIFNTSFByb3h5KCkgZXJyb3IgewotCWlmIGwuY2ZnLkNsaWVudFRMU0NvbmZpZyAhPSBuaWwgeworCWlmIGwuY2ZnLkNsaWVudFRMU0NvbmZpZyA9PSBuaWwgewogCQlyZXR1cm4gdHJhY2UuQmFkUGFyYW1ldGVyKCJjbGllbnQgVExTIGNvbmZpZyBpcyBtaXNzaW5nIikKIAl9CiAKIAljbGllbnRUTFNDb25maWcgOj0gbC5jZmcuQ2xpZW50VExTQ29uZmlnLkNsb25lKCkKIAljbGllbnRUTFNDb25maWcuTmV4dFByb3RvcyA9IFtdc3RyaW5ne3N0cmluZyhsLmNmZy5Qcm90b2NvbCl9CiAJY2xpZW50VExTQ29uZmlnLkluc2VjdXJlU2tpcFZlcmlmeSA9IGwuY2ZnLkluc2VjdXJlU2tpcFZlcmlmeQorCWNsaWVudFRMU0NvbmZpZy5TZXJ2ZXJOYW1lID0gbC5jZmcuU05JCiAKIAl1cHN0cmVhbUNvbm4sIGVyciA6PSB0bHMuRGlhbCgidGNwIiwgbC5jZmcuUmVtb3RlUHJveHlBZGRyLCBjbGllbnRUTFNDb25maWcpCiAJaWYgZXJyICE9IG5pbCB7CmRpZmYgLS1naXQgYS90b29sL3RzaC9wcm94eS5nbyBiL3Rvb2wvdHNoL3Byb3h5LmdvCmluZGV4IDQwZmIzZGYwZjA2YTcuLmQ3NTM0MWQ0MzY0MTYgMTAwNjQ0Ci0tLSBhL3Rvb2wvdHNoL3Byb3h5LmdvCisrKyBiL3Rvb2wvdHNoL3Byb3h5LmdvCkBAIC0xNyw2ICsxNyw3IEBAIGxpbWl0YXRpb25zIHVuZGVyIHRoZSBMaWNlbnNlLgogcGFja2FnZSBtYWluCiAKIGltcG9ydCAoCisJImNyeXB0by90bHMiCiAJImZtdCIKIAkibmV0IgogCSJvcyIKQEAgLTMyLDI2ICszMywzNSBAQCBpbXBvcnQgKAogKQogCiBmdW5jIG9uUHJveHlDb21tYW5kU1NIKGNmICpDTElDb25mKSBlcnJvciB7Ci0JY2xpZW50LCBlcnIgOj0gbWFrZUNsaWVudChjZiwgZmFsc2UpCisJdGMsIGVyciA6PSBtYWtlQ2xpZW50KGNmLCBmYWxzZSkKKwlpZiBlcnIgIT0gbmlsIHsKKwkJcmV0dXJuIHRyYWNlLldyYXAoZXJyKQorCX0KKworCWFkZHJlc3MsIGVyciA6PSB1dGlscy5QYXJzZUFkZHIodGMuV2ViUHJveHlBZGRyKQogCWlmIGVyciAhPSBuaWwgewogCQlyZXR1cm4gdHJhY2UuV3JhcChlcnIpCiAJfQogCi0JYWRkcmVzcywgZXJyIDo9IHV0aWxzLlBhcnNlQWRkcihjbGllbnQuV2ViUHJveHlBZGRyKQorCXBvb2wsIGVyciA6PSB0Yy5Mb2NhbEFnZW50KCkuQ2xpZW50Q2VydFBvb2wodGMuU2l0ZU5hbWUpCiAJaWYgZXJyICE9IG5pbCB7CiAJCXJldHVybiB0cmFjZS5XcmFwKGVycikKIAl9CisJdGxzQ29uZmlnIDo9ICZ0bHMuQ29uZmlneworCQlSb290Q0FzOiBwb29sLAorCX0KIAogCWxwLCBlcnIgOj0gYWxwbnByb3h5Lk5ld0xvY2FsUHJveHkoYWxwbnByb3h5LkxvY2FsUHJveHlDb25maWd7Ci0JCVJlbW90ZVByb3h5QWRkcjogICAgY2xpZW50LldlYlByb3h5QWRkciwKKwkJUmVtb3RlUHJveHlBZGRyOiAgICB0Yy5XZWJQcm94eUFkZHIsCiAJCVByb3RvY29sOiAgICAgICAgICAgYWxwbmNvbW1vbi5Qcm90b2NvbFByb3h5U1NILAogCQlJbnNlY3VyZVNraXBWZXJpZnk6IGNmLkluc2VjdXJlU2tpcFZlcmlmeSwKIAkJUGFyZW50Q29udGV4dDogICAgICBjZi5Db250ZXh0LAogCQlTTkk6ICAgICAgICAgICAgICAgIGFkZHJlc3MuSG9zdCgpLAotCQlTU0hVc2VyOiAgICAgICAgICAgIGNmLlVzZXJuYW1lLAorCQlTU0hVc2VyOiAgICAgICAgICAgIHRjLkhvc3RMb2dpbiwKIAkJU1NIVXNlckhvc3Q6ICAgICAgICBjZi5Vc2VySG9zdCwKLQkJU1NISG9zdEtleUNhbGxiYWNrOiBjbGllbnQuSG9zdEtleUNhbGxiYWNrLAorCQlTU0hIb3N0S2V5Q2FsbGJhY2s6IHRjLkhvc3RLZXlDYWxsYmFjaywKIAkJU1NIVHJ1c3RlZENsdXN0ZXI6ICBjZi5TaXRlTmFtZSwKKwkJQ2xpZW50VExTQ29uZmlnOiAgICB0bHNDb25maWcsCiAJfSkKIAlpZiBlcnIgIT0gbmlsIHsKIAkJcmV0dXJuIHRyYWNlLldyYXAoZXJyKQo="
).decode()


def _source559_audit(
    status: str = "passed",
    official_evaluation_transport: str = "proxy_bypassed_for_test_isolation",
) -> GoldValidationAudit:
    return GoldValidationAudit(
        instance_id=SOURCE559_INSTANCE_ID,
        mode="verified_override",
        dataset_patch_sha256=SOURCE559_DATASET_PATCH_SHA256,
        validation_patch_sha256=SOURCE559_REFERENCE_PATCH_SHA256,
        dataset_patch_status="known_failed",
        reference_validation_status="passed",
        attempt_gold_validation_status=status,
        official_evaluation_transport=official_evaluation_transport,
        source_dataset="livesweagent/claude-sonnet-4-5_swebench_pro_traj",
        source_revision="e9c3cf3611956d75ad8a78b9cce5b4a524828e22",
        source_file_oid="7d910a550fc80f16647b795e2ab23fa032ac91fa",
        source_kind="verified_reference_submission",
    )


def _dataset_audit(status: str = "pending") -> GoldValidationAudit:
    audit = select_gold_validation("instance_other", "ordinary patch").audit
    audit["attempt_gold_validation_status"] = status
    return GoldValidationAudit(**audit)


def test_reference_patch_is_static_and_hash_pinned() -> None:
    assert len(SOURCE559_REFERENCE_PATCH.encode()) == 3303
    assert hashlib.sha256(SOURCE559_REFERENCE_PATCH.encode()).hexdigest() == SOURCE559_REFERENCE_PATCH_SHA256


def test_source559_real_dataset_patch_hash_has_no_trailing_newline() -> None:
    assert len(SOURCE559_REAL_DATASET_PATCH.encode()) == 3218
    # The diff's single terminating LF is part of the raw dataset value; the
    # rejected 307e... digest came from appending one more LF while extracting
    # the JSON string with ``jq -r``.
    assert SOURCE559_REAL_DATASET_PATCH.endswith("\n")
    assert not SOURCE559_REAL_DATASET_PATCH.endswith("\n\n")
    assert hashlib.sha256(SOURCE559_REAL_DATASET_PATCH.encode()).hexdigest() == (
        "de187c18609f9a6fdedca6fb8b0fb2beb381bca169f02fa21550f67072e4f464"
    )


def test_source559_selects_override_for_real_patch_and_rejects_added_newline() -> None:
    selection = select_gold_validation(SOURCE559_INSTANCE_ID, SOURCE559_REAL_DATASET_PATCH)

    assert selection.mode == "verified_override"
    assert selection.dataset_patch_sha256 == ("de187c18609f9a6fdedca6fb8b0fb2beb381bca169f02fa21550f67072e4f464")
    appended_newline = SOURCE559_REAL_DATASET_PATCH + "\n"
    assert hashlib.sha256(appended_newline.encode()).hexdigest() == (
        "307e989e4d276031f6fa8cfdac54a3c1e6b7b92cccb24c67835f7161e5dc86a3"
    )
    with pytest.raises(GoldValidationOverrideError):
        select_gold_validation(SOURCE559_INSTANCE_ID, appended_newline)


def test_exact_instance_selects_override_and_audits_pending_before_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    original = _source559_dataset_patch()
    import powercontext_eval.benchmarks.swebench_pro.gold_overrides as overrides

    monkeypatch.setattr(overrides, "SOURCE559_DATASET_PATCH_SHA256", hashlib.sha256(original.encode()).hexdigest())
    selection = select_gold_validation(SOURCE559_INSTANCE_ID, original)
    assert selection.mode == "verified_override"
    assert selection.validation_patch == SOURCE559_REFERENCE_PATCH
    assert selection.audit["attempt_gold_validation_status"] == "pending"
    assert selection.audit["dataset_patch_sha256"] == hashlib.sha256(original.encode()).hexdigest()
    assert selection.audit["official_evaluation_transport"] == "proxy_bypassed_for_test_isolation"


def test_hash_drift_fails_closed() -> None:
    with pytest.raises(GoldValidationOverrideError):
        select_gold_validation(SOURCE559_INSTANCE_ID, "changed dataset patch")


def test_other_instances_keep_original_patch_and_have_no_reference_provenance() -> None:
    selection = select_gold_validation("instance_other", "ordinary patch")
    assert selection.validation_patch == "ordinary patch"
    assert selection.mode == "dataset_patch"
    assert selection.audit["source_dataset"] is None
    assert selection.audit["attempt_gold_validation_status"] == "pending"
    assert selection.audit["official_evaluation_transport"] == "docker_proxy"
    assert selection.audit["official_evaluation_test_selection"] == "dataset_selected_files"
    assert selection.evaluator_selected_test_files_to_run is None


def test_source595_removes_only_the_invalid_non_test_integration_target(monkeypatch: pytest.MonkeyPatch) -> None:
    patch = "source595 Gold patch"
    import powercontext_eval.benchmarks.swebench_pro.gold_overrides as overrides

    monkeypatch.setattr(overrides, "SOURCE595_DATASET_PATCH_SHA256", hashlib.sha256(patch.encode()).hexdigest())
    selection = select_gold_validation(SOURCE595_INSTANCE_ID, patch, SOURCE595_SELECTED_TEST_FILES)

    assert selection.mode == "dataset_patch"
    assert selection.validation_patch == patch
    assert selection.official_evaluation_transport == "docker_proxy"
    assert selection.official_evaluation_test_selection == "required_unit_tests_only_for_invalid_integration_target"
    assert selection.evaluator_selected_test_files_to_run == SOURCE595_EFFECTIVE_TEST_FILES
    assert selection.audit["official_evaluation_test_selection"] == (
        "required_unit_tests_only_for_invalid_integration_target"
    )


def test_source595_selection_override_fails_closed_on_row_drift(monkeypatch: pytest.MonkeyPatch) -> None:
    patch = "source595 Gold patch"
    import powercontext_eval.benchmarks.swebench_pro.gold_overrides as overrides

    monkeypatch.setattr(overrides, "SOURCE595_DATASET_PATCH_SHA256", hashlib.sha256(patch.encode()).hexdigest())
    with pytest.raises(GoldValidationOverrideError):
        select_gold_validation(SOURCE595_INSTANCE_ID, patch + " drift", SOURCE595_SELECTED_TEST_FILES)
    with pytest.raises(GoldValidationOverrideError):
        select_gold_validation(SOURCE595_INSTANCE_ID, patch, '["different.py"]')


def test_legacy_ordinary_audit_defaults_to_docker_proxy() -> None:
    data = select_gold_validation("instance_other", "ordinary patch").audit
    data.pop("official_evaluation_transport", None)

    audit = GoldValidationAudit(**data)

    assert audit.official_evaluation_transport == "docker_proxy"


def test_gold_audits_record_direct_transport_when_proxy_is_disabled() -> None:
    ordinary = _dataset_audit().model_copy(update={"official_evaluation_transport": "direct"})
    source559 = _source559_audit(official_evaluation_transport="direct")

    assert GoldValidationAudit.model_validate(ordinary.model_dump(), strict=True).official_evaluation_transport == (
        "direct"
    )
    assert GoldValidationAudit.model_validate(source559.model_dump(), strict=True).official_evaluation_transport == (
        "direct"
    )


@pytest.mark.parametrize("status", ["pending", "failed"])
def test_source559_report_requires_successful_verified_audit(status: str) -> None:
    base = {
        "title": "test",
        "revisions": {"powercontext": "a" * 40},
        "configuration": {"instance": SOURCE559_INSTANCE_ID},
        "off": ArmReport(
            arm="off", state=ArmState.TREATMENT_VALIDATED, resolved=True, passed=True, treatment_valid=True
        ),
        "on": ArmReport(arm="on", state=ArmState.TREATMENT_VALIDATED, resolved=True, passed=True, treatment_valid=True),
    }
    with pytest.raises(ValidationError):
        ReportBundle(**base)
    with pytest.raises(ValidationError):
        ReportBundle(**base, gold_validation=_source559_audit(status=status))
    report = ReportBundle(**base, gold_validation=_source559_audit())
    assert report.gold_validation is not None
    assert "| official_evaluation_transport | proxy_bypassed_for_test_isolation |" in render_report(report)


def test_source559_report_rejects_tampered_audit() -> None:
    base = {
        "title": "test",
        "revisions": {"powercontext": "a" * 40},
        "configuration": {"instance": SOURCE559_INSTANCE_ID},
        "off": ArmReport(
            arm="off", state=ArmState.TREATMENT_VALIDATED, resolved=True, passed=True, treatment_valid=True
        ),
        "on": ArmReport(arm="on", state=ArmState.TREATMENT_VALIDATED, resolved=True, passed=True, treatment_valid=True),
    }
    data = _source559_audit().model_dump()
    data["source_file_oid"] = "0" * 40
    with pytest.raises(ValidationError):
        ReportBundle(**base, gold_validation=data)
    with pytest.raises(ValidationError):
        ReportBundle(**base, gold_validation=_source559_audit(official_evaluation_transport="docker_proxy"))


@pytest.mark.parametrize("status", ["pending", "failed"])
def test_any_report_rejects_unsuccessful_gold_audit(status: str) -> None:
    ordinary = ReportBundle(
        title="test",
        revisions={"powercontext": "a" * 40},
        configuration={"instance": "instance_other"},
        off=ArmReport(arm="off", state=ArmState.TREATMENT_VALIDATED, resolved=True, passed=True, treatment_valid=True),
        on=ArmReport(arm="on", state=ArmState.TREATMENT_VALIDATED, resolved=True, passed=True, treatment_valid=True),
    )
    with pytest.raises(ValidationError):
        ReportBundle.model_validate(
            ordinary.model_dump(mode="json") | {"gold_validation": _dataset_audit(status).model_dump(mode="json")},
            strict=True,
        )


def test_non_source559_cannot_claim_verified_override_and_old_reports_still_parse() -> None:
    ordinary = ReportBundle(
        title="test",
        revisions={"powercontext": "a" * 40},
        configuration={"instance": "instance_other"},
        off=ArmReport(arm="off", state=ArmState.TREATMENT_VALIDATED, resolved=True, passed=True, treatment_valid=True),
        on=ArmReport(arm="on", state=ArmState.TREATMENT_VALIDATED, resolved=True, passed=True, treatment_valid=True),
    )
    old_payload = ordinary.model_dump(mode="json")
    old_payload.pop("gold_validation", None)
    assert ReportBundle.model_validate(old_payload, strict=True).gold_validation is None
    with pytest.raises(ValidationError):
        ReportBundle.model_validate(
            ordinary.model_dump(mode="json") | {"gold_validation": _source559_audit().model_dump()}, strict=True
        )
