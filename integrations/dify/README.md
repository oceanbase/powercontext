# PowerContext for Dify

Experimental integration with two independently installable Dify plugins:

| Package | Purpose | Host requirement |
| --- | --- | --- |
| `powercontext/` | Eight HTTP-backed memory tools, including automatic recall/capture callbacks | Dify plugin support and SDK 0.10.2 |
| `powercontext_agent/` | Function Calling strategy with automatic recall, event capture and bounded context | Legacy Workflow Agent; select both PC callbacks in Tools |
| Agent V2 native memory | Uses the tool package through the daemon; retains Dify history and compaction | Dify source with `feat/agent-v2-mem`; this is not available in an unmodified release |

The two packages are separate because Dify's manifest validator disallows combining tool and agent-strategy providers.
No PowerContext SDK is added to Dify core. The Agent V2 layer accepts any two plugin tools implementing the protocol below.

## Development

For an actual local CE + daemon + PC deployment before publishing, see [local setup](local/README.md) and
[deployed validation status](local/VALIDATION.md). The local scripts keep database volumes, credentials and ports isolated.

```bash
uv sync --locked --project integrations/dify
make dify-test
```

`integrations/dify/uv.lock` pins the development environment. The tool plugin's `requirements.txt` pins PowerContext to an
immutable source commit; use a matching Server. Deployment needs GitHub access to install this source dependency. Move to a
published, compatible PowerContext release before submitting a release that requires PyPI-only dependency installation.
After changing the HTTP contract, regenerate the model-visible tool parameter schemas with
`uv run --project integrations/dify python integrations/dify/generate_tool_schemas.py`.

