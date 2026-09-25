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

"""Explicit price policies and usage-based cost records for LongMemEval-V2 model stages.

Prices are never hardcoded: every price comes from an explicitly provided policy, so a
recorded cost always names the provider, model, currency, per-million prices, and the
policy revision it was computed under.

A cost is computed only from real provider usage, and only when that usage reports the
cache split the policy prices. Cached input and uncached input have different prices (the
DeepSeek cache-hit rate is roughly fifty times cheaper than the cache-miss rate), so a
total-input-token approximation would misprice long, highly repeated prompts. Whenever the
split is missing, the provider/model does not match the policy, or no policy is configured,
the cost stays ``null`` with a reason — never ``0``, because a missing price is not a free
model and an unpriced token class is not free either.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass

from powercontext_eval.errors import PowerContextEvalError

# The amount fields are named ``*_usd`` and are summed across stages, so only a policy that
# actually prices United States dollars may produce them.
SUPPORTED_CURRENCY = "USD"


class CostPolicyError(PowerContextEvalError):
    """An explicitly provided cost policy is missing, malformed, or unusable."""


@dataclass(frozen=True)
class ModelPricePolicy:
    """One explicitly configured price identity for one provider and one model."""

    provider: str
    model: str
    currency: str
    input_cache_hit_price_per_million: float
    input_cache_miss_price_per_million: float
    output_price_per_million: float
    price_policy_revision: str


@dataclass
class UsageAccount:
    """Sum per-call model usage so completed calls stay priced even when processing fails.

    ``account`` accepts the normalized per-call usage records the transports produce
    (``input_tokens``/``output_tokens`` plus the optional cache split); anything a call
    did not report coerces to zero or marks the summed split incomplete.
    """

    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_hit_tokens: int = 0
    cache_miss_tokens: int = 0
    cache_split_reported: bool = True

    def account(self, usage: object) -> None:
        self.calls += 1
        if not isinstance(usage, Mapping):
            return
        self.input_tokens += _usage_int(usage.get("input_tokens"))
        self.output_tokens += _usage_int(usage.get("output_tokens"))
        hit = _usage_optional_int(usage.get("input_cache_hit_tokens"))
        miss = _usage_optional_int(usage.get("input_cache_miss_tokens"))
        if hit is None or miss is None:
            self.cache_split_reported = False
        else:
            self.cache_hit_tokens += hit
            self.cache_miss_tokens += miss


def parse_cost_policy(value: object, *, label: str = "cost policy") -> ModelPricePolicy | None:
    """Parse one explicitly provided price policy; ``None`` means "no prices configured"."""

    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise CostPolicyError(f"{label} must be a JSON object when provided")
    required = {
        "provider",
        "model",
        "currency",
        "input_cache_hit_price_per_million",
        "input_cache_miss_price_per_million",
        "output_price_per_million",
        "price_policy_revision",
    }
    if set(value) != required:
        raise CostPolicyError(f"{label} must contain exactly {', '.join(sorted(required))}")
    record = {str(key): item for key, item in value.items()}
    currency = _nonblank(record["currency"], f"{label} currency")
    if currency.upper() != SUPPORTED_CURRENCY:
        raise CostPolicyError(
            f"{label} currency must be {SUPPORTED_CURRENCY}; the recorded amount fields are USD-denominated"
        )
    return ModelPricePolicy(
        provider=_nonblank(record["provider"], f"{label} provider"),
        model=_nonblank(record["model"], f"{label} model"),
        currency=SUPPORTED_CURRENCY,
        input_cache_hit_price_per_million=_price(
            record["input_cache_hit_price_per_million"], f"{label} input_cache_hit_price_per_million"
        ),
        input_cache_miss_price_per_million=_price(
            record["input_cache_miss_price_per_million"], f"{label} input_cache_miss_price_per_million"
        ),
        output_price_per_million=_price(record["output_price_per_million"], f"{label} output_price_per_million"),
        price_policy_revision=_nonblank(record["price_policy_revision"], f"{label} price_policy_revision"),
    )


def cost_policy_record(policy: ModelPricePolicy | None) -> dict[str, object] | None:
    """Render a policy as the identity block recorded in manifests and summaries."""

    if policy is None:
        return None
    return {
        "provider": policy.provider,
        "model": policy.model,
        "currency": policy.currency,
        "input_cache_hit_price_per_million": policy.input_cache_hit_price_per_million,
        "input_cache_miss_price_per_million": policy.input_cache_miss_price_per_million,
        "output_price_per_million": policy.output_price_per_million,
        "price_policy_revision": policy.price_policy_revision,
    }


def usage_cost_usd(
    policy: ModelPricePolicy,
    *,
    cache_hit_tokens: int,
    cache_miss_tokens: int,
    output_tokens: int,
) -> float:
    """Price real provider-reported usage, charging cached and uncached input separately."""

    cache_hit_tokens = _token_count(cache_hit_tokens, "cache_hit_tokens")
    cache_miss_tokens = _token_count(cache_miss_tokens, "cache_miss_tokens")
    output_tokens = _token_count(output_tokens, "output_tokens")
    cost = (cache_hit_tokens / 1_000_000) * policy.input_cache_hit_price_per_million
    cost += (cache_miss_tokens / 1_000_000) * policy.input_cache_miss_price_per_million
    cost += (output_tokens / 1_000_000) * policy.output_price_per_million
    return round(cost, 6)


def usage_cost_block(
    policy: ModelPricePolicy | None,
    *,
    provider: str,
    model: str,
    input_tokens: int,
    cache_hit_tokens: int | None,
    cache_miss_tokens: int | None,
    output_tokens: int,
) -> dict[str, object]:
    """Render one stage's real usage with its cost, or a null cost and the unavailable reason.

    The cost requires a policy that matches both the provider and the model, plus a usage
    record that reports the cache hit/miss split the policy prices. Anything else records
    ``cost_usd: null`` with the reason instead of a number.
    """

    provider = _nonblank(provider, "provider")
    model = _nonblank(model, "model")
    input_tokens = _token_count(input_tokens, "input_tokens")
    output_tokens = _token_count(output_tokens, "output_tokens")
    hit = _optional_token_count(cache_hit_tokens, "cache_hit_tokens")
    miss = _optional_token_count(cache_miss_tokens, "cache_miss_tokens")
    usage = {
        "provider": provider,
        "model": model,
        "input_tokens": input_tokens,
        "input_cache_hit_tokens": hit,
        "input_cache_miss_tokens": miss,
        "output_tokens": output_tokens,
    }
    reason = _unavailable_reason(
        policy,
        provider=provider,
        model=model,
        input_tokens=input_tokens,
        hit=hit,
        miss=miss,
    )
    if reason is not None:
        return _unpriced_block(usage, reason)
    assert policy is not None and hit is not None and miss is not None  # narrowed by _unavailable_reason
    return {
        **usage,
        "cost_usd": usage_cost_usd(policy, cache_hit_tokens=hit, cache_miss_tokens=miss, output_tokens=output_tokens),
        "currency": policy.currency,
        "input_cache_hit_price_per_million": policy.input_cache_hit_price_per_million,
        "input_cache_miss_price_per_million": policy.input_cache_miss_price_per_million,
        "output_price_per_million": policy.output_price_per_million,
        "price_policy_revision": policy.price_policy_revision,
        "unavailable_reason": None,
    }


def model_free_cost_block(*, stage: str, reason: str) -> dict[str, object]:
    """Render the zero-cost record for a stage that provably called no model."""

    return {
        "stage": _nonblank(stage, "stage"),
        "input_tokens": 0,
        "input_cache_hit_tokens": 0,
        "input_cache_miss_tokens": 0,
        "output_tokens": 0,
        "cost_usd": 0.0,
        "reason": _nonblank(reason, "reason"),
    }


def _unavailable_reason(
    policy: ModelPricePolicy | None,
    *,
    provider: str,
    model: str,
    input_tokens: int,
    hit: int | None,
    miss: int | None,
) -> str | None:
    if policy is None:
        return "no price policy was configured for this run"
    if policy.provider != provider or policy.model != model:
        return (
            f"the configured price policy prices provider {policy.provider!r} model {policy.model!r}, "
            f"not provider {provider!r} model {model!r}"
        )
    if (hit is None) != (miss is None):
        return "provider usage reported only one side of the required cache hit/miss split"
    if hit is None:
        return "provider usage did not report a cache hit/miss split, so cached input cannot be priced"
    assert miss is not None
    if hit + miss != input_tokens:
        return "provider usage cache hit/miss tokens do not equal reported input tokens"
    return None


def _unpriced_block(usage: Mapping[str, object], reason: str) -> dict[str, object]:
    return {
        **usage,
        "cost_usd": None,
        "currency": None,
        "input_cache_hit_price_per_million": None,
        "input_cache_miss_price_per_million": None,
        "output_price_per_million": None,
        "price_policy_revision": None,
        "unavailable_reason": reason,
    }


def _price(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise CostPolicyError(f"{label} must be a finite number")
    price = float(value)
    if price < 0:
        raise CostPolicyError(f"{label} must not be negative")
    return price


def _token_count(value: int, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise CostPolicyError(f"{label} must be a non-negative integer")
    return value


def _optional_token_count(value: int | None, label: str) -> int | None:
    return None if value is None else _token_count(value, label)


def _nonblank(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CostPolicyError(f"{label} must be a non-empty string")
    return value.strip()


def _usage_int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _usage_optional_int(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None
