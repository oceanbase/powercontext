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

"""Offline engineering evidence only: no database/model transport or real secrets."""

import copy
import hashlib
import io
import json
import ssl
import sys
import time
import traceback
from pathlib import Path
from types import SimpleNamespace

import certifi
import httpx
import pytest
from powercontext_datus import HOLDOUT_SHA256, admission, native, paired, transport, worker
from powercontext_datus.freeze import IntegrityError, digest_json, snapshot
from powercontext_datus.sandbox import Sandbox
from test_review_invariants import component_plan, decimal_run  # noqa: F401 - shared no-DB fixture


@pytest.fixture
def ca(tmp_path):
    path = tmp_path / "approved-ca.pem"
    path.write_bytes(Path(certifi.where()).read_bytes())  # public trust store, not a key
    return {"ca_file": str(path), "ca_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def save(path, value):
    path.write_text(json.dumps(value))
    return {"file": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def authorize(plan, tmp_path):
    evidence = paired.read_json(Path(plan["admission_file"]))
    receipt = save(tmp_path / "external.json", {"synthetic": True})
    approval = {
        "version": 1,
        "issue_id": admission.ISSUE_ID,
        "phase": plan["evidence_kind"],
        "grant_id": "synthetic-only",
        "runner_id": "synthetic-runner",
        "approved_by": "synthetic-approver",
        "subject": "synthetic-task",
        "issued_at": int(time.time()) - 1,
        "expires_at": int(time.time()) + 3600,
        "evaluation_sha256": admission.evaluation_identity(plan),
        "conditions": dict.fromkeys(admission.CONDITIONS, True),
        "receipts": dict.fromkeys(admission.CONDITIONS, receipt),
    }
    if plan["evidence_kind"] == "independent_formal":
        ledger = tmp_path / "private-runner"
        ledger.mkdir(mode=0o700, exist_ok=True)
        approval["single_run_ledger"] = str(ledger / "single-grant.json")
    evidence["safety_approval"] = save(tmp_path / "approval.json", approval)
    save(Path(plan["admission_file"]), evidence)
    return evidence["safety_approval"]["sha256"]


@pytest.fixture
def live(component_plan, ca, tmp_path, monkeypatch):  # noqa: F811 - imported shared pytest fixture
    plan = component_plan
    plan["evidence_kind"] = "independent_formal"
    plan["tasks"] = [{"task_id": f"formal-{i}", "question": f"Synthetic held out question {i}"} for i in range(46)]
    plan["public"]["model"].update(base_url="https://model.example.invalid/v1", tls=ca)
    plan["public"]["database"] = {
        "type": "mysql",
        "host": "db.example.invalid",
        "port": 3306,
        "username": "synthetic",
        "name": "snapshot",
        "tls": ca,
    }
    plan["secret_refs"] = {"db_password": "/never/read/db", "model_api_key": "/never/read/model"}
    monkeypatch.setattr(Sandbox, "execution_identity", lambda self: {})
    sandbox = Sandbox(tmp_path / "runtime/bin/python", tmp_path / "bridge")
    plan["runtime_profile_sha256"] = digest_json(paired.runtime_profile(sandbox))
    skill = tmp_path / "enhanced/example"
    skill.mkdir()
    (skill / "SKILL.md").write_text("Synthetic skill, not learned business evidence")
    plan["arms"]["enhanced"]["skill_names"] = ["example"]
    _, table = decimal_run("1.00")
    save(
        Path(plan["oracle_file"]), {t["task_id"]: {"expected": table, "declared_answer": table} for t in plan["tasks"]}
    )
    evidence = {
        "evidence_kind": plan["evidence_kind"],
        "roster_sha256": digest_json(plan["tasks"]),
        "common_sha256": paired.file_hash(Path(plan["common_file"])),
        "oracle_sha256": paired.file_hash(Path(plan["oracle_file"])),
        "independent_author": "synthetic-author",
        "independent_reviewer": "synthetic-reviewer",
        "data_mode": "immutable_read_only_snapshot",
        "holdout_source_sha256": HOLDOUT_SHA256,
    }
    ref = save(tmp_path / "provenance.json", {"synthetic": True})
    for key in (
        "source_receipt",
        "exposure_ledger",
        "native_learning_receipts",
        "powercontext_generation_receipt",
        "skill_approval_receipt",
        "data_version_receipt",
        "oracle_consistency_receipt",
        "service_authorization_receipt",
    ):
        evidence[key] = ref
    evidence["skill_deliveries"] = {
        "native": [],
        "enhanced": [
            {
                "name": "example",
                "files": snapshot(skill),
                "scope_id": "synthetic",
                "artifact": {"family": "skill", "artifact_id": "synthetic", "revision": 1},
                "tree_digest": "synthetic",
                "archive_digest": "synthetic",
            }
        ],
    }
    for name in ("learning", "development"):
        evidence[f"{name}_roster"] = save(
            tmp_path / f"{name}.json", [{"task_id": name, "question_sha256": admission.question_fingerprint(name)}]
        )
    evidence["formal_roster_receipt"] = save(
        tmp_path / "registered.json", {"source_sha256": HOLDOUT_SHA256, "roster_sha256": digest_json(plan["tasks"])}
    )
    evidence["development_report"] = save(
        tmp_path / "development-report.json",
        {
            "evidence_kind": "independent_development",
            "state_valid": True,
            "profile_sha256": admission.profile_identity(plan),
            "real_development_runs": {"native": 1, "enhanced": 1},
            "cases": [
                {"arm": arm, "task_id": "development", "verdict": {"trace_complete": True, "native_execution": True}}
                for arm in paired.ARMS
            ],
        },
    )
    save(Path(plan["admission_file"]), evidence)
    return plan, sandbox, authorize(plan, tmp_path)


@pytest.mark.parametrize(
    "change",
    ["missing_anchor", "wrong_anchor", "expired", "condition", "receipt", "ca", "secret_ref", "runtime", "model"],
)
def test_gate_failures_precede_all_secret_reads_and_dispatch(live, tmp_path, monkeypatch, change):
    plan, sandbox, anchor = live
    calls = []
    monkeypatch.setattr(paired, "credentials_for", lambda _: calls.append("secret"))
    monkeypatch.setattr(Sandbox, "run", lambda *a, **k: calls.append("dispatch"))
    if change == "missing_anchor":
        anchor = None
    elif change == "wrong_anchor":
        anchor = "0" * 64
    elif change in {"expired", "condition"}:
        doc = paired.read_json(tmp_path / "approval.json")
        if change == "expired":
            doc["expires_at"] = 0
        else:
            doc["conditions"]["rotation_resolved"] = False
        ref = save(tmp_path / "approval.json", doc)
        evidence = paired.read_json(Path(plan["admission_file"]))
        evidence["safety_approval"] = ref
        save(Path(plan["admission_file"]), evidence)
        anchor = ref["sha256"]
    elif change == "receipt":
        save(tmp_path / "external.json", {"changed": True})
    elif change == "ca":
        Path(plan["public"]["model"]["tls"]["ca_file"]).write_text("invalid certificate")
    elif change == "secret_ref":
        plan["secret_refs"]["db_password"] = "/never/read/different"  # noqa: S105 - synthetic reference, never read
    elif change == "runtime":
        (tmp_path / "runtime/extra.py").write_text("changed runtime")
    else:
        plan["public"]["model"]["base_url"] = "http://downgrade.invalid"
    with pytest.raises(IntegrityError):
        paired.freeze_plan(plan, sandbox, approval_sha256=anchor)
    assert calls == []


@pytest.mark.parametrize("change", ["size", "registered", "overlap", "development", "profile"])
def test_formal_roster_and_development_contract(live, tmp_path, change):
    plan, _, _ = live
    evidence = paired.read_json(Path(plan["admission_file"]))
    if change == "size":
        plan["tasks"].pop()
    elif change == "registered":
        plan["tasks"][0]["question"] = "substitution"
    elif change == "overlap":
        evidence["learning_roster"] = save(
            tmp_path / "learning.json",
            [{"task_id": "learning", "question_sha256": admission.question_fingerprint(plan["tasks"][0]["question"])}],
        )
    elif change == "development":
        report = paired.read_json(tmp_path / "development-report.json")
        report["cases"].pop()
        evidence["development_report"] = save(tmp_path / "development-report.json", report)
    else:
        plan["public"]["max_turns"] += 1
    with pytest.raises(IntegrityError):
        admission.validate_formal(plan, evidence)


def test_synthetic_formal_freeze_pair_and_one_run_ledger(live, tmp_path, monkeypatch):
    plan, sandbox, anchor = live
    calls, reads = [], []

    def credentials(_):
        reads.append(1)
        return {"model_api_key": "synthetic-never-publish", "db_password": "synthetic-never-publish"}

    def dispatch(self, request, **kwargs):
        admission.validate_dispatch(request)
        assert set(request["trust_stores"]) == {"model", "database"}
        assert "admission_file" not in request and "oracle_file" not in request
        calls.append(request["prepare_only"])
        if request["prepare_only"]:
            return {
                "returncode": 0,
                "malformed_output": False,
                "records": [{"kind": "effective_config", "effective_sha256": "synthetic"}],
            }
        return decimal_run("1.00")[0]

    monkeypatch.setattr(paired, "credentials_for", credentials)
    monkeypatch.setattr(Sandbox, "run", dispatch)
    manifest = paired.freeze_plan(plan, sandbox, approval_sha256=anchor)
    report = paired.run_pair(manifest, sandbox, tmp_path / "evidence", approval_sha256=anchor)
    assert calls == [True, True] + [False] * 92
    assert reads == [1, 1]
    assert report["formal_state"] == "completed" and report["state_valid"]
    assert report["real_formal_runs"] == {"native": 46, "enhanced": 46}
    assert report["real_development_runs"] == {"native": 0, "enhanced": 0}
    assert all(arm["accepted"] for arm in report["arms"].values())
    assert "synthetic-never-publish" not in json.dumps(report)
    # These are simulated records, not actual benchmark evidence or a live score.
    with pytest.raises(FileExistsError):
        paired.run_pair(manifest, sandbox, tmp_path / "retry", approval_sha256=anchor)
    assert reads == [1, 1] and not (tmp_path / "retry").exists()


def test_native_smoke_missing_gate_never_reads_a_secret(monkeypatch):
    calls = []
    monkeypatch.setattr(native, "read_secret", lambda *a: calls.append("secret"))
    monkeypatch.setattr(native, "mysql_connector", lambda *a: calls.append("connector"))
    with pytest.raises(IntegrityError):
        native.database_smoke({"evidence_kind": "native_smoke"})
    assert not calls


@pytest.mark.parametrize("damage", ["receipt", "expiry", "runtime"])
def test_formal_midrun_drift_retains_denominator_and_stops_dispatch(live, tmp_path, monkeypatch, damage):
    plan, sandbox, anchor = live
    calls = []
    monkeypatch.setattr(paired, "credentials_for", lambda _: {})

    def dispatch(self, request, **kwargs):
        if request["prepare_only"]:
            return {
                "returncode": 0,
                "malformed_output": False,
                "records": [{"kind": "effective_config", "effective_sha256": "synthetic"}],
            }
        calls.append(request["task"]["task_id"])
        if damage == "receipt":
            save(tmp_path / "external.json", {"changed": True})
        elif damage == "expiry":
            expiry = paired.read_json(tmp_path / "approval.json")["expires_at"]
            monkeypatch.setattr(admission.time, "time", lambda: expiry + 1)
        else:
            (tmp_path / "runtime/drift.py").write_text("changed")
        return decimal_run("1.00")[0]

    monkeypatch.setattr(Sandbox, "run", dispatch)
    manifest = paired.freeze_plan(plan, sandbox, approval_sha256=anchor)
    report = paired.run_pair(manifest, sandbox, tmp_path / "aborted", approval_sha256=anchor)
    assert len(calls) == 1 and len(report["cases"]) == 92
    assert report["formal_state"] == "aborted" and not report["state_valid"]
    assert report["real_formal_runs"] == {"native": 1, "enhanced": 0}
    assert not any(arm["accepted"] for arm in report["arms"].values())


@pytest.mark.parametrize("worker_outcome", ["completed", "timeout_partial"])
@pytest.mark.parametrize("damage", ["receipt_json", "receipt_utf8", "admission_json", "valid_json_drift"])
def test_gen11_post_worker_parse_failure_preserves_case_and_aborted_report(
    live, tmp_path, monkeypatch, damage, worker_outcome
):
    plan, sandbox, anchor = live
    dispatches = []
    returned, _ = decimal_run("1.00")
    if worker_outcome == "timeout_partial":
        returned["records"] = returned["records"][:-3]
        returned.update(returncode=None, timeout=True, malformed_output=True)
    returned.update(
        stdout="\n".join(json.dumps(record) for record in returned["records"])
        + ("\n{partial" if worker_outcome == "timeout_partial" else "\n"),
        process_started=True,
        not_started=False,
        state_valid=True,
        stderr_bytes=17,
        wall_seconds=0.125,
    )
    original = copy.deepcopy(returned)
    monkeypatch.setattr(paired, "credentials_for", lambda _: {})

    def dispatch(self, request, **kwargs):
        if request["prepare_only"]:
            return {
                "returncode": 0,
                "malformed_output": False,
                "records": [{"kind": "effective_config", "effective_sha256": "synthetic"}],
            }
        dispatches.append(request["task"]["task_id"])
        target = Path(plan["admission_file"]) if damage == "admission_json" else tmp_path / "external.json"
        target.write_bytes(
            {
                "receipt_json": b'{"unfinished":',
                "receipt_utf8": b"\xff",
                "admission_json": b'{"unfinished":',
                "valid_json_drift": b'{"changed":true}',
            }[damage]
        )
        return returned

    monkeypatch.setattr(Sandbox, "run", dispatch)
    manifest = paired.freeze_plan(plan, sandbox, approval_sha256=anchor)
    output = tmp_path / "gen11-evidence"
    report = paired.run_pair(manifest, sandbox, output, approval_sha256=anchor)
    assert dispatches == [plan["tasks"][0]["task_id"]]
    paths = sorted(output.glob("case-*.json"))
    assert len(paths) == len(report["cases"]) == 92
    first = paired.read_json(paths[0])
    # Started evidence is not replaced by the unstarted placeholder, even when
    # the worker timed out and the last stdout line was incomplete.
    assert first == {
        "arm": "native",
        "task_id": plan["tasks"][0]["task_id"],
        **original,
        "state_valid": False,
        "control_failure": "leakage/state_drift",
    }
    for path in paths[1:]:
        case = paired.read_json(path)
        assert not case["process_started"] and case["not_started"] and not case["records"]
    assert report["formal_state"] == "aborted" and not report["state_valid"]
    assert paired.read_json(output / "report.json") == json.loads(json.dumps(report))
    assert report["real_formal_runs"] == {"native": 1, "enhanced": 0}
    assert all(
        arm["total"] == arm["results_present"] == 46 and not arm["accepted"] and arm["total_steps"] is None
        for arm in report["arms"].values()
    )
    assert paired.read_json(tmp_path / "private-runner/single-grant.json")["state"] == "started"


@pytest.mark.parametrize("reader", [admission.read_document, paired.read_json], ids=["admission", "paired"])
@pytest.mark.parametrize("damage", ["syntax", "utf8", "integer_limit", "depth"])
def test_gen11_document_parse_errors_have_bounded_integrity_diagnostics(tmp_path, reader, damage):
    path = tmp_path / "document.json"
    payload = b"SYNTHETIC_DOCUMENT_CONTENT"
    path.write_bytes(
        {
            "syntax": b'{"value":"' + payload,
            "utf8": b"\xff" + payload,
            "integer_limit": b"1" * 641,
            "depth": b"[" * 10000 + b"]" * 10000,
        }[damage]
    )
    previous = sys.get_int_max_str_digits()
    try:
        sys.set_int_max_str_digits(640)
        with pytest.raises(IntegrityError) as caught:
            reader(path)
    finally:
        sys.set_int_max_str_digits(previous)
    assert str(caught.value) == "invalid JSON evidence document"
    assert caught.value.__cause__ is None and caught.value.__suppress_context__
    assert payload.decode() not in "".join(traceback.format_exception(caught.value))


@pytest.mark.parametrize("damage", ["syntax", "utf8"])
def test_gen11_preflight_parse_failure_reads_no_secret_and_claims_no_grant(live, tmp_path, monkeypatch, damage):
    plan, sandbox, anchor = live
    calls = []
    monkeypatch.setattr(paired, "credentials_for", lambda _: calls.append("secret"))
    monkeypatch.setattr(Sandbox, "run", lambda *a, **k: calls.append("worker"))
    Path(plan["admission_file"]).write_bytes(b"{" if damage == "syntax" else b"\xff")
    with pytest.raises(IntegrityError):
        paired.freeze_plan(plan, sandbox, approval_sha256=anchor)
    assert not calls and not (tmp_path / "private-runner/single-grant.json").exists()


@pytest.mark.parametrize("damage", ["syntax", "utf8"])
def test_gen11_final_validation_parse_failure_still_writes_complete_report(live, tmp_path, monkeypatch, damage):
    plan, sandbox, anchor = live
    calls = []
    monkeypatch.setattr(paired, "credentials_for", lambda _: {})

    def dispatch(self, request, **kwargs):
        if request["prepare_only"]:
            return {
                "returncode": 0,
                "malformed_output": False,
                "records": [{"kind": "effective_config", "effective_sha256": "synthetic"}],
            }
        calls.append(request["task"]["task_id"])
        return decimal_run("1.00")[0]

    monkeypatch.setattr(Sandbox, "run", dispatch)
    manifest = paired.freeze_plan(plan, sandbox, approval_sha256=anchor)
    original_write = paired.write_json

    def write(path, value):
        original_write(path, value)
        if path.name == "case-0091.json":
            Path(plan["admission_file"]).write_bytes(b"{" if damage == "syntax" else b"\xff")

    monkeypatch.setattr(paired, "write_json", write)
    output = tmp_path / "final-validation"
    report = paired.run_pair(manifest, sandbox, output, approval_sha256=anchor)
    assert len(calls) == len(report["cases"]) == len(list(output.glob("case-*.json"))) == 92
    assert report["formal_state"] == "aborted" and not report["state_valid"]
    assert report["real_formal_runs"] == {"native": 46, "enhanced": 46}
    assert all(arm["total"] == 46 and not arm["accepted"] for arm in report["arms"].values())
    assert paired.read_json(output / "report.json") == json.loads(json.dumps(report))


def test_live_sample_safety_path_without_transport(live, tmp_path, monkeypatch):
    plan, sandbox, _ = live
    plan["evidence_kind"] = "independent_learning"
    plan["tasks"] = [{"task_id": "learning-one", "question": "Synthetic independent learning question"}]
    evidence = paired.read_json(Path(plan["admission_file"]))
    evidence.update(evidence_kind=plan["evidence_kind"], roster_sha256=digest_json(plan["tasks"]))
    save(Path(plan["admission_file"]), evidence)
    anchor = authorize(plan, tmp_path)
    calls = []
    monkeypatch.setattr(paired, "credentials_for", lambda _: {})

    def dispatch(self, request, **kwargs):
        admission.validate_dispatch(request)
        calls.append(request["task"]["task_id"])
        return decimal_run("1.00")[0]

    monkeypatch.setattr(Sandbox, "run", dispatch)
    report = paired.run_samples(plan, sandbox, tmp_path / "samples", approval_sha256=anchor)
    assert calls == ["learning-one"] and report["state_valid"]
    assert report["formal_state"] == "not_started"


def test_formal_dispatch_claim_prevents_replay_and_cross_run(live, tmp_path):
    plan, _, anchor = live
    approval = admission.validate_safety(plan, anchor)
    admission.claim_formal(approval, "manifest", digest_json(plan), "first-run")
    admission.claim_dispatch(approval, plan, "native", plan["tasks"][0], "first-run", "manifest")
    with pytest.raises(FileExistsError):
        admission.claim_dispatch(approval, plan, "native", plan["tasks"][0], "first-run", "manifest")
    with pytest.raises(IntegrityError):
        admission.claim_dispatch(approval, plan, "enhanced", plan["tasks"][0], "different-run", "manifest")
    with pytest.raises(IntegrityError):
        admission.claim_dispatch(approval, plan, "enhanced", plan["tasks"][0], "first-run", "different-manifest")


def test_worker_formal_requires_permit_and_verified_ca_before_graph(live, monkeypatch):
    plan, _, anchor = live
    approval = admission.validate_safety(plan, anchor)
    calls = []
    monkeypatch.setattr(
        worker, "probe_boundary", lambda _: {"all_denied": True, "frozen_read_only": True, "proc_absent": True}
    )
    monkeypatch.setattr(worker, "execute_graph", lambda *a, **k: calls.append(k))
    request = {
        "evidence_kind": plan["evidence_kind"],
        "public": plan["public"],
        "task": plan["tasks"][0],
        "denied_paths": [],
    }
    trace = SimpleNamespace(emit=lambda *a, **k: None, observe_model_http=lambda *a: None)
    with pytest.raises(IntegrityError):
        worker.execute_request(request, trace)
    request["admission_permit"] = admission.dispatch_permit(plan, approval, request["task"])
    request["trust_stores"] = {
        name: transport.trust_bytes(plan["public"][name]["tls"]) for name in ("model", "database")
    }
    corrupted = copy.deepcopy(request)
    corrupted["trust_stores"]["database"] += "changed"
    with pytest.raises(IntegrityError):
        worker.execute_request(corrupted, trace)
    assert not calls
    worker.execute_request(request, trace)
    assert calls == [{"fixture": False}]


def test_exact_trust_store_no_ambient_roots_and_overrides(ca, monkeypatch):
    monkeypatch.setenv("SSL_CERT_FILE", "/never/read/ambient-ca")
    context = transport.tls_context(ca)
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
    assert not context.hostname_checks_common_name
    assert context.minimum_version == ssl.TLSVersion.TLSv1_2
    original = httpx.Client.__init__
    observed = []
    monkeypatch.setattr(httpx.Client, "send", lambda *a, **kw: observed.append(kw))
    with transport.model_tls(context):
        with httpx.Client(verify=False, trust_env=True, follow_redirects=True) as client:  # noqa: S501 - assert override is forbidden
            assert client._transport._pool._ssl_context is context
            assert not client._trust_env and not client.follow_redirects
            client.send(httpx.Request("GET", "https://model.example.invalid"), follow_redirects=True)
        with pytest.raises(IntegrityError):
            httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200)))
    assert httpx.Client.__init__ is original
    assert observed == [{"follow_redirects": False}]