Build with the official [Dify plugin CLI](https://github.com/langgenius/dify-plugin-daemon/releases):

```bash
dify plugin package integrations/dify/powercontext
dify plugin package integrations/dify/powercontext_agent
```

The CLI's executable name depends on how it was installed. Upload each `.difypkg` through Dify's plugin management page.
No credentials belong in a package. `README.md`, `PRIVACY.md`, `LICENSE`, icons and declarations ship with each package.
Source packages can be prepared with `python integrations/dify/package_sources.py`; these archives are for code review,
not a substitute for the official `.difypkg` validation.

## Configure the PowerContext connection

Create a credential for the PowerContext tool provider:

- `base_url`: reachable Server URL. Prefer HTTPS. `127.0.0.1` inside a plugin container means that container.
- `token`: the Server bearer token, stored as a Dify secret credential.
- `namespace`: stable deployment/workspace identifier. Use different values for separate deployments/workspaces.
- `scope_id`: optional existing Scope shared by every invocation using this credential. Leave empty for isolated bindings.
- `allow_insecure_http`: explicit opt-in for a trusted private network; false by default.

By default, the host supplies the invoking app and user. The binding key is:

```text
integration = "dify"
kind = "user" or "business"
external_id = sha256(UTF-8 JSON([namespace, app_id, subject_kind, subject_id], compact separators, no ASCII escaping))
```

The user ID is Dify's runtime user ID, which may differ from an application's public username. Business identity is a
configured literal shared within the app. Bind each identity before use; the plugin resolves with `allow_default=false`.
A missing binding degrades memory instead of silently selecting the default Scope.
For Workflow Service API calls, `sys.user_id` is the caller's external identifier; plugins receive the internal `EndUser.id`.
An administrator can obtain it from `created_by_end_user.id` in the Console API's workflow-run detail response.

Provision an identity using an admin credential; do not expose this operation to the model:

```bash
export POWERCONTEXT_BASE_URL=https://memory.example.com
export POWERCONTEXT_TOKEN=your-admin-token
uv run --project integrations/dify python integrations/dify/provision_scope.py \
  --namespace production-workspace --app-id dify-app-id --subject-kind user --subject-id runtime-user-id
```

Pass `--scope-id` to bind to an existing Scope. Credentials with an explicit `scope_id` intentionally bypass identity binding.
Bindings and Scope IDs select data; they are not authorization grants. Configure PC access control or use separate Server
credentials/instances for isolation across trust boundaries. A shared admin token is not multi-user authorization.

## Legacy Workflow Agent

1. Install both plugin packages and configure the PowerContext provider credential.
2. Select **PowerContext Function Calling** as the Agent strategy.
3. Select `prepare_context`, `capture_event`, and the desired business tools in Tools. Each callback needs its PC credential.
4. Set the model, query and instruction. Set `context_window` if model metadata does not declare it.
5. Choose user identity, or configure a business identifier. Provision the corresponding binding.

The strategy removes the two callbacks from the model's tools. It recalls once at the start and captures visible user/model
text and tool calls/results automatically. Configured tool inputs override model arguments. It reserves model output space,
counts serialized UTF-8 text/schema bytes conservatively, clears older tool results and summarizes complete older interactions.
The first user request is retained. If the current task cannot fit, it fails before sending an oversized model request.
This strategy is intended for text conversations; it does not estimate provider-specific image/audio token costs.

## Agent V2 and Agent apps

Use Dify source with the native memory changes, including API, Web and `dify-agent` runtime. The implementation was developed
against Dify `06f58809` (`1.17.0` source version). This version number alone does not imply released external-memory support.

In the shared Agent configuration, add and authorize the memory tools under **Tools**, then open **Advanced settings →
External memory**. Select recall/capture tools, user or business identity, byte budgets and whether to capture execution
history. Configuration is also available in `AgentSoulConfig.memory.external`; both entrypoints use the same contract.

The API stores tool and credential references. At run time it resolves credentials within the existing tenant tool owner.
Callbacks run outside model tool selection. Recall is a transient instruction block included in Dify's existing compaction
budget. It is removed when Dify persists history. Memory does not replace local conversation history or resume snapshots.

## Callback protocol

Both tools accept two objects. `memory_context` is a host-controlled form parameter; `request` contains the operation:

```json
{
  "memory_context": {"app_id": "app-id", "subject_kind": "user", "subject_id": "runtime-user-id"},
  "request": {"query": "What happened previously?", "max_bytes": 8000}
}
```

Recall emits one Dify JSON message with `status: "ready"`, `content` and exact UTF-8 `content_bytes`, or
`status: "empty"`, `content: null`, `content_bytes: 0`. PowerContext also returns its versioned `schema` field.

Capture accepts `event_id`, `event`, `sequence`, `payload`, `metadata` and `max_bytes`. Supported events are `user_prompt`,
`model_response`, `tool_call`, `tool_result`, and `run_end`. A successful capture emits `status: "accepted"`; PC adds a Source
reference and position. Hosts treat malformed acknowledgements as degraded capture. Error messages contain a stable error
category, never raw exceptions or credentials.

## Durability, privacy and operation

Capture writes durable ContentSource evidence. It does not itself extract long-term Memory. Configure the PC extraction
pipeline and scheduler, or invoke `flush_memory` at an explicit checkpoint. Recall depends on extraction completing; there
is no automatic per-event flush. `remember_memory` is an explicit direct memory-write tool.

Events are bounded (8,192 bytes by default). Sensitive field names and the PC connection token are redacted before storage.
Agent V2 additionally redacts and bounds payloads before the daemon boundary. Arbitrary metadata is discarded by the bridge.
Hidden reasoning parts, known reasoning tags, binary attachments and credentials are not intentionally captured. Free text
can still contain personal data or secrets not recognizable as credential fields; choose capture settings accordingly.

Each callback has a 10-second deadline; after a callback fails it is skipped for the rest of that run. The next run retries.
Recall defaults to 8,000 bytes and is further limited to one quarter of a known input budget. Dify logs degradation; the legacy
strategy also emits an Agent log. Callback configuration resolution failures are logged without leaking credential-bearing
exceptions. PC tool errors distinguish authentication, forbidden, missing binding/resource, conflict and availability issues.

Run/event IDs yield deterministic source identities. This gives stable retry identifiers, not an exactly-once delivery
promise. Capture is best effort before transmission: process termination or cancellation can lose an unacknowledged event.
There is no persistent client outbox. Successful HTTP acceptance is the durable boundary. Paused V2 runs retain native
history; resumed runs use a new run ID and do not recapture a nonexistent new user prompt.

## Validation and publication

Automated coverage includes real SDK registration, SDK message types, daemon HTTP serialization, Pydantic AI execution,
local-history persistence, tenant identity, failures, capture bounds, and a real PC HTTP app + SQLite extraction/recall chain.
These do not substitute for a deployed CE + plugin-daemon + real model acceptance run. See `ACCEPTANCE.md` for that checklist.

Marketplace submission requires the maintainer's publisher account, package validation, review and release approval. Native
Agent V2 support additionally depends on upstreaming/releasing the Dify layer. Do not advertise V2 automatic memory as a
plugin-only capability of an unmodified Dify release.
