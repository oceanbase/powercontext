# Knowledge-base Dashboard

React and TypeScript sources for the knowledge-base plugin's sandboxed Dashboard page. The Server serves the
checked-in assets in `plugins/knowledge-base/ui`; production does not require Node or an extra frontend server.

From the repository root, with Node 22:

```sh
npm ci --prefix frontend/knowledge-base
npm run build --prefix frontend/knowledge-base
```

Commit the source, lockfile and rebuilt assets together. The knowledge-base CI job rebuilds and checks for drift.
Keep `node_modules` outside the plugin directory: the framework hashes the complete plugin directory when loading it.
The build emits a classic script for the opaque-origin iframe, keeps third-party license notices, and checks the
framework's 5 MiB per-asset limit.

The host Dashboard supplies authentication and its navigation shell. All requests go through the existing
`powercontextPluginClient` bridge. Document edits and forget actions use the framework Artifact endpoints with
revision preconditions. Chunk content is loaded independently of the document list and directory, using an exact
Scope, document ID and revision. Markdown preview disables raw HTML and remote images; relative links remain text.

The UI provides a document table, processing receipts, upload and Server-directory import, a chunk reader with
historical revisions and synchronous editing, retrieval testing, and chunking settings. Browser regressions live in
`tests/e2e/test_knowledge_base_dashboard.py` and run against a real Server with deterministic test embeddings.
