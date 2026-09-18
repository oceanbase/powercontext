# PowerContext

External memory tools for Dify agents and workflows. Connect to your own PowerContext Server; no hosted account is required.
This plugin is experimental. It is independently installable from the PowerContext Agent strategy plugin.

## Connection

Configure the Server URL, bearer token and a namespace unique to your Dify workspace/deployment. Prefer HTTPS. Private-network
HTTP requires explicit opt-in. An optional existing Scope ID intentionally shares that Scope across all uses of the credential.
Otherwise, provision bindings for the runtime app/user or configured business identity before invocation. Missing bindings
never fall back to the default Scope. Scope selection does not replace Server access control.

## Tools

| Tool | Behavior |
| --- | --- |
| `prepare_context` | Return bounded historical context for a query |
| `capture_event` | Store bounded execution evidence as ContentSource |
| `search_memory` | Search memory in the resolved Scope |
| `remember_memory` | Explicitly write a memory entry |
| `get_memory_entry` | Read a referenced memory entry |
| `revise_memory_entry` | Revise an existing entry |
| `retire_memory_entry` | Retire an entry from active memory |
| `flush_memory` | Ask the configured extraction pipeline to process captured evidence |

All tools accept a JSON `request`. For example, `prepare_context` accepts `{"query":"project preferences","max_bytes":8000}`.
Never pass `scope_id` in the request: the host credential and binding determine it. `memory_context` is a form parameter,
not a model input. When absent, it defaults to the invoking Dify app/user identity when the host supplies both.

Legacy Workflow Agent automatic memory requires the separate **PowerContext Function Calling** strategy and both
`prepare_context` and `capture_event` selected as callback tools. Other tools can be called explicitly from any compatible workflow.

Agent V2 automatic memory requires Dify's native external-memory layer. After installing this tool plugin, configure its two
callbacks under **Advanced settings → External memory** in a Dify build containing that layer. An unmodified Dify release
does not gain native Agent V2 memory through this package alone.

## Data and errors

Capture is evidence ingestion, not immediate memory extraction. Configure the PC pipeline/scheduler or an explicit flush.
Events are redacted and bounded; arbitrary metadata is discarded. Server acceptance is the durable boundary. The plugin has
no local persistent outbox and does not guarantee exactly-once delivery. Errors return stable categories without raw credentials.
See [PRIVACY.md](PRIVACY.md). Retiring memory does not delete its captured source evidence.

The package pins a PowerContext source commit in `requirements.txt`; run a compatible Server. Installation requires access
to that GitHub source dependency. Source, provisioning helper, protocol and acceptance instructions live under
`integrations/dify/` in the PowerContext repository. Marketplace publication and native Dify layer release are separate steps.
