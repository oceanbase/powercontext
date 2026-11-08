---
title: Import existing Codex Memory
description: Import derived files under .codex/memories as traceable, repeatable Source snapshots through a Source plugin.
---

# Import existing Codex Memory

This capability reads already-generated text under the Codex home's `memories/` directory, addressing
[#1384](https://github.com/oceanbase/powercontext/issues/1384). Historical user prompts use the separate
[Codex session history capability](source-plugins.md#codex-session-history).

Each imported file becomes a `codex-memory-snapshot` Source containing a namespace, relative path, SHA-256 of the original
file bytes, redacted text and a redaction flag. Its TextEvidence projection explicitly records `origin=codex-memory` and
`evidence_kind=derived_memory`. This retains derived material without directly creating Memory, Skills, Handoffs or reviewed
Artifacts, and without executing instructions or scripts in the files. References in the text remain text; the importer does
not invent citations to original sessions. The file hash identifies the bytes read, not factual accuracy or review status.
Remote Source evidence declarations still default to `untrusted` / `unknown`.

## Prepare

The plugin lives in the checkout at `integrations/codex/memory_source_plugin/`. On the machine holding the files, prepare the
repository Python environment:

```sh
cd /path/to/powercontext
uv sync
```

Prepare a running Server, an existing destination Scope, and separate administrator and Scope contributor credential files.
Each file contains only its bearer token; on Unix, set permissions to `600` and do not use symlinks. An administrator registers
the Definition. The importing identity needs `scope.read` and `scope.contribute`, without `server.admin`. The Server does not
automatically gain access to the client's filesystem.

Choose a stable `namespace`, such as `personal-codex-memory`, for this collection. Use different namespaces for different devices
or collections, and keep the same namespace for repeated imports of one collection. Do not put credentials or sensitive absolute
paths in the namespace.

## One-shot import on macOS or Linux

Preview locally first. This command does not contact the Server or write Sources; it outputs relative paths, prospective Source
IDs, failure reasons and empty files:

```sh
uv run python -m integrations.codex.memory_source_plugin preview \
  --codex-home "$HOME/.codex" --namespace personal-codex-memory
```

Without `--codex-home`, the command uses `CODEX_HOME`, falling back to `~/.codex`. `--path` defaults to `.`, selecting all of
`memories/`. For example, `--path MEMORY.md` selects one file, and `--path rollout_summaries` selects that subtree.

An administrator registers the immutable Source Definition. This only registers its data contract; it does not upload plugin
code or load it into the Server:

```sh
uv run python -m integrations.codex.memory_source_plugin register \
  --server-url http://127.0.0.1:8000 --credential-file /path/to/admin.token
```

Then import using the Scope contributor credential:

```sh
uv run python -m integrations.codex.memory_source_plugin import \
  --codex-home "$HOME/.codex" --namespace personal-codex-memory \
  --scope-id SCOPE_ID --binding-id personal-codex-memory \
  --server-url http://127.0.0.1:8000 --credential-file /path/to/contributor.token
```

Replace `SCOPE_ID` with the actual ID; import never infers a default Scope. `binding-id` is the stable Server checkpoint identity.
Keep it unchanged when importing the same collection and selection; use distinct bindings for different import jobs. Prefer HTTPS
for remote connections. See [Connect to a remote Server](../operate/connect-remote-server.md) for transport policy and explicit
HTTP options. Connection settings can also come from `POWERCONTEXT_CLIENT_SERVER_URL` and `POWERCONTEXT_CLIENT_API_TOKEN`;
do not pass tokens as command arguments.

On success, JSON `status` is `complete`, and `items[].source_ref` contains actual submission receipts. Repeated imports can have
no new items. Preview or import failures exit with code `1`; argument syntax errors use `2`. Preview does not print file bodies;
import results expose neither file bodies nor checkpoints.

## Continuous collection with a Linux Runtime

For ongoing tracking, follow the [Source plugin deployment workflow](source-plugins.md#load-collect-on-a-running-server).
Prepare trusted code and credentials on the execution machine, then edit the plugin directory's `powercontext.yml`:

```yaml
id: codex-memory
plugin: .
scope_ids: [SCOPE_ID]
config:
  codex_home: /absolute/path/to/codex-home
  namespace: personal-codex-memory
  poll_interval: 30
```

`codex_home` must be an absolute path on the execution machine. After preparing the environment file as described in the deployment
workflow, load the plugin:

```sh
powercontext source load \
  --config /path/to/powercontext/integrations/codex/memory_source_plugin/powercontext.yml \
  --env-file /path/to/pc.env
```

The manifest has no initial targets; loading alone reads no memories. Explicitly add a target through the Server's public MCP:

```javascript
pc_source_capabilities({"scope_id":"SCOPE_ID"})
pc_source_instances({"scope_id":"SCOPE_ID"})
pc_source_add({
  "scope_id":"SCOPE_ID","instance_id":"codex-memory",
  "capability":"codex.memory-history","capability_version":"1",
  "input":{"path":"."}
})
```

Retain the returned `target_id`. Inspect, disable and resume the target:

```javascript
pc_source_status({"scope_id":"SCOPE_ID","target_id":"TARGET_ID","view":"control"})
pc_source_target_set_enabled({"scope_id":"SCOPE_ID","target_id":"TARGET_ID","enabled":false})
pc_source_target_set_enabled({"scope_id":"SCOPE_ID","target_id":"TARGET_ID","enabled":true})
```

`tracking=stopped` means the Runtime has acknowledged target shutdown. Disabling retains existing Sources; re-enabling a target
does not implicitly enable a disabled instance. Managed deployment requires Linux. One-shot import supports macOS and Linux.
Local Windows acquisition is not supported; explicitly transferred material can be imported on a Unix execution machine.

## Inspect saved evidence and use it downstream

Read the exact receipt's `source_type` and `source_id` through
`GET /v1/scopes/{scope_id}/sources/{source_type}/{source_id}`. Browse saved snapshots through
`GET /v1/scopes/{scope_id}/sources`. Management target status retains only the latest submission report, not the full Source list.

To process this material into recallable Memory, configure models and processing as described in
[Enable extraction and vector search](../get-started/configure-models.md), then inspect the generated Memory and its Source references.
An import receipt proves Source acceptance, not successful Memory generation, review or recall.

## Files, versions and recovery

- Recursively select UTF-8 `.md`, `.markdown` and `.txt` files, allowing a BOM. Examples include `MEMORY.md`, `memory_summary.md`,
  and text under `raw_memories/`, `rollout_summaries/` and `skills/`. Preserve file text apart from BOM decoding and known-credential
  redaction. JSON, databases, scripts and other formats in a directory are not imported; explicitly selecting an unsupported file
  fails with `unsupported_format`.
- Reject absolute selections, `..` traversal, symlinks, binary text, invalid UTF-8 and oversized files. Whitespace-only files are
  reported as skipped. Known credentials from the environment and the selected Codex home's `auth.json` are redacted; this is not
  a general sensitive-information detector.
- Identical namespace, relative path and file snapshot reuse a Source. Changes create a new Source while previous snapshots remain
  exactly readable. Reverting to an identical historical snapshot reuses that Source rather than recording another event.
  Deleting an original file never deletes historical Sources.
- Checkpoints are isolated by destination Server, binding/instance and Scope, and advance with CAS only after a complete successful
  batch. Other files may already be saved when one fails, while the checkpoint remains unchanged; fixing the file and retrying
  deduplicates accepted Sources. Missing checkpoints, process interruption or switching to a managed instance allow safe resubmission.
  Concurrent checkpoint conflicts do not overwrite progress; retry serially.
- Each poll fully scans and rereads the selected tree; this is not an append-byte incremental reader. Defaults are 256 KiB per file,
  a 16 MiB file-read budget per target per poll, 1,000 text files and 10,000 directory entries. Failed reads conservatively consume
  budget too; probing file growth reads at most one extra byte. File or entry count limits reject partial directory enumeration.
  Checkpoints retain only the latest successful snapshot per path, not every historical version. Adjust limits through configuration
  or the one-shot command's `--max-file-bytes`, `--max-total-bytes`, `--max-files` and `--max-entries` options.
