# Knowledge-base plugin

One installable business plugin owns document ingestion, Markdown/image OCR processing, chunks and retrieval.
A knowledge base is a core Scope; spaces and their membership/defaults live only in plugin storage.

- [English guide](../../docs/en/docs/workflows/knowledge-base.md)
- [中文指南](../../docs/zh/docs/workflows/knowledge-base.md)
- [Design RFC #2570](https://github.com/oceanbase/powercontext/pull/2570)

Copy this **whole directory**, keeping its name `knowledge-base`, into the configured Server plugin directory.
Install `powercontext[server,knowledge-base]` into the same environment used by Server and Workers. The plugin is
copy-installed rather than automatically enabled by installing the Python extra.

Requires the framework contracts from [#2659](https://github.com/oceanbase/powercontext/pull/2659) and
[#2660](https://github.com/oceanbase/powercontext/pull/2660). Merge those prerequisites before this plugin; the plugin does not
bundle framework patches. It uses `ctx.storage`, the framework writer, and the Dashboard `client.artifacts` bridge.
