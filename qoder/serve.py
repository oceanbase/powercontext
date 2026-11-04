"""HTTP API for SyncRecall (FastAPI).

Endpoints
    GET  /api/health                     seekdb version, PowerContext readiness, embedder
    GET  /api/bootstrap?user=            courses, memory snapshot, counters
    POST /api/recall                     question -> hybrid hits + citations + SQL + memory events
    GET  /api/review?user=&course=       review plan (seekdb schedule + PowerContext weak points)
    POST /api/review/grade               SM-2 update in seekdb + weak/strong write to PowerContext
    GET  /api/profile?user=              PowerContext memory snapshot
    POST /api/profile                    write retrieval preferences into PowerContext
    POST /api/focus                      write the current study focus into PowerContext
    GET  /api/events?user=&limit=        seekdb audit trail (retrievals, grades, memory writes)
"""

from __future__ import annotations

import io
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent
# Run directly from a source checkout: make ``src/`` importable.
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from syncrecall.memory import PowerContextUnavailable  # noqa: E402
from syncrecall.service import Service  # noqa: E402
from syncrecall.store import DB  # noqa: E402

app = FastAPI(title="SyncRecall", version="1.0.0", description=__doc__)
service = Service()
db = DB()

WEB = ROOT / "web"


class RecallRequest(BaseModel):
    user: str = "demo-user"
    question: str = Field(min_length=1, max_length=500)
    limit: Optional[int] = Field(default=None, ge=1, le=30)


class GradeRequest(BaseModel):
    user: str = "demo-user"
    card_id: str
    grade: int = Field(ge=0, le=5)


class ProfileRequest(BaseModel):
    user: str = "demo-user"
    courses: List[str] = Field(default_factory=list)
    source_types: List[str] = Field(default_factory=list)
    keyword_weight: float = Field(default=0.6, ge=0.0, le=1.0)
    vector_weight: float = Field(default=0.4, ge=0.0, le=1.0)
    top_k: int = Field(default=8, ge=1, le=30)
    tag: str = ""
    reason: str = "用户在界面调整检索偏好"


class FocusRequest(BaseModel):
    user: str = "demo-user"
    focus: str = Field(min_length=1, max_length=200)


@app.get("/api/health")
def health() -> Dict[str, Any]:
    return service.health()


@app.get("/api/bootstrap")
def bootstrap(user: str = "demo-user") -> Dict[str, Any]:
    try:
        snapshot = service.get_profile(user)
    except PowerContextUnavailable as error:
        raise HTTPException(status_code=503, detail=f"PowerContext 不可用：{error}") from error
    with db.cursor() as cur:
        cur.execute("SELECT DISTINCT course FROM note ORDER BY course")
        courses = [row[0] for row in cur.fetchall()]
        cur.execute("SELECT DISTINCT source_type FROM note ORDER BY source_type")
        source_types = [row[0] for row in cur.fetchall()]
    return {
        "user": user,
        "courses": courses,
        "source_types": source_types,
        "notes": db.table_count("note"),
        "cards": db.table_count("review_card"),
        "embedder": service.embedder_name,
        "memory": snapshot,
        "examples": [
            "Raft 怎么保证不丢已提交的日志",
            "为什么 InnoDB 可重复读下 update 后还能看到别人的修改",
            "Paxos 和 Raft 到底有什么区别",
            "2PC 的阻塞点是什么",
            "B+ 树为什么会页分裂",
        ],
    }


@app.post("/api/recall")
def recall(request: RecallRequest) -> Dict[str, Any]:
    try:
        result = service.recall(request.user, request.question, limit=request.limit)
    except (PowerContextUnavailable, RuntimeError) as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    return result.as_dict()


@app.get("/api/review")
def review(user: str = "demo-user", course: str = "") -> Dict[str, Any]:
    try:
        return service.review_plan(user, course)
    except (PowerContextUnavailable, RuntimeError) as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@app.post("/api/review/grade")
def grade(request: GradeRequest) -> Dict[str, Any]:
    try:
        return service.grade_card(request.user, request.card_id, request.grade)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@app.get("/api/profile")
def profile(user: str = "demo-user") -> Dict[str, Any]:
    try:
        return service.get_profile(user)
    except PowerContextUnavailable as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@app.post("/api/profile")
def save_profile(request: ProfileRequest) -> Dict[str, Any]:
    payload = request.model_dump(exclude={"user", "reason"})
    try:
        return service.set_profile(request.user, payload, reason=request.reason)
    except PowerContextUnavailable as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@app.post("/api/focus")
def save_focus(request: FocusRequest) -> Dict[str, Any]:
    try:
        return service.set_focus(request.user, request.focus)
    except PowerContextUnavailable as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@app.get("/api/events")
def events(user: str = "demo-user", limit: int = 40) -> Dict[str, Any]:
    rows = db.fetch_events(limit=max(1, min(200, limit)))
    for row in rows:
        row["created_at"] = str(row["created_at"])
        try:
            row["detail"] = __import__("json").loads(row["detail"])
        except (TypeError, ValueError):
            pass
    return {"user": user, "events": rows}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB / "index.html")


app.mount("/static", StaticFiles(directory=str(WEB)), name="static")
