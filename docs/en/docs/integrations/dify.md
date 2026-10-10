---
title: Dify
description: Experimental Dify tools for a separate PowerContext Server.
---

# Dify

The [PowerContext tool plugin](https://github.com/oceanbase/powercontext/tree/master/integrations/dify) provides 19 HTTP tools for Memory, bounded context, explicit Source capture, Handoff, Experience/Skill generation and exact reads, and read-only candidate inspection. It is experimental source, with Marketplace publication tracked in [#1837](https://github.com/oceanbase/powercontext/issues/1837).

Deploy PowerContext Server separately. Build the plugin with the official Dify CLI, install the package in your workspace and configure a daemon-reachable Server URL, bearer token, and exactly one existing Scope ID or pre-provisioned `dify/configured-scope` binding key. Each call disables Default-Scope fallback. Credential validation resolves and reads the protected Scope; Server policies determine each operation's permissions.

One credential is a shared fixed application/team Scope. It does not infer per-user or per-conversation isolation from Dify runtime fields. Nested references remain unchanged and the Server validates their access. Use appropriately restricted credentials/policies or separate instances for distinct authorization boundaries.

The plugin does not automatically recall/capture, approve candidates, manage Scopes, install external Skills, or provide Agent V2 memory callbacks. Optional Agent instructions guide tool selection but do not guarantee recall before every answer. A Workflow/Chatflow must execute `pc_prepare_context` before the model and explicitly connect `result.content` to the model input for that guarantee. Reusable templates follow acceptance of the plugin into `langgenius/dify-plugins`.

Calls emit text, JSON and six named outputs: `ok`, `operation`, `status`, `data`, `error`, `result`. Successful data preserves the complete HTTP response; `empty` is a successful empty read. The Workflow variable picker exposes operation-specific fields under `result`, including context content and exact references. Branch on `ok` before using `result`; it is `{}` on error/unknown, while `data` retains any partial recovery receipt. Write timeouts or invalid receipts return `unknown`; inspect Server state before recovery because the plugin does not retry. Remove secrets before explicit Source capture. Historical text is untrusted evidence.

Object, array and nullable tool inputs are strings containing one JSON value. Optional inputs may be omitted; explicit null uses the text `null`, and nullable strings include JSON quotes (`"理由"`). The default Dify 1.17.1 daemon `0.6.10-local` retains these valid string declarations and their decoded-schema descriptions; the plugin decodes once and validates the HTTP contract. Empty references and malformed JSON are rejected. Workflow outputs retain native values: serialize a complete structured output once in a Code node before supplying it to the next tool. [Examples](https://github.com/oceanbase/powercontext/blob/master/integrations/dify/plugin/README.md) cover Handoff chaining.

Workflow selectors can traverse `result.candidate.candidate_id` and `result.draft.objective` while complete responses retain null values. Check the operation's status before reading a nullable candidate or draft. Source replay tests cover official daemon serialization, Dify parameter models/casting and model schemas; installation, dispatch and actual Agent/Workflow runs still require deployment acceptance.

`data` and `error` are nullable envelope values without expandable child schemas. Use `result.*` selectors for individual response fields in downstream nodes.

See the source [README](https://github.com/oceanbase/powercontext/blob/master/integrations/dify/README.md), [tool catalog](https://github.com/oceanbase/powercontext/blob/master/integrations/dify/tool-coverage.md), [Scope mapping](https://github.com/oceanbase/powercontext/blob/master/integrations/dify/scope-mapping.md), [privacy](https://github.com/oceanbase/powercontext/blob/master/integrations/dify/plugin/PRIVACY.md) and [acceptance evidence](https://github.com/oceanbase/powercontext/blob/master/integrations/dify/ACCEPTANCE.md).
