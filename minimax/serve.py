#!/usr/bin/env python3
"""HTTP interface to the context module (FastAPI, uvicorn).

The API is a thin transport over ``context.operations``; it holds no state of its own
and answers from seekdb on every request.  Launch:

    uvicorn serve:app --host 127.0.0.1 --port 8099

Endpoints
    GET  /health                         -> database reachability and version
    GET  /specs                          -> the spec store
    GET  /context?spec=ARR-001&spec=..   -> the assembled developer context
    POST /tasks/{task_id}/state          -> snapshot the task state
    GET  /tasks/{task_id}/state          -> latest saved state
    GET  /tasks/{task_id}/history        -> state revisions
    POST /tasks/{task_id}/import         -> re-import the saved state
    GET  /ready?spec=...                 -> progress from work_sessions
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from context import build_context, config, operations, powercontext  # noqa: E402
from context.db import connect  # noqa: E402

app = FastAPI(title="MiniMax developer context for seekdb", version="1.0.0")


class StateRequest(BaseModel):
    stage: str = ""
    reason: str = "manual"


@app.get("/health")
def health() -> dict[str, Any]:
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT version(), CURRENT_USER()")
            version, user = cur.fetchone()
            cur.execute("SELECT COUNT(*) FROM spec")
            specs = int(cur.fetchone()[0])
    finally:
        conn.close()
    return {"ok": True, "server": version, "user": user, "specs": specs}


@app.get("/specs")
def specs() -> list[dict[str, Any]]:
    return [
        {"id": spec["id"], "kind": spec["kind"], "state": spec["state"], "title": spec["title"]}
        for spec in build_context.load_specs()
    ]


@app.get("/context")
def context(spec: list[str] | None = Query(default=None)) -> dict[str, Any]:
    return operations.context_payload(spec)


@app.get("/context/text")
def context_text(spec: list[str] | None = Query(default=None), compact: bool = False) -> dict[str, str]:
    return {"text": build_context.render(operations.context_payload(spec), compact=compact)}


@app.get("/tasks/{task_id}/state")
def get_state(task_id: str) -> dict[str, Any]:
    os.environ["MINIMAX_TASK_ID"] = task_id
    payload = operations.task_payload()
    if payload is None:
        raise HTTPException(status_code=404, detail=f"no saved state for {task_id}")
    return payload


@app.post("/tasks/{task_id}/state")
def post_state(task_id: str, request: StateRequest) -> dict[str, Any]:
    os.environ["MINIMAX_TASK_ID"] = task_id
    payload = operations.task_payload()
    spec_ids = payload["payload"]["spec_ids"] if payload else config.SPEC_IDS
    return operations.save_state(request.stage, [], spec_ids, "")


@app.get("/tasks/{task_id}/history")
def get_history(task_id: str) -> list[dict[str, Any]]:
    return [{"revision": revision, "reason": reason}
            for revision, reason in powercontext.history(task_id)]


@app.post("/tasks/{task_id}/import")
def post_import(task_id: str) -> dict[str, Any]:
    os.environ["MINIMAX_TASK_ID"] = task_id
    state, summary = powercontext.import_state(task_id, build_context.build)
    return {"revision": state.revision, **summary}


@app.get("/ready")
def ready(spec: list[str] | None = Query(default=None)) -> dict[str, Any]:
    return operations.readiness(spec or config.SPEC_IDS)
