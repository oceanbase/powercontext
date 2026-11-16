# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
# http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Read retained prepare history and exact details without replay."""

import asyncio
import hashlib
import json
from collections import defaultdict, deque

from powercontext.builtin.runtime.models import PreparedContext
from powercontext.builtin.runtime.receipt import PreparedContextReceipt
from powercontext.http._generated.models import PreparedContextReceipt as PublicReceipt
from powercontext.server.dashboard.errors import ReadError
from powercontext.server.dashboard.presenters import artifact_destination


def _receipt_value(raw, prepared):
    encoded = json.dumps(raw, ensure_ascii=False, separators=(",", ":"))
    if len(encoded.encode()) > 8192:
        raise ValueError
    # Reuse the public shape and the owning Runtime's cross-field rules.
    PublicReceipt.model_validate_json(encoded)
    receipt = PreparedContextReceipt.model_validate_json(encoded)
    digest = None if prepared.content is None else "sha256:" + hashlib.sha256(prepared.content.encode()).hexdigest()
    if (
        receipt.used_bytes != prepared.content_bytes
        or receipt.used_bytes > receipt.requested_max_bytes
        or receipt.content_digest != digest
    ):
        raise ValueError
    return receipt


async def inspect_record(api, ctx, record):
    prepared = PreparedContext(
        status=record["status"], content=record["prepared_content"], content_bytes=record["content_bytes"]
    )
    receipt = None
    error = "missing"
    if record["receipt_payload"] is not None:
        try:
            receipt = _receipt_value(record["receipt_payload"], prepared)
            error = None
        except (ValueError, RecursionError):
            error = "invalid"
    selected = []
    retained_text = {}
    for snapshot in record["diagnostics_payload"].get("text_snapshots", []):
        if snapshot["stage"] != "assembly":
            continue
        for candidate in snapshot.get("items") or []:
            ref = candidate.get("artifact")
            if ref is not None:
                key = (snapshot.get("scope_id"), ref["family"], ref["artifact_id"], ref["revision"])
                retained_text[key] = candidate["text"]
    urls = {}

    async def reference_url(scope_id, ref):
        key = (scope_id, ref["family"], ref["artifact_id"], ref["revision"])
        if key not in urls:
            try:
                await api.artifact_revision(scope_id, ref["family"], ref["artifact_id"], ref["revision"])
            except ReadError as read_error:
                if read_error.status == 401:
                    raise
                urls[key] = None
            else:
                urls[key] = artifact_destination(ctx, ref, scope=scope_id)
        return urls[key]

    try:
        async with asyncio.timeout(10):
            for item in receipt.selected if receipt is not None else ():
                address = item.artifact_address
                row = {"available": False}
                if address is not None:
                    ref = address.artifact.model_dump(mode="json")
                    url = await reference_url(address.scope_id, ref)
                    if url is not None:
                        row = {
                            "available": True,
                            "family": item.kind,
                            "scope_id": address.scope_id,
                            "ref": ref,
                            "bytes": item.rendered_bytes,
                            "truncated": item.truncated,
                            "text": retained_text.get((
                                address.scope_id,
                                ref["family"],
                                ref["artifact_id"],
                                ref["revision"],
                            )),
                            "url": url,
                        }
                selected.append(row)
            for snapshot in record["diagnostics_payload"].get("text_snapshots", []):
                for item in snapshot.get("items") or []:
                    ref = item.get("artifact")
                    if ref is not None:
                        item["url"] = await reference_url(snapshot["scope_id"], ref)
    except TimeoutError:
        if receipt is not None:
            selected.extend({"available": False} for _ in range(len(receipt.selected) - len(selected)))
    ctx["context_diagnostic"] = {
        "prepared": prepared,
        "receipt": receipt,
        "error": error,
        "selected": selected,
        "fallbacks": sum(group.count for group in receipt.retrieval.searches if group.fallback_reason != "none")
        + sum(group.count for group in receipt.retrieval.reranks if group.fallback_reason != "none")
        if receipt is not None
        else None,
    }


async def load_history(api, request, ctx):
    from datetime import UTC, date, datetime, time, timedelta

    ctx["context_capture_enabled"] = getattr(request.app.state, "context_diagnostics_enabled", False)
    ctx["context_history"] = None
    if api.builtin is None:
        return
    query = request.query_params
    status = query.get("diagnostic_status") or None
    since = query.get("diagnostic_since") or None
    until = query.get("diagnostic_until") or None

    def boundary(value, *, end=False):
        if value is None:
            return None
        try:
            day = date.fromisoformat(value)
            return datetime.combine(day + timedelta(days=int(end)), time(), UTC).isoformat()
        except (ValueError, OverflowError) as error:
            raise ReadError(422, "invalid_request") from error

    page = await api.project(
        ctx["scope"],
        "context_prepare_diagnostics",
        status=status,
        since=boundary(since),
        until=boundary(until, end=True),
        cursor=query.get("diagnostic_cursor"),
    )
    ctx["context_history"] = page
    ctx["context_history_filters"] = {"status": status, "since": since, "until": until}


