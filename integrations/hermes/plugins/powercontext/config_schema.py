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

"""Declarative configuration for the PowerContext Hermes Provider."""

from plugins.memory.config_schema import (  # ty: ignore[unresolved-import]
    KIND_BOOL,
    KIND_NUMBER,
    KIND_SECRET,
    KIND_TEXT,
    STORAGE_FLAT_JSON,
    ProviderConfigSchema,
    ProviderField,
)

CONFIG_SCHEMA = ProviderConfigSchema(
    name="powercontext",
    label="PowerContext",
    storage=STORAGE_FLAT_JSON,
    docs_url="https://github.com/oceanbase/powercontext/tree/master/integrations/hermes",
    fields=(
        ProviderField(
            key="base_url",
            label="PowerContext server URL",
            kind=KIND_TEXT,
            default="http://127.0.0.1:8000",
            description="Base URL of the running PowerContext server.",
            inline=True,
        ),
        ProviderField(
            key="authorization",
            label="Authorization header",
            kind=KIND_SECRET,
            env_key="POWERCONTEXT_HERMES_AUTHORIZATION",
            description="Optional complete header value, for example: Bearer <token>.",
            inline=True,
        ),
        ProviderField(
            key="scope_id",
            label="Explicit Scope ID",
            kind=KIND_TEXT,
            default="",
            description="Optional server-owned Scope selected before durable bindings and the server default.",
        ),
        ProviderField(
            key="max_bytes",
            label="Maximum recalled context bytes",
            kind=KIND_NUMBER,
            default="8000",
            description="Bounded context returned by /v1/context/prepare.",
        ),
        ProviderField(
            key="timeout",
            label="HTTP timeout in seconds",
            kind=KIND_NUMBER,
            default="5",
            description="Per-request timeout for PowerContext.",
        ),
        ProviderField(
            key="capture_pre_compress",
            label="Capture new turns before compression",
            kind=KIND_BOOL,
            default="false",
            description="Filter and persist new user/assistant turns before Hermes compresses context.",
        ),
        ProviderField(
            key="capture_turns",
            label="Capture completed turns",
            kind=KIND_BOOL,
            default="true",
            description="Persist completed Hermes turns as PowerContext Sources.",
        ),
        ProviderField(
            key="flush_on_session_end",
            label="Flush memory at session end",
            kind=KIND_BOOL,
            default="true",
            description="Run bounded memory extraction when the Hermes session ends.",
        ),
        ProviderField(
            key="evaluation_trace",
            label="Evaluation trace",
            kind=KIND_BOOL,
            default="false",
            description="Record recalled context in per-session local JSONL files.",
        ),
        ProviderField(
            key="evaluation_trace_path",
            label="Evaluation trace directory",
            kind=KIND_TEXT,
            description="Optional directory for per-session evaluation trace files.",
        ),
    ),
)
