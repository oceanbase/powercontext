---
title: Inference privacy protection
description: Check final model requests and complete raw responses at owned provider transports.
---

# Inference privacy protection

PowerContext checks model requests before transmission and complete raw responses before delivery at its owned inference transports. Protection is opt-in, requires a verified target profile, and introduces no database tables.

## Inspection preserves content

In `enforcing` mode, admitted request bodies and complete supported response bodies pass through unchanged,
including literal text that looks like a placeholder. Forbidden values cause refusal; PowerContext does not
replace them and send an altered request. This protection does not redact, pseudonymize, or automatically restore
values in prompts or responses.

Replacing a value and restoring it later does not guarantee equivalent task decisions. Carrying an opaque
identifier between fields differs from reasoning about an actual IP range, path ownership, or token claims.
A substitute can change the evidence used to choose an action; restoring a real path after the model selects
the wrong operation does not correct that operation. This protection provides no reversible masking mechanism
or guarantee that substituted content preserves task meaning.

## Configure a supported target

Privacy configuration belongs to the trusted deployment, under `InferenceConfig.protection`.
Its default mode is `disabled`, preserving existing inference behavior. `shadow` and `advisory` observe already
materialized requests only; they do not inspect responses or stop delivery. They must not be described as preventing disclosure. `enforcing` blocks or withholds when a
required check fails. Representation or context reuse options cannot disable an enforcing check.

For example, construct an inference configuration with a controlled text-only endpoint:

```python
from pydantic import SecretStr

from powercontext.builtin.inference.privacy import InferenceProtectionConfig, UpstreamProfile
from powercontext.builtin.runtime import InferenceConfig

inference = InferenceConfig(
    generation_model="openai:approved-model",
    generation_base_url="https://models.example/v1",
    generation_model_settings={"max_tokens": 1024},
    protection=InferenceProtectionConfig(
        mode="enforcing",
        forbidden_values=(SecretStr("synthetic-secret-for-a-local-test"),),
        targets={
            "coding": UpstreamProfile(
                endpoint="https://models.example/v1/chat/completions",
                model="approved-model",
                protocol="openai-chat",
                context_window_tokens=32768,
                max_output_tokens=1024,
                validated_utf8_token_bound=False,
            )
        },
    ),
)
```

`SecretStr` hides the configured value when it is displayed; rule matching still uses its actual value.
It does not redact request or response content.

The endpoint, model, and limits are illustrative. This example deliberately withholds until the deployment has
verified that its model's tokenizer and protocol template fit the conservative UTF-8 bound and configured protocol
reserve, then sets `validated_utf8_token_bound=True`. A character-count estimate is not a hard model-window bound.
Generating targets need a declared context window, output limit, and an actual positive output limit in the final
request. A payload that exceeds the bound is withheld.

Server deployments can set the same object as JSON in `POWERCONTEXT_SERVER_INFERENCE_PROTECTION`.
Keep secret values in the deployment's protected configuration. Never put real credentials in a checked-in example.
Exact model aliases returned by a provider must be explicitly listed in `response_models` when they differ from
the requested model.

An `openai-responses` target can set `admit_encrypted_items` to `["reasoning"]`, `["agent_message"]`, or both.
It is empty by default. Clients such as Codex send `store: false` with `include: ["reasoning.encrypted_content"]`,
so responses carry encrypted reasoning that later requests replay, and a subagent receives its task as an encrypted
`agent_message` part. Core cannot inspect these values:

- At `before_release`, Core admits the encrypted reasoning in the response. When the request names a conversation, it
  returns a `receipt` for that principal, Scope, target and conversation, valid for one hour. The receipt proves the
  response's encrypted values. It also proves, as handover values, the function-call argument values that the checked
  request's tool definitions declare `"encrypted": true` (Codex declares the task of `spawn_agent` this way). Other
  argument values are not receipted.
- At `before_send`, an encrypted value at an admitted position is admitted only when it is proven, unexpired, by a
  presented receipt issued to the request's conversation. The positions are the `encrypted_content` of a root
  `reasoning` item, and an `agent_message` content part whose only fields are `type: "encrypted_content"` and
  `encrypted_content`. A receipt issued to the request's `parent_conversation_id` proves only its handover values, and
  only in an `agent_message` part: a subagent can receive its task, never the parent's reasoning. Otherwise the request
  is withheld with `encrypted-origin-unverified`.
- An allowed `before_send` with a conversation also returns one `receipt` that replaces the receipts of that
  conversation the request presented. It carries every unexpired proof those receipts held, with its expiry, so a
  retried or divergent history keeps its proof. It renews for one hour the values the request carried and the proven
  handover values its replayed function calls carry, so a parent that keeps talking does not strand a pending
  subagent's task. A long conversation therefore presents a constant number of receipts, and a value stays admissible
  for one hour after its last verified use.
