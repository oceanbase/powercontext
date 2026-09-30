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

"""A loopback-only API for a persisted, real coding handoff experiment."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from collections.abc import Sequence
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import UUID, uuid4

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from powercontext.builtin.inference import InferenceConfigurationError
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import BuiltinConfig, DecisionRequest, open_builtin_runtime

from .adapter import SystemOneDecisionModel
from .decision import load_laya_budget
from .generation import Settings, generate
from .scenario import SCENARIO, verify_code

_ROOT = Path(__file__).resolve().parents[2]
_STEPS = ("seed", "recall", "generate", "review", "verify", "finish")
_PHASES = ("new", "seeded", "recalled", "generated", "reviewed", "verified", "completed")
_ARMS = ("without_memory", "with_memory")


class DemoError(Exception):
    """An actionable, credential-free error suitable for the local API."""


class CreateRun(BaseModel):
    model_config = ConfigDict(extra="forbid")
    providers: list[Literal["jev", "laya"]]


class Experiment:
    """Own isolated runs and their explicit, retryable steps."""

    def __init__(self, settings: Settings, directory: Path) -> None:
        self.settings = settings
        self.directory = directory.resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.locks: dict[str, asyncio.Lock] = {}
        self.laya_budget = None
        self.provider_status: list[dict[str, Any]] = []
        for name in ("jev", "laya"):
            status: dict[str, Any] = {"name": name, "configured": False, "model": getattr(settings, f"{name}_model")}
            try:
                settings.provider(name)
                if name == "laya":
                    if not settings.laya_checkpoint:
                        raise ValueError("Missing checkpoint")  # noqa: TRY003, TRY301
                    self.laya_budget = load_laya_budget(Path(settings.laya_checkpoint))
                status["configured"] = True
            except (ValueError, OSError, ImportError, KeyError, TypeError, AttributeError, InferenceConfigurationError):
                status["reason"] = (
                    "Check LAYA_* settings, local checkpoint and optional Transformers dependency."
                    if name == "laya"
                    else "Set JEV_ENDPOINT, JEV_MODEL and JEV_API_KEY."
                )
            self.provider_status.append(status)
        # A crashed process cannot still own a step. Its persisted partial results
        # remain reusable, but an in-flight provider request may already be billed.
        for path in self.directory.glob("*/run.json"):
            run = json.loads(path.read_text(encoding="utf-8"))
            if run.get("busy"):
                run.update(
                    busy=None, error="Service restarted during a step. Retrying may repeat an unfinished model call."
                )
                self.save(run)

    def path(self, run_id: str) -> Path:
        try:
            if str(UUID(run_id)) != run_id:
                raise ValueError  # noqa: TRY301
        except ValueError:
            raise HTTPException(404, "Run not found") from None
        return self.directory / run_id

    def read(self, run_id: str) -> dict[str, Any]:
        try:
            return json.loads((self.path(run_id) / "run.json").read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise HTTPException(404, "Run not found") from None

    def save(self, run: dict[str, Any]) -> None:
        directory = self.path(run["id"])
        directory.mkdir(parents=True, exist_ok=True)
        temporary = directory / "run.json.new"
        temporary.write_text(json.dumps(run, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(directory / "run.json")

    def create(self, providers: Sequence[str]) -> dict[str, Any]:
        available = {item["name"] for item in self.provider_status if item["configured"]}
        if not set(providers) <= available:
            raise HTTPException(400, "Selected review provider is not configured")
        run = {
            "id": str(uuid4()),
            "created_at": datetime.now(UTC).isoformat(),
            "phase": "new",
            "busy": None,
            "error": None,
            "providers": list(dict.fromkeys(providers)),
            "scenario": SCENARIO,
        }
        self.save(run)
        return run

    def status(self) -> dict[str, Any]:
        paths = sorted(self.directory.glob("*/run.json"), key=lambda path: path.stat().st_mtime, reverse=True)[:20]
        runs = [json.loads(path.read_text(encoding="utf-8")) for path in paths]
        return {
            "generation": {"configured": self.settings.generation_configured, "model": self.settings.generation_model},
            "providers": self.provider_status,
            "runs": [{key: run[key] for key in ("id", "phase", "created_at")} for run in runs],
            "current_run": runs[0]["id"] if runs else None,
        }

    async def worker(self, run: dict[str, Any], mode: str, **values: Any) -> dict[str, Any]:
        request = {"mode": mode, "directory": str(self.path(run["id"])), **values}
        spawning = asyncio.create_task(
            asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "examples.systemone.worker",
                cwd=_ROOT,
                env={"PATH": os.defpath, "LANG": "C.UTF-8"},
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        )
        try:
            process = await asyncio.shield(spawning)
        except asyncio.CancelledError:
            process = await spawning
            with suppress(ProcessLookupError):
                process.kill()
            await process.wait()
            raise
        try:
            stdout, _ = await asyncio.wait_for(process.communicate(json.dumps(request).encode()), timeout=45)
        except (TimeoutError, asyncio.CancelledError):
            with suppress(ProcessLookupError):
                process.kill()
            await process.wait()
            raise
        if process.returncode:
            raise DemoError("The independent Memory process failed; check the local data directory and dependencies.")  # noqa: TRY003
        return json.loads(stdout)

    async def step(self, run_id: str, step: str) -> dict[str, Any]:
        if step not in _STEPS:
            raise HTTPException(404, "Unknown step")
        self.read(run_id)
        lock = self.locks.setdefault(run_id, asyncio.Lock())
        if lock.locked():
            raise HTTPException(409, "A step is already running")
        async with lock:
            run = self.read(run_id)
            current = _PHASES.index(run["phase"])
            requested = _STEPS.index(step)
            if requested < current:
                return run
            if requested > current:
                raise HTTPException(409, "Complete the preceding step first")
            run.update(busy=step, error=None)
            self.save(run)
            try:
                await self.execute(run, step)
            except asyncio.CancelledError:
                run["error"] = "Step interrupted. A provider request may already have been billed."
                raise
            except Exception as error:
                run["error"] = str(error) if isinstance(error, DemoError) else f"Step failed ({type(error).__name__})."
            else:
                run["phase"] = _PHASES[current + 1]
            finally:
                run["busy"] = None
                self.save(run)
            return run

    async def execute(self, run: dict[str, Any], step: str) -> None:
        directory = self.path(run["id"])
        if step == "seed":
            run["seed"] = await self.worker(run, "seed")
        elif step == "recall":
            run["recall"] = await self.worker(
                run, "recall", scope_id=run["seed"]["scope_id"], other_scope_id=run["seed"]["other_scope_id"]
            )
            if run["recall"]["isolation"]["status"] != "passed":
                raise DemoError("Project scope isolation did not pass; generation was not started.")  # noqa: TRY003
        elif step == "generate":
            await self.generate(run)
        elif step == "review":
            await self.review(run)
        elif step == "verify":
            verified = run.setdefault("verification", {})
            for arm in _ARMS:
                if arm not in verified:
                    verified[arm] = await verify_code(run["generated"][arm]["code"], directory / arm)
                    self.save(run)
        elif step == "finish":
            await self.finish(run)

    async def generate(self, run: dict[str, Any]) -> None:
        target = {"model": self.settings.generation_model, "endpoint": self.settings.generation_endpoint}
        if "generation_target" in run and run["generation_target"] != target:
            raise DemoError("The generation model or endpoint changed. Create a new experiment for a fair comparison.")  # noqa: TRY003
        if not self.settings.generation_configured:
            raise DemoError("Configure GENERATION_ENDPOINT, GENERATION_MODEL and GENERATION_API_KEY first.")  # noqa: TRY003
        run["generation_target"] = target
        self.save(run)
        generated = run.setdefault("generated", {})
        async with httpx.AsyncClient() as client:
            for arm in _ARMS:
                if arm in generated:
                    continue
                context = run["recall"]["prepared"]["content"] if arm == "with_memory" else None
                try:
                    generated[arm] = await generate(client, self.settings, context)
                except (ValueError, RuntimeError) as error:
                    raise DemoError(str(error)) from None
                self.save(run)

    async def review(self, run: dict[str, Any]) -> None:
        reviews = run.setdefault("reviews", {})
        # Only actual, exact-citation-checked Memory text is relevant to this
        # question. Excluding the retrieval envelope does not clip its evidence.
        content = run["recall"]["prepared"]["content"]
        payload = json.loads(
            content.split("BEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1", 1)[1].split(
                "END_POWERCONTEXT_PREPARED_CONTEXT_V1", 1
            )[0]
        )
        evidence = tuple(item["content"] for item in payload["items"])
        run["review_evidence"] = list(evidence)
        directory = self.path(run["id"])
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{directory / 'runtime.db'}"))
        config = config.model_copy(
            update={"inference": config.inference.model_copy(update={"decision_timeout_seconds": 65.0})}
        )
        for provider in run["providers"]:
            results = reviews.setdefault(provider, {})
            async with httpx.AsyncClient() as client:
                backend = SystemOneDecisionModel(
                    self.settings.provider(provider),
                    client,
                    laya_budget=self.laya_budget if provider == "laya" else None,
                )
                async with open_builtin_runtime(
                    config, decision_model=backend, scheduler_path=directory / "scheduler.db"
                ) as runtime:
                    if runtime.decision_model is None:
                        raise DemoError("The Runtime did not expose the injected decision model.")  # noqa: TRY003
                    for arm in _ARMS:
                        if arm in results:
                            continue
                        started = time.monotonic()
                        result = await runtime.decision_model.evaluate(
                            DecisionRequest(
                                decision_kind="example.invoice-review",
                                question="Does this implementation satisfy every project amount-conversion rule in the evidence?",
                                subject=run["generated"][arm]["code"],
                                evidence=evidence,
                            )
                        )
                        results[arm] = {
                            "outcome": result.outcome.value,
                            "used_fallback": result.used_fallback,
                            "confidence": result.confidence,
                            "policy_id": result.policy_id,
                            "usage": result.usage.model_dump(mode="json"),
                            "elapsed_ms": round((time.monotonic() - started) * 1000),
                        }
                        self.save(run)

    async def finish(self, run: dict[str, Any]) -> None:
        scope_id = run["seed"]["scope_id"]
        if "recorded" not in run:
            summary = json.dumps(
                {
                    "run_id": run["id"],
                    "observed_tests": {
                        arm: {
                            key: run["verification"][arm][key]
                            for key in ("passed", "total", "code_sha256", "exit_code")
                        }
                        for arm in _ARMS
                    },
                    "review_opinions": {
                        name: {arm: {key: value[arm][key] for key in ("outcome", "used_fallback")} for arm in _ARMS}
                        for name, value in run["reviews"].items()
                    },
                },
                ensure_ascii=False,
            )
            run["recorded"] = await self.worker(run, "record", scope_id=scope_id, summary=summary)
            self.save(run)
        resumed = await self.worker(run, "resume", scope_id=scope_id)
        if run["id"] not in (resumed["prepared"]["content"] or ""):
            raise DemoError("The saved experiment outcome was not recalled in the new process.")  # noqa: TRY003
        run["saved"] = {**resumed, "memory_ref": run["recorded"]["memory_ref"], "record_pid": run["recorded"]["pid"]}


async def local_only(request: Request, call_next: Any) -> Any:
    host = request.headers.get("host", "")
    if request.url.hostname not in {"127.0.0.1", "localhost"}:
        return JSONResponse({"error": "Loopback host required"}, status_code=403)
    origin = request.headers.get("origin")
    if (origin and origin != f"http://{host}") or request.headers.get("sec-fetch-site") == "cross-site":
        return JSONResponse({"error": "Same-origin access required"}, status_code=403)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
    response.headers["Cache-Control"] = "no-store"
    return response


def create_app(settings: Settings, data_dir: Path) -> FastAPI:
    experiment = Experiment(settings, data_dir)
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.experiment = experiment

    app.middleware("http")(local_only)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, error: HTTPException) -> JSONResponse:
        return JSONResponse({"error": str(error.detail)}, status_code=error.status_code)

    @app.get("/api/status")
    async def status() -> dict[str, Any]:
        return experiment.status()

    @app.post("/api/runs")
    async def create(body: CreateRun) -> dict[str, Any]:
        return experiment.create(body.providers)

    @app.get("/api/runs/{run_id}")
    async def get_run(run_id: str) -> dict[str, Any]:
        return experiment.read(run_id)

    @app.post("/api/runs/{run_id}/steps/{step}")
    async def step(run_id: str, step: str) -> dict[str, Any]:
        return await experiment.step(run_id, step)

    @app.get("/api/runs/{run_id}/report")
    async def report(run_id: str) -> JSONResponse:
        return JSONResponse(
            experiment.read(run_id), headers={"Content-Disposition": f'attachment; filename="experiment-{run_id}.json"'}
        )

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=Path("examples/systemone/.env.systemone"))
    parser.add_argument("--data-dir", type=Path, default=Path(".powercontext/systemone"))
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    settings_options: dict[str, Any] = {"_env_file": args.env_file, "_env_file_encoding": "utf-8"}
    settings = Settings(**settings_options)
    uvicorn.run(create_app(settings, args.data_dir), host="127.0.0.1", port=args.port, access_log=False)


if __name__ == "__main__":
    main()
