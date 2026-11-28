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

"""Separate historical preparation content from the environment of a new execution."""

from __future__ import annotations


def preparation_compatibility(recorded, current):
    """Require identical code, locks, data and SDKs; retain interpreter/build metadata.

    Python, SQLite and the runtime distribution's Git-derived version label are execution
    metadata. Source inputs and complete request hashes must still be reexecuted and match
    before a new session is accepted. This never approves reuse of old live execution.
    """
    environments = [{}, {}]

    def content(value, side, path=""):
        if isinstance(value, list):
            return [content(v, side, f"{path}/{i}") for i, v in enumerate(value)]
        if not isinstance(value, dict):
            return value
        result = {k: content(v, side, f"{path}/{k}") for k, v in value.items()}
        if {"suite_code_hash", "runtime_code_hash", "versions"} <= value.keys():
            execution_keys = {"python", "sqlite", "powercontext"}
            environments[side][path] = {k: v for k, v in value["versions"].items() if k in execution_keys}
            result["versions"] = {k: v for k, v in value["versions"].items() if k not in execution_keys}
        return result

    matched = content(recorded, 0) == content(current, 1)
    return {
        "content_matches": matched,
        "recorded_execution_environment": environments[0],
        "current_execution_environment": environments[1],
        "historical_live_execution_reused": False,
    }