def test_capture_and_tls_patch_ownership_unwinds_on_graph_failure(live, monkeypatch):
    from contextlib import ExitStack

    from powercontext_datus.capture import ExecutionTrace

    plan, _, anchor = live
    approval = admission.validate_safety(plan, anchor)
    monkeypatch.setattr(
        worker, "probe_boundary", lambda _: {"all_denied": True, "frozen_read_only": True, "proc_absent": True}
    )
    marker = RuntimeError("synthetic graph construction failure")

    def fail(*a, **k):
        raise marker

    monkeypatch.setattr(worker, "execute_graph", fail)
    originals = {
        (cls, name): getattr(cls, name) for cls in (httpx.Client, httpx.AsyncClient) for name in ("send", "__init__")
    }
    request = {
        "evidence_kind": plan["evidence_kind"],
        "public": plan["public"],
        "task": plan["tasks"][0],
        "denied_paths": [],
    }
    request["admission_permit"] = admission.dispatch_permit(plan, approval, request["task"])
    request["trust_stores"] = {
        name: transport.trust_bytes(plan["public"][name]["tls"]) for name in ("model", "database")
    }
    with ExitStack() as stack:
        trace = SimpleNamespace(_stack=stack, emit=lambda *a, **k: None, online=False)
        trace.observe_model_http = lambda endpoint: ExecutionTrace.observe_model_http(trace, endpoint)
        with pytest.raises(RuntimeError) as caught:
            worker.execute_request(request, trace)
        assert caught.value is marker
    assert all(getattr(cls, name) is original for (cls, name), original in originals.items())


