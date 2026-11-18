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

"""Show this service instance's public health checks independently of Scope data."""

from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError

from powercontext.builtin.runtime.config import BuiltinConfig
from powercontext.http._generated.models import HealthResponse, ReadinessResponse
from powercontext.server.dashboard.errors import ReadError


def runtime_configuration(config: BuiltinConfig) -> dict[str, Any]:
    """Copy only display-safe values from the configuration used to assemble this Server."""
    runtime = config.runtime
    schedules = []
    for family, interval in (
        ("memory", runtime.memory_schedule_seconds),
        ("topic-memory", runtime.topic_memory_schedule_seconds),
        ("experience", runtime.experience_schedule_seconds),
    ):
        unit = "seconds"
        count = interval
        if interval is not None and interval % 3600 == 0:
            unit, count = "hours", interval / 3600
        elif interval is not None and interval % 60 == 0:
            unit, count = "minutes", interval / 60
        schedules.append({"family": family, "enabled": interval is not None, "unit": unit, "count": count})
    cron_fields = runtime.profile_cron.split()
    daily_time = None
    if (
        len(cron_fields) == 5
        and cron_fields[2:] == ["*", "*", "*"]
        and all(value.isdigit() for value in cron_fields[:2])
    ):
        daily_time = f"{int(cron_fields[1]):02d}:{int(cron_fields[0]):02d}"
    return {
        "profile_daily_time": daily_time,
        "role": runtime.artifact_processing_role,
        "dream_enabled": runtime.dream_enabled,
        "schedules": schedules,
        "profile_enabled": runtime.profile_schedule_enabled,
        "profile_cron": runtime.profile_cron,
        "profile_timezone": runtime.profile_timezone,
        "generation_configured": config.inference.generation_model is not None,
        "generation_concurrency": runtime.generation_concurrency,
        "generation_timeout_seconds": config.inference.generation_timeout_seconds,
        "max_workers": runtime.artifact_processing_max_workers,
        "worker_timeout_seconds": runtime.artifact_processing_worker_timeout_seconds,
    }


async def load_runtime(api, ctx):
    for name, path, model in (
        ("live", "/health/live", HealthResponse),
        ("ready", "/health/ready", ReadinessResponse),
    ):
        try:
            response = await api.send("GET", path)
            # A 503 with the readiness contract is a health result, not a failed read.
            if response.status_code != 200 and not (name == "ready" and response.status_code == 503):
                ctx["errors"]["runtime_" + name] = ReadError(response.status_code, "service_unavailable")
                continue
            ctx["runtime_health"][name] = model.model_validate(response.json()).model_dump(mode="json")
        except (ValidationError, ValueError):
            ctx["errors"]["runtime_" + name] = ReadError(503, "service_unavailable")
        except ReadError as error:
            ctx["errors"]["runtime_" + name] = error
    ctx["runtime_configuration"] = getattr(api.app.state, "dashboard_runtime_configuration", None)
    ctx["runtime_checked_at"] = datetime.now(UTC)
