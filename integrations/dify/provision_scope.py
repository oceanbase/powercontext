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

"""Provision an explicit Dify identity binding with a PC administrator credential."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "powercontext"))

from bridge import MemoryIdentity, binding_key
from powercontext.client import PowerContextClient
from powercontext.http import CreateScopeRequest, SetScopeBindingRequest


async def provision(args: argparse.Namespace) -> None:
    identity = MemoryIdentity(app_id=args.app_id, subject_kind=args.subject_kind, subject_id=args.subject_id)
    key = binding_key(args.namespace, identity)
    async with PowerContextClient(
        os.environ["POWERCONTEXT_BASE_URL"], token=os.environ["POWERCONTEXT_TOKEN"]
    ) as client:
        scope_id = args.scope_id
        if not scope_id:
            scope = await client.create_scope(
                CreateScopeRequest(
                    title=f"Dify {args.subject_kind} memory",
                    summary="Externally bound Dify memory scope",
                    idempotency_key=f"dify:{key.external_id}",
                )
            )
            scope_id = scope.scope_id
        result = await client.set_scope_binding(
            SetScopeBindingRequest.model_validate({"key": key.model_dump(), "scope_id": scope_id})
        )
        print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("namespace", "app-id", "subject-id"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--subject-kind", choices=("user", "business"), default="user")
    parser.add_argument("--scope-id")
    asyncio.run(provision(parser.parse_args()))