@pytest.mark.parametrize("damage", ["hash", "missing", "symlink", "invalid_pem"])
def test_bad_trust_material_fails_before_connector_import(ca, tmp_path, monkeypatch, damage):
    if damage == "hash":
        ca["ca_sha256"] = "0" * 64
    elif damage == "missing":
        ca["ca_file"] = str(tmp_path / "absent.pem")
    elif damage == "symlink":
        link = tmp_path / "alias.pem"
        link.symlink_to(ca["ca_file"])
        ca["ca_file"] = str(link)
    else:
        Path(ca["ca_file"]).write_text("not a certificate")
        ca["ca_sha256"] = hashlib.sha256(b"not a certificate").hexdigest()
    calls = []
    monkeypatch.setattr(transport.importlib, "import_module", lambda name: calls.append(name))
    with pytest.raises((IntegrityError, ssl.SSLError)):
        transport.mysql_connector({"tls": ca}, "synthetic-only")
    assert not calls


@pytest.mark.parametrize("outcome", ["no_ssl", "certificate_failure", "valid_tls"])
def test_real_pymysql_authentication_socket_never_downgrades(ca, monkeypatch, outcome):
    from pymysql.connections import Connection
    from pymysql.constants import CLIENT
    from pymysql.protocol import MysqlPacket

    context = transport.tls_context(ca)
    events, connections = [], []
    raw = SimpleNamespace(close=lambda: events.append("closed"))
    wrapped = SimpleNamespace(makefile=lambda mode: io.BytesIO(), close=lambda: events.append("tls_closed"))

    def wrap(self, sock, *, server_hostname):
        assert self is context and sock is raw and server_hostname == "db.example.invalid"
        events.append("verify")
        if outcome == "certificate_failure":
            raise ssl.SSLCertVerificationError("synthetic untrusted peer")  # noqa: TRY003
        return wrapped

    def connect(self):
        connections.append(self)
        self._sock = raw
        self.server_version = "8.0.0"
        self.server_capabilities = CLIENT.SECURE_CONNECTION | (0 if outcome == "no_ssl" else CLIENT.SSL)
        self.salt = b"0" * 20
        self._auth_plugin_name = "mysql_native_password"
        self.write_packet = lambda payload: events.append((
            "ssl_request" if len(payload) == 32 else "auth",
            len(payload),
        ))
        self._read_packet = lambda: MysqlPacket(b"\x00\x00\x00\x02\x00\x00\x00", "utf8")
        self._request_authentication()  # locked, real driver; only the socket is synthetic

    monkeypatch.setattr(Connection, "connect", connect)
    monkeypatch.setattr(ssl.SSLContext, "wrap_socket", wrap)
    database = {"host": "db.example.invalid", "port": 3306, "username": "synthetic", "name": "synthetic"}
    if outcome == "valid_tls":
        connection = transport.mysql_connection(database, "synthetic-password", context)
        assert events[0] == ("ssl_request", 32) and events[1] == "verify" and events[2][0] == "auth"
        connection._force_close()
    else:
        from pymysql.err import OperationalError

        with pytest.raises((OperationalError, ssl.SSLCertVerificationError)):
            transport.mysql_connection(database, "synthetic-password", context)
        assert not any(isinstance(event, tuple) and event[0] == "auth" for event in events)
        assert connections[0]._sock is None and "closed" in events
        if outcome == "no_ssl":
            assert events == ["closed"]


