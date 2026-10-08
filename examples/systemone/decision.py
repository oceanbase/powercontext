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

"""Evaluate one bounded question through the Runtime's System One decision port."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from importlib import import_module
from pathlib import Path

import httpx
from pydantic import ValidationError

from powercontext.builtin.inference import InferenceConfigurationError
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import BuiltinConfig, DecisionRequest, open_builtin_runtime

from .adapter import SystemOneConfig, SystemOneDecisionModel
from .laya import LayaInputBudget


def load_laya_budget(checkpoint: Path) -> LayaInputBudget:
    """Read the served checkpoint without downloading or changing its files."""
    auto_tokenizer = import_module("transformers").AutoTokenizer
    config = json.loads((checkpoint / "rl_agent_config.json").read_text(encoding="utf-8"))
    tokenizer = auto_tokenizer.from_pretrained(
        str(checkpoint / "tokenizer"), local_files_only=True, trust_remote_code=False
    )
    return LayaInputBudget(
        tokenize=lambda text: tokenizer.encode(text, add_special_tokens=False, truncation=False),
        max_length=config["max_len"],
        head_max_length=config["head_max_len"],
        mask_token=tokenizer.mask_token,
    )


async def evaluate(
    config: SystemOneConfig, budget: LayaInputBudget | None, request: DecisionRequest, data_dir: Path
) -> int:
    """Expose the backend through the Runtime's shared failure handling."""
    runtime_config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{data_dir / 'runtime.db'}"))
    async with httpx.AsyncClient(timeout=config.timeout_seconds) as client:
        backend = SystemOneDecisionModel(config, client, laya_budget=budget)
        async with open_builtin_runtime(
            runtime_config, decision_model=backend, scheduler_path=data_dir / "scheduler.db"
        ) as runtime:
            decision_model = runtime.decision_model
            if decision_model is None:
                raise RuntimeError("The Runtime did not expose the injected decision model")  # noqa: TRY003
            result = await decision_model.evaluate(request)
    print(
        json.dumps(
            {
                "outcome": result.outcome,
                "used_fallback": result.used_fallback,
                "policy_id": result.policy_id,
                "confidence": result.confidence,
                "usage": result.usage.model_dump(mode="json"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 1 if result.used_fallback else 0


def main() -> int:
    """Load explicit provider settings and evaluate the same input with either backend."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir", type=Path, default=Path(".powercontext/systemone"), help="SQLite Runtime directory"
    )
    parser.add_argument("--checkpoint", type=Path, help="Laya checkpoint directory matching the served model")
    parser.add_argument("--question", default="是否应继续为这个项目使用 pytest 编写测试？")  # noqa: RUF001
    parser.add_argument("--subject", default="为当前项目新增一个回归测试。")
    parser.add_argument("--evidence", action="append", help="Evidence text; repeat for multiple entries")
    args = parser.parse_args()
    try:
        config = SystemOneConfig.model_validate({
            "provider": os.environ.get("SYSTEMONE_PROVIDER", ""),
            "endpoint": os.environ.get("SYSTEMONE_ENDPOINT", ""),
            "model": os.environ.get("SYSTEMONE_MODEL", ""),
            "api_key": os.environ.get("SYSTEMONE_API_KEY", ""),
        })
    except ValidationError:
        parser.error(
            "Invalid SYSTEMONE_* settings; check the selected provider's .env.example and endpoint requirements"
        )
    budget = None
    if config.provider == "laya":
        if args.checkpoint is None:
            parser.error("Laya requires --checkpoint matching the model running on the server")
        try:
            budget = load_laya_budget(args.checkpoint)
        except ImportError:
            parser.error("Laya requires the optional tokenizer dependency: uv run --with 'transformers>=4,<6' ...")
        except (OSError, ValueError, KeyError, TypeError, AttributeError, InferenceConfigurationError):
            parser.error(
                "Cannot load the Laya checkpoint; check tokenizer/, rl_agent_config.json, and their compatibility"
            )
    elif args.checkpoint is not None:
        parser.error("--checkpoint applies only to the Laya provider")
    data_dir = args.data_dir.expanduser().resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    request = DecisionRequest(
        decision_kind="example.coding-test-policy",
        question=args.question,
        subject=args.subject,
        evidence=tuple(args.evidence) if args.evidence else ("项目约定：使用 pytest 编写测试。",),  # noqa: RUF001
    )
    return asyncio.run(evaluate(config, budget, request, data_dir))


if __name__ == "__main__":
    raise SystemExit(main())
