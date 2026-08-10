# Session replay harness

This harness runs committed scenarios through Bub and a real PowerContext Server. Every scenario uses one isolated
PowerContext scope. Every step uses a new Bub session, so durable state crosses the boundary only through PowerContext.

It supports three evidence modes:

- `acceptance` calls real Bub tools without a model. It blocks on process completion, Memory extraction, and prepared
  context recall.
- `live` sends the same inputs to a real Bub model. It adds agent and model spans, and records answer quality as a
  diagnostic score or judge label.
- `rescore` reads `replay.json` and runs the same Pydantic Evals oracle without rerunning Bub or PowerContext.

Each run writes `replay.json`, `eval-report.json`, and `report.md`. The replay is self-contained and contains the
scenario, public Memory snapshots, prepared context, outputs, and Pydantic Evals-compatible spans. It never records
API keys, authorization headers, or database URLs.

## Run against an existing Server

```bash
export POWERCONTEXT_BUB_BASE_URL=http://127.0.0.1:8000
export POWERCONTEXT_E2E_DATABASE=sqlite
make harness-acceptance
```

Run one real-provider scenario:

```bash
export BUB_MODEL=openai:model-name
export BUB_API_KEY=replace-me
export BUB_API_BASE=https://provider.example/v1
make harness-live
```

`BUB_MODEL` is reused by PowerContext and the Pydantic Evals judge for OpenAI and DeepSeek providers.
`POWERCONTEXT_E2E_JUDGE_MODEL` selects an explicit judge. Server generation settings take precedence, and embedding
always uses its own PowerContext profile.

## Run the complete environment

Docker Compose starts PowerContext and runs the same committed scenarios used in CI. SQLite is the default:

```bash
make harness-compose-acceptance
```

Run the same acceptance set against OceanBase:

```bash
POWERCONTEXT_E2E_DATABASE=oceanbase make harness-compose-acceptance
```

`make harness-compose-live` uses the provider variables above. Evidence is written below `.powercontext-e2e/bub/`;
set `POWERCONTEXT_E2E_OUTPUT` to keep it elsewhere. `make harness-compose-down` removes containers and database volumes.
