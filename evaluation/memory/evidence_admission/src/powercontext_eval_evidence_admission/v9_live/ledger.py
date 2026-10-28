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

"""Durable reservations and explicit recovery; unknown sends are never retried."""

from __future__ import annotations

import fcntl
import json
import os
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation
from pathlib import Path

from ..manifests import EvaluationError
from ..models import canonical, digest
from ..v6.audit import _write_new
from .contract import contract


def money(value):
    if isinstance(value, (bool, float)):
        raise EvaluationError("invalid_currency_amount")
    try:
        amount = Decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        raise EvaluationError("invalid_currency_amount") from None
    if not amount.is_finite() or amount < 0:
        raise EvaluationError("invalid_currency_amount")
    return amount


def charge(input_tokens, output_tokens):
    rates = contract()["billing_envelope"]
    return (
        Decimal(input_tokens) * Decimal(rates["input_cny_per_million"])
        + Decimal(output_tokens) * Decimal(rates["output_cny_per_million"])
    ) / Decimal(1000000)


def reservation():
    limits = contract()["billing_envelope"]
    return charge(limits["input_token_bound"], limits["output_token_bound"])


def settlement_facts(terminal, output_hash, input_budget):
    limits = contract()["billing_envelope"]
    before, after = terminal.get("input_tokens"), terminal.get("output_tokens")
    known = terminal.get("usage_known") is True and all(type(v) is int and v >= 0 for v in (before, after))
    if not known:
        reason = "usage_unknown"
    elif before > limits["input_token_bound"] or after > limits["output_token_bound"]:
        reason = "billing_envelope_violated"
    elif terminal["outcome"] != "success":
        reason = "attempt_failed"
    elif output_hash is None:
        reason = "missing_durable_output"
    elif input_budget is not None and before > input_budget:
        reason = "actual_provider_input_budget_exceeded"
    else:
        reason = None
    return {
        "attempt_id": terminal["attempt_id"],
        "outcome": terminal["outcome"],
        "error_category": terminal.get("error_category"),
        "input_tokens": before if known else None,
        "output_tokens": after if known else None,
        "charge_cny": str(charge(before, after)) if known else None,
        "output_hash": output_hash,
        "stop_reason": reason,
    }


def write_private(path: Path, value):
    # Native object order affects the SDK's model-visible JSON string. Hashes remain canonical,
    # but persistence must preserve the supplied order rather than silently rewriting that string.
    raw = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    _write_new(path, raw)
    sync_directory(path.parent)


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


@contextmanager
def exclusive(directory):
    fd = os.open(directory / "session.lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise EvaluationError("session_already_running") from None
        yield
    finally:
        os.close(fd)


class Ledger:
    def __init__(self, directory, *, create=None):
        self.path = directory / "execution.jsonl"
        if create is not None:
            _write_new(self.path, b"")
            self.append("session", **create)

    def rows(self):
        raw = self.path.read_bytes()
        if not raw.endswith(b"\n"):
            raise EvaluationError("execution_ledger_incomplete_write")
        try:
            rows = [json.loads(line) for line in raw.splitlines()]
        except (ValueError, UnicodeError):
            raise EvaluationError("execution_ledger_corrupt") from None
        previous = None
        for index, row in enumerate(rows):
            content = {k: v for k, v in row.items() if k != "hash"}
            if row.get("index") != index or row.get("previous") != previous or row.get("hash") != digest(content):
                raise EvaluationError("execution_ledger_hash_mismatch")
            previous = row["hash"]
        if not rows or rows[0].get("kind") != "session":
            raise EvaluationError("execution_ledger_missing_session")
        return rows

    def append(self, kind, **facts):
        rows = self.rows() if self.path.stat().st_size else []
        row = {"index": len(rows), "previous": rows[-1]["hash"] if rows else None, "kind": kind, **facts}
        row["hash"] = digest(row)
        with self.path.open("ab") as stream:
            stream.write(canonical(row) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        sync_directory(self.path.parent)
        return row

    def state(self):
        rows = self.rows()
        reserved, settled, reviews = {}, {}, {}
        authorization = None
        for row in rows[1:]:
            key = row.get("call_id")
            if row["kind"] == "reserved":
                if key in reserved or money(row["reservation_cny"]) != reservation():
                    raise EvaluationError("invalid_or_duplicate_reservation")
                reserved[key] = row
            elif row["kind"] == "settled":
                if key not in reserved or key in settled or row["attempt_id"] != reserved[key]["attempt_id"]:
                    raise EvaluationError("invalid_or_duplicate_settlement")
                settled[key] = row
            elif row["kind"] == "review":
                if row["pair_id"] in reviews:
                    raise EvaluationError("duplicate_pair_review")
                reviews[row["pair_id"]] = row
            elif row["kind"] == "authorization":
                if row["previous_approval_hash"] != (authorization["approval_hash"] if authorization else None):
                    raise EvaluationError("authorization_chain_mismatch")
                authorization = row
            else:
                raise EvaluationError("unknown_execution_event")
        pending = sorted(set(reserved) - set(settled))
        unknown = sorted(k for k, r in settled.items() if r["charge_cny"] is None)
        held = sum((money(reserved[k]["reservation_cny"]) for k in [*pending, *unknown]), Decimal(0))
        known = sum((money(r["charge_cny"]) for r in settled.values() if r["charge_cny"] is not None), Decimal(0))
        stopped = pending or unknown or any(r["stop_reason"] for r in settled.values())
        return {
            "reserved": reserved,
            "settled": settled,
            "reviews": reviews,
            "authorization": authorization,
            "pending_call_ids": pending,
            "unknown_usage_call_ids": unknown,
            "known_upper_charge_cny": str(known),
            "held_reservation_cny": str(held),
            "stop_required": bool(stopped),
        }

    def reserve(self, call, attempt_id, limit):
        state = self.state()
        if state["stop_required"]:
            raise EvaluationError("unresolved_or_failed_attempt_no_automatic_recovery")
        if len(state["reserved"]) >= contract()["max_calls"]:
            raise EvaluationError("session_call_limit_reached")
        if call["call_id"] in state["reserved"]:
            raise EvaluationError("call_already_reserved_never_retry")
        if money(state["known_upper_charge_cny"]) + reservation() > money(limit):
            raise EvaluationError("insufficient_currency_reservation")
        self.append(
            "reserved",
            call_id=call["call_id"],
            attempt_id=attempt_id,
            request_hash=call["request_hash"],
            reservation_cny=str(reservation()),
        )

    def settle(self, call_id, terminal, *, output_hash=None, input_budget=None):
        state = self.state()
        if (
            call_id not in state["reserved"]
            or call_id in state["settled"]
            or state["reserved"][call_id]["attempt_id"] != terminal["attempt_id"]
        ):
            raise EvaluationError("invalid_or_duplicate_settlement")
        self.append("settled", call_id=call_id, **settlement_facts(terminal, output_hash, input_budget))
