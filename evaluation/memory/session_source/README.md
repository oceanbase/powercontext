# Session Source acceptance evidence

[Issue #2869](https://github.com/oceanbase/powercontext/issues/2869) and
[RFC #2845](https://github.com/oceanbase/powercontext/pull/2845) describe role-aware capture using the existing
Content Source API. The cases and provider outputs here contain synthetic conversations.

## Serialized size

The baseline server accepted a 200,000-character serialized envelope and rejected 200,001 with 422.
With compact JSON, 100,000 literal quotation marks required 200,093 serialized characters and returned 422.
At a 200,000-character envelope, the baseline accepted 599,814 UTF-8 bytes of CJK input and 799,721 bytes
of emoji input. Both matched the original strings after the SQLite server reopened.

The weighted estimator used by Atomic Memory was applied to a single structured evidence item with the
standard Coding instructions, session role guidance and empty caller metadata. Its estimates include the instructions and evidence
serialization. Production defaults allow 64,000 input tokens per extraction. Deployment-specific budgets,
a longer custom prompt or a smaller configured budget can make even one Session Source exceed the limit.
An over-budget singleton holds the journal cursor until the processing configuration can accommodate it.

| Serialized envelope characters | ASCII estimated tokens | CJK or emoji estimated tokens |
| ---: | ---: | ---: |
| 32,000 | 9,464 | 33,394 |
| 48,000 | 13,464 | 49,394 |
| 64,000 | 17,464 | 65,394 |
| 200,000 | 51,464 | 201,394 |

The 48,000-character limit leaves roughly 14,600 estimated tokens for fixed and variable overhead in the
measured CJK and emoji scenarios. The limit includes escaping, session ID and structural delimiters. The
512-message cap allows small historical turns to share one envelope while bounding the number of parsed
message objects. Content plus compact JSON metadata is capped at 56,000 characters. The weighted estimate
for a 48,000-character CJK envelope with approximately 8,000 CJK metadata characters was 57,452 tokens
with the built-in Coding instructions and native evidence serialization. The HTTP boundary tests cover a full 48,000-character CJK envelope and a one-character
increase caused by JSON escaping.

## Native persistence

The CI-pinned OceanBase 4.3.5.6 CE image on Linux arm64 was initialized with the Server's migration CLI.
The API accepted a Unicode conversation, rejected a conflicting batch without writing its other item, and
retained the original JSON, Source reference and position through Server restart, list/detail reads and exact
replay. A recording extractor received the original structured roles and message text from that Source.

## Codex final-response availability

Codex CLI 0.167.2 on Linux x86_64 and 0.171.0-alpha.13.2 on macOS supplied `last_assistant_message` in its real `Stop` hook. The hook probe
submitted one immutable Source after a direct turn and after a real CLI tool continuation. The tool result was
absent from both captured messages. A capture rejected with HTTP 422 still produced the same final CLI message.
Interrupting the CLI during a model request produced no session capture. The model endpoint was a local
Responses fixture and the capture endpoint was a real loopback PowerContext Server with SQLite. This probe
qualifies the CLI event and API contract. Automatic capture in the shipped plugin is a separate integration.

```sh
CODEX_CLI_BIN="$(command -v codex)" uv run --locked pytest -q \
  tests/e2e/test_codex_host_service_chain.py -k stop_supplies
```

## Paired extraction

[Cases](cases.json) label eight conversations covering short confirmations, corrections, forged role labels,
unverified completions, repeated evidence, speaker preferences and Assistant-only input. Both arms use the same
real DeepSeek Flash model, structured Atomic Memory extraction adapter, Coding instructions, identifiers and
sampling settings. The User-only arm filters Assistant messages before creating the extraction input. The
Assistant-only conversation has no User-only model call. Labels are absent from model input.

| Rules | Input | Labeled claims recovered | Unsupported facts | Duplicate candidates | Requests | Input tokens | Output tokens |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Initial | User only | 7/8 | 0 | 0 | 7 | 9,586 | 299 |
| Initial | User and Assistant | 8/8 | 1 | 0 | 8 | 11,113 | 338 |
| Corroboration required | User only | 7/8 | 0 | 0 | 7 | 10,223 | 298 |
| Corroboration required | User and Assistant | 8/8 | 0 | 0 | 8 | 11,841 | 303 |

Scoring is a manual comparison of the candidate texts to the labeled propositions. A candidate can cover
multiple claims. A duplicate repeats the same proposition within one result. On both runs the extra context
recovered Lark's release-tag convention after the user confirmed the proposal with a short reply. Under the
initial rules, the paired arm also asserted an Assistant-only claim about an archive size limit. After adding
explicit corroboration and omission rules, the paired arm omitted that candidate and retained the user's
archive policy. The Plover candidate in the User-only arm changed language from English to Chinese while
retaining the correct attribution and policy.

For the seven cases in which both arms called the model, adding Assistant context under the final rules
used 168 additional input tokens (1.6%). The additional Assistant-only call used 1,450 input tokens. Totals use
provider-reported token usage as the cost measure and do not represent billed currency. Each arm was run
once per case with one model alias. These results cover extraction on the supplied cases and leave
cross-Source deduplication, provider variance and production extractor accuracy unmeasured.

One additional end-to-end smoke test used the real Server with DeepSeek Flash. A single Session Source
entered through the capture API, advanced the Atomic Memory cursor from zero to one and published Lark's
user-accepted release-tag convention. The Assistant's separate claim about an 80 GB limit did not enter
the resulting Memory.

[Initial outputs](deepseek-flash-initial.json) · [Corroboration outputs](deepseek-flash-20261127.json)

```sh
PYDANTIC_AI_NO_BANNER=1 uv run --locked python evaluation/memory/session_source/run.py \
  --env-file /path/to/generation.env --output /tmp/session-source-results.json
```

Use a local environment file containing `DEEPSEEK_API_KEY` and
`POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL=deepseek:deepseek-flash`. The runner uses at most 15 requests
and 512 output tokens per request, with provider retries and model continuations disabled.