def _stage_rows(record, receipt, stage):
    rows = record["diagnostics_payload"].get(stage, [])
    if rows:
        return [dict(row, count=1) for row in rows]
    # Older records may have only the aggregated Receipt, without per-call counts.
    if receipt is None:
        return []
    return [row.model_dump(mode="json") for row in getattr(receipt.retrieval, stage)]


def _search_text_rows(record, receipt):
    rows = _stage_rows(record, receipt, "searches")
    snapshots = [s for s in record["diagnostics_payload"].get("text_snapshots", []) if s["stage"] == "search"]
    pending = defaultdict(deque)
    for index, snapshot in enumerate(snapshots):
        key = (snapshot["round"], snapshot["family"], snapshot.get("scope_id"), snapshot["outcome"])
        pending[key].append(index)
    used = set()
    for row in rows:
        key = (row["round"], row["family"], row.get("scope_id"), row["outcome"])
        matches = pending[key]
        index = matches.popleft() if matches else None
        row["text_snapshot"] = snapshots[index] if index is not None else None
        if index is not None:
            used.add(index)
    # Keep unmatched historical text visible without inventing execution counts or modes.
    for index, snapshot in enumerate(snapshots):
        if index not in used:
            rows.append({
                **{key: snapshot.get(key) for key in ("round", "family", "scope_id", "outcome")},
                "mode": None,
                "fallback_reason": "none",
                "count": None,
                "text_snapshot": snapshot,
            })
    return rows


def _rerank_text_rows(record, receipt):
    rows = _stage_rows(record, receipt, "reranks")
    inputs, outputs = defaultdict(deque), defaultdict(deque)
    for snapshot in record["diagnostics_payload"].get("text_snapshots", []):
        if snapshot["stage"] not in {"rerank-input", "rerank-output"}:
            continue
        key = (snapshot["round"], snapshot.get("scope_id"), snapshot["family"])
        target = inputs if snapshot["stage"] == "rerank-input" else outputs
        target[key].append(snapshot)
    for row in rows:
        key = (row["round"], row.get("scope_id"), "atomic-memory")
        row["input_text"] = inputs[key].popleft() if inputs[key] else None
        row["output_text"] = outputs[key].popleft() if outputs[key] else None
    # Preserve unmatched text without assigning it to a different Scope or call.
    for key in dict.fromkeys((*inputs, *outputs)):
        while inputs[key] or outputs[key]:
            rows.append({
                "round": key[0],
                "scope_id": key[1],
                "outcome": None,
                "fallback_reason": None,
                "count": None,
                "input_text": inputs[key].popleft() if inputs[key] else None,
                "output_text": outputs[key].popleft() if outputs[key] else None,
            })
    return rows


def _assembly_rows(record, receipt):
    payload = record["diagnostics_payload"]
    candidates = payload.get("final_candidates", {})
    selected = payload.get("selected_by_family", {})
    texts = {}
    for snapshot in payload.get("text_snapshots", []):
        if snapshot["stage"] == "assembly":
            texts.setdefault(snapshot["family"], []).append(snapshot)
    omitted = {}
    if receipt is not None:
        for group in receipt.omitted:
            omitted.setdefault(group.family, []).append(group)
    return [
        {
            "family": family,
            "candidates": candidates.get(family),
            "selected": selected.get(family),
            "omitted": omitted.get(family, []),
            "omissions_known": receipt is not None,
            "text_snapshots": texts.get(family, []),
        }
        for family in dict.fromkeys((*candidates, *selected, *omitted, *texts))
    ]


async def load_detail(api, request, ctx):
    selected = request.query_params.get("diagnostic")
    if not selected or len(selected) > 64:
        raise ReadError(422, "invalid_request")
    record = await api.project(ctx["scope"], "context_prepare_diagnostics", record_id=selected)
    if record is None:
        raise ReadError(404, "not_found")
    if record["status"] in {"ready", "empty"}:
        await inspect_record(api, ctx, record)
    else:
        ctx["context_diagnostic"] = {
            "prepared": {"status": record["status"], "content": None, "content_bytes": None},
            "receipt": None,
            "error": None,
            "selected": [],
            "fallbacks": None,
        }
    ctx["context_diagnostic"].update(record=record)
    receipt = ctx["context_diagnostic"]["receipt"]
    ctx["context_search_rows"] = _search_text_rows(record, receipt)
    ctx["context_rerank_rows"] = _rerank_text_rows(record, receipt)
    ctx["context_assembly_rows"] = _assembly_rows(record, receipt)