- A receipt is bound to its target profile's `endpoint` and `protocol`. Changing either for that profile voids the
  receipts already issued, so a value is never admitted to a destination other than the one that returned it.
- One receipt holds at most 1,024 values. A check whose values do not fit is withheld with
  `encrypted-receipt-capacity-exceeded` rather than leaving a later request without proof.
- Encrypted values at any other position, and an `agent_message` part with any other field, stay
  `content_unsupported`.

Admission proves where a value came from, not what it contains: Core has not inspected the plaintext. The PowerContext
gateway keeps and presents receipts per conversation.

The supported rules match configured exact values and optional bounded formats: `email`, `private_key`, and
`api_token`. Both raw JSON and decoded text, tool arguments, and numeric spellings are inspected. Duplicate JSON
keys, opaque image/audio/file carriers, unknown request fields, incomplete output, and unsupported targets cannot
receive complete coverage. These rules are not a general PII classifier, semantic DLP system, or tool authorization.

The bounded `openai-responses` profile requires an explicit boolean `store: false`; omitted, null, or enabled
storage is withheld. Responses otherwise stores responses by default; see the provider's
[data-retention contract](https://developers.openai.com/api/docs/guides/conversation-state#data-retention-for-model-responses).
For built-in Pydantic AI Responses models, set `openai_store: false` in the relevant model settings.
Unexpanded `item_reference` inputs, including references whose `type` is omitted or null, are withheld because
their historical content is unavailable for inspection and window accounting. Pass the complete supported
text/tool input instead of a provider-side item reference. Anthropic documents support only inline plain-text
sources; PDF/base64, URL, Files API references, and documents containing images are withheld. Responses
image-generation tools and opaque `image_generation_call` results are withheld on input and output.

Built-in OpenAI/Anthropic clients and configured embedding/rerank paths install the checks at the final HTTP
transport. Native SDK formatting happens first. Observation modes preserve the provider's existing retry policy;
explicitly budgeted workers still disable SDK retries. Enforcing disables SDK transport retries; output-schema retries
remain bounded by the existing stage and every actual attempt is checked. A mandatory privacy failure propagates
through advisory decision fallbacks and cannot become an empty extraction or a consumed Memory HOLD.
If a Memory write gate encounters that refusal, flush fails before publication and keeps its Source window
pending across restart so it can be retried after the policy or model output is corrected.
Spawned Topic Memory workers inherit the same deployment policy for every generation stage. A refusal leaves
the Source window pending without publishing or acknowledging it.
Custom injected inference objects without a controlled final transport are rejected in enforcing mode.

## Provider usage on failures

Provider-reported input, output, cache-read, cache-write and reasoning counts remain separate from estimates. Missing counts stay unknown. A protected response can consume tokens even when delivery is refused; known usage remains attached to the failure and is reported through the existing scoped usage recorder. Cache-read input can already be included in input tokens and must not be counted twice.

The supported local streaming profile is bounded OpenAI Chat SSE with one choice and explicit `allow_streaming=true`. It buffers the complete stream before release and checks assembled text, tool arguments, `reasoning`, and `reasoning_content`, including content split across events. Tool indices must use unsigned JSON integer spellings; ambiguous spellings such as `-0`, `0.0`, or `0e0` are withheld before release. Byte, timeout, and concurrency limits apply. Other streaming profiles are withheld. Strict buffering delays first content.

Loopback HTTP/SSE and spawned-worker tests verify the owned boundaries with controlled responses. They do not establish external provider compatibility, hidden upstream routing or retention, or model quality.

## Cache settings and cost boundaries

Native cache options remain in the final provider adapter, including existing Pydantic AI model settings.
Explicit TTL/retention values require approval in the trusted target profile. A change to the body, target, or
relevant protocol headers requires another inspection of the complete request. The protected transport checks
every actual attempt after SDK rendering.

Stable input can help preserve a prefix but cannot guarantee cache hits. Provider cache-read/write counters
describe provider accounting; they do not prove physical isolation or lower latency. Protection neither creates
a provider cache nor establishes the provider's retention behavior beyond the inspected protocol.

Measure the complete successful task, including retrieval embedding, generation input/output, auxiliary calls,
warmups, failures, and retries. Missing usage or prices remain unknown. An output-limit parameter is not proof of
a provider's hard billing bound: preserve the actual reported usage even when it exceeds the requested limit.
Report input reduction, total task tokens/cost, quality, and latency separately. Existing Atomic evidence-body
deduplication and recall-query reuse are baseline behavior, not savings introduced by inference protection.