def test_mysql_connector_installs_creator_on_every_engine_generation(ca, monkeypatch):
    # Native constructor contract is checked separately under the Datus pin.
    import threading

    import sqlalchemy

    class Native:
        def __init__(self, config):
            assert config["password"] == ""
            self._engine_lock = threading.Lock()
            self.engine = None
            self._owns_engine = False
            self.timeout_seconds = 30

    real_import = transport.importlib.import_module
    monkeypatch.setattr(
        transport.importlib,
        "import_module",
        lambda name: SimpleNamespace(MySQLConnector=Native) if name == "datus_mysql" else real_import(name),
    )
    engines, connects = [], []

    def engine(url, **kwargs):
        assert url == "mysql+pymysql://" and kwargs["pool_pre_ping"] is True
        engines.append(kwargs)
        return object()

    monkeypatch.setattr(sqlalchemy, "create_engine", engine)
    monkeypatch.setattr(transport, "mysql_connection", lambda db, pw, ctx: connects.append((pw, ctx)))
    connector = transport.mysql_connector(
        {"host": "db.example.invalid", "port": 3306, "username": "synthetic", "name": "synthetic", "tls": ca},
        "synthetic",
    )
    first = connector._ensure_engine()
    assert connector._ensure_engine() is first
    engines[0]["creator"]()
    connector.engine = None
    connector._owns_engine = False
    assert connector._ensure_engine() is not first
    engines[1]["creator"]()
    assert len(connects) == 2 and connects[0][1] is connects[1][1]
    connector.engine = object()
    with pytest.raises(IntegrityError):
        connector._ensure_engine()
