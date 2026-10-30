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

"""Opt-in real-service baseline probes with bounded, credential-free evidence.

No test collection or provider calls occur on import. Every invocation requires
an explicit output directory; completed and unresolved attempts are never
silently rerun. Reports contain numeric usage and synthetic-result assertions,
not credentials, configured endpoints, request headers, or provider errors.
This probe opens a baseline provider client without enforcing protection. A
successful sum checks connectivity and structured output only; it does not
establish protection, Experience quality, or input-encoding token savings.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import time
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict

from powercontext.builtin.inference.pydantic_ai import InferenceLimits, PydanticAIStructuredGenerator
from powercontext.server.configuration import server_settings_context
from powercontext.server.settings import ServerSettings


class AcceptanceFailure(RuntimeError):
    """A content-free, stable acceptance failure."""


class SumInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    left: int
    right: int


class SumOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    result: int


def digest(value: str | bytes) -> str:
    encoded = value.encode() if isinstance(value, str) else value
    return hashlib.sha256(encoded).hexdigest()


def write_report(directory: Path, report: dict[str, Any]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    candidate = directory / "report.pending"
    candidate.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    candidate.chmod(0o600)
    candidate.replace(directory / "report.json")


def numeric_usage(value: object) -> dict[str, Any] | None:
    """Retain provider-native numeric usage without synthesizing absent zeros."""
    if not isinstance(value, dict):
        return None
    result: dict[str, Any] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            continue
        if isinstance(item, int) and not isinstance(item, bool) and item >= 0:
            result[key] = item
        elif isinstance(item, dict):
            child = numeric_usage(item)
            if child:
                result[key] = child
    return result or None


class RequestEvidence:
    """Observe real HTTP attempts without recording request or response contents."""

    def __init__(self, directory: Path, report: dict[str, Any]) -> None:
        self.directory = directory
        self.report = report
        self.attempts: list[dict[str, Any]] = []
        report["http_attempts"] = self.attempts

    async def on_request(self, request: httpx.Request) -> None:
        body = await request.aread()
        self.attempts.append({
            "ordinal": len(self.attempts) + 1,
            "state": "outcome_unknown",
            "method": request.method,
            "request_bytes": len(body),
            "request_sha256": digest(body),
            "started_monotonic": time.monotonic(),
            "provider_usage": None,
        })
        write_report(self.directory, self.report)

    async def on_response(self, response: httpx.Response) -> None:
        raw = await response.aread()
        attempt = self.attempts[-1]
        attempt["state"] = "response_received"
        attempt["http_status"] = response.status_code
        attempt["latency_seconds"] = round(time.monotonic() - attempt.pop("started_monotonic"), 6)
        attempt["response_bytes"] = len(raw)
        try:
            value = json.loads(raw)
        except (UnicodeError, json.JSONDecodeError):
            value = None
        if isinstance(value, dict):
            attempt["provider_usage"] = numeric_usage(value.get("usage"))
            if isinstance(value.get("model"), str):
                attempt["reported_model_sha256"] = digest(value["model"])
            choices = value.get("choices")
            if isinstance(choices, list):
                attempt["finish_reasons"] = [
                    item.get("finish_reason")
                    for item in choices
                    if isinstance(item, dict) and item.get("finish_reason") in {"stop", "length", "tool_calls"}
                ]
        attempt["usage_known"] = attempt["provider_usage"] is not None
        write_report(self.directory, self.report)


async def inspect_database(settings: ServerSettings) -> dict[str, Any]:
    """Inspect the configured existing database without schema creation or migration."""
    from sqlalchemy import inspect, text

    from powercontext.builtin.persistence.oceanbase import OceanBaseConfig, OceanBaseProfile
    from powercontext.builtin.persistence.seekdb import SeekDBConfig
    from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile

    database = settings.database
    if isinstance(database, SQLiteConfig):
        context = SQLiteProfile.open_readonly(database)
        version_statement = "SELECT sqlite_version()"
    elif isinstance(database, OceanBaseConfig):
        context = OceanBaseProfile.open_existing(database, tables=())
        version_statement = "SELECT VERSION()"
    elif isinstance(database, SeekDBConfig):
        # Opening embedded SeekDB can create paths. A dedicated run owns that
        # lifecycle; this read-only probe does not start an unknown instance.
        return {"state": "not_run", "reason": "embedded-instance-requires-owned-lifecycle"}
    else:
        raise AcceptanceFailure("unsupported-database-profile")
    async with asyncio.timeout(25), context as profile, profile.database.transaction() as connection:
        backend_version = str(await connection.scalar(text(version_statement)))
        tables = set(await connection.run_sync(lambda sync: inspect(sync).get_table_names()))
        return {
            "state": "connected",
            "configured_backend_kind": database.kind,
            "backend_version": backend_version,
            "table_count": len(tables),
            "required_tables_present": {
                name: name in tables for name in ("pc_scopes", "pc_sources", "pc_artifacts", "pc_artifact_heads")
            },
            "schema_changed": False,
            "data_changed": False,
        }


async def probe_model(
    settings: ServerSettings, directory: Path, report: dict[str, Any], *, max_output_tokens: int
) -> dict[str, Any]:
    from openai import AsyncOpenAI
    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers.openai import OpenAIProvider

    report["model_stage"] = "configured-client-construction"
    write_report(directory, report)
    inference = settings.inference
    configured = inference.generation_model or ""
    prefix, separator, model = configured.partition(":")
    if not separator or prefix not in {"openai", "openai-chat"}:
        raise AcceptanceFailure("probe-requires-openai-chat-generation-profile")
    endpoint = str(inference.generation_base_url) if inference.generation_base_url else os.getenv("OPENAI_BASE_URL")
    api_key = os.getenv("OPENAI_API_KEY")
    if not endpoint or not api_key:
        raise AcceptanceFailure("configured-model-endpoint-or-key-unavailable")
    if inference.generation_headers:
        raise AcceptanceFailure("probe-does-not-infer-custom-header-authentication")
    report["model_profile_sha256"] = digest(configured)
    report["target_sha256"] = digest(endpoint + "\0" + configured)
    report["protocol"] = "openai-chat"
    recorder = RequestEvidence(directory, report)
    async with (
        httpx.AsyncClient(
            timeout=45,
            follow_redirects=False,
            event_hooks={"request": [recorder.on_request], "response": [recorder.on_response]},
        ) as transport,
        AsyncOpenAI(
            base_url=endpoint,
            api_key=api_key,
            max_retries=0,
            timeout=45,
            http_client=transport,
        ) as client,
    ):
        generator = PydanticAIStructuredGenerator(
            model=OpenAIChatModel(model, provider=OpenAIProvider(openai_client=client)),
            instructions="Return the exact sum of left and right. Use the required JSON schema. No explanation.",
            input_type=SumInput,
            output_type=SumOutput,
            limits=InferenceLimits(timeout_seconds=45, max_requests=1, max_output_tokens_per_request=max_output_tokens),
            model_settings={**inference.generation_model_settings, "temperature": 0},
        )
        result = await generator.generate(SumInput(left=17, right=25))
    return {
        "state": "completed",
        "quality_pass": result.output.result == 42,
        "adapter_usage": result.usage.model_dump(mode="json"),
        "actual_http_attempts": len(recorder.attempts),
        "max_output_tokens": max_output_tokens,
        "configured_sdk_retries": 0,
        "configured_generation_requests": 1,
        "enforcement": "baseline-not-enforcing",
        "cost": None,
        "cost_unknown_reason": "no-verified-price-policy",
    }


async def run_probe(
    settings: ServerSettings, directory: Path, report: dict[str, Any], *, max_output_tokens: int
) -> None:
    for name, call in (
        ("database", lambda: inspect_database(settings)),
        ("model", lambda: probe_model(settings, directory, report, max_output_tokens=max_output_tokens)),
    ):
        try:
            report[name] = await call()
        except Exception as error:
            # Provider/database exception text can include credentials or content.
            report[name] = {"state": "failed", "error_type": type(error).__name__}
            if isinstance(error, AcceptanceFailure):
                report[name]["reason"] = str(error)
        write_report(directory, report)
    report["status"] = (
        "PASS"
        if report["database"].get("state") == "connected" and report["model"].get("quality_pass") is True
        else "INCOMPLETE"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mode", choices=("probe",), default="probe")
    parser.add_argument("--max-output-tokens", type=int, default=1024)
    args = parser.parse_args()
    if (args.output / "report.json").exists():
        raise SystemExit("existing-report-requires-new-explicit-run")
    logging.disable(logging.CRITICAL)
    report: dict[str, Any] = {
        "schema": "powercontext.rfc2086.real-acceptance.v1",
        "status": "RUNNING",
        "mode": args.mode,
        "started_at": datetime.now(UTC).isoformat(),
        "versions": {package: version(package) for package in ("pydantic-ai-slim", "openai", "httpx", "sqlalchemy")},
        "synthetic_inputs_only": True,
        "provider_calls_authorized": True,
        "database_writes_authorized": "unique-run-scope-only",
    }
    write_report(args.output, report)
    try:
        with server_settings_context(env_file=args.env_file) as settings:
            asyncio.run(run_probe(settings, args.output, report, max_output_tokens=args.max_output_tokens))
    except Exception as error:
        report["status"] = "INCOMPLETE"
        report["error_type"] = type(error).__name__
    report["finished_at"] = datetime.now(UTC).isoformat()
    write_report(args.output, report)
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
