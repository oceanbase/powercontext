# Scope mapping

A provider credential selects one fixed Scope boundary. Set either an existing `scope_id` or a pre-provisioned `binding_external_id`. Credentials with neither or both are rejected before networking.

Every invocation calls `resolve_scope_binding` with `allow_default=false`. Direct Scope selection sends `explicit_scope_id`; binding selection sends:

```json
{"allow_default": false, "binding_keys": [{"integration": "dify", "kind": "configured-scope", "external_id": "deployment:application"}]}
```

An administrator provisions that key through PowerContext's supported Scope binding operations. Include a deployment/application namespace to avoid accidental collisions. Do not use a workspace path, username, hash or business identifier as a Scope ID. A missing binding is an error and never resolves to Default.

Credential validation uses the same resolution followed by protected `get_scope`. It does not write data or use an unauthenticated health endpoint as evidence of access. Save-time read validation cannot promise write permission; handle actual Server 403 responses.

The resolved Scope is injected into every selected operation. Top-level `scope_id`, URL, token, binding, expected Memory revision, context assembly and byte budget are not model parameters. Nested citations and prepared Handoff objects keep their original identities. The Server owns reference and cross-Scope access checks; a matching top-level Scope is not an authorization proof.

Dify user/session identifiers are not used for automatic routing. One credential shares its Scope among its authorized applications/users. A shared administrator token represents one trust domain, even when Scope A/B searches are separate. Production resource isolation requires appropriately restricted Server credentials/policies or separate instances. Dynamic trusted identity routing needs a separate design and host/Server acceptance.

Requests use synchronous per-call HTTP clients in the gevent-patched SDK, with no shared mutable credential/Scope state and no `asyncio.run()`. Socket timeouts are not overall operation deadlines; align the outer Dify node/daemon limits. Writes never retry automatically.
