# README screenshots

Captured on 2026-11-02 from the v1.5.0 release branch. The Dashboard and Desktop use
the same isolated SQLite Server and synthetic **Atlas Commerce · Demo** /
**Atlas 商城 · 演示** project Scopes.

| Screen | English | Chinese | Resolution |
| --- | --- | --- | --- |
| Dashboard memories | [Image](../docs/assets/readme-dashboard-memories.png) | [Image](../docs/assets/readme-dashboard-memories-zh.png) | 1600 × 1084 |
| Dashboard Handoff | [Image](../docs/assets/readme-dashboard-handoff.png) | [Image](../docs/assets/readme-dashboard-handoff-zh.png) | 1600 × 1000 |
| Desktop memories | [Image](../docs/assets/readme-desktop-memories.png) | [Image](../docs/assets/readme-desktop-memories-zh.png) | 1440 × 1100 |

The demonstration follows a checkout reliability task: idempotent retries, payment
confirmation, mobile accessibility, bilingual field parity, and the next acceptance
scenario. Sources, memories, and Handoffs were persisted through public APIs and read
back through exact Artifact revisions. The configured LLM generated nine English
memories and both Handoffs; eight explicit memories per language provide additional
decisions and constraints. Selected memories show revision 2 and retain their source
references. The English Handoff shows revision 2; the Chinese Handoff shows revision 1.

Dashboard images were captured from the actual Chromium-rendered application.
Desktop images were captured from the actual Linux Tauri application using Wry 0.55.1,
WebKitGTK 2.50.6, and a virtual display. Native IPC and HTTP requests use the running
Server. The desktop view is scrolled to the search results and exact-revision reader;
English search returns its limit of ten matches, and Chinese search returns eight.
No UI responses, DOM content, or image pixels were substituted.

Desktop capture uses the disposable `desktop-ci-fixture` compatibility profile bound
to Server source `112db11e885c38b997da2177e03a4065a7e35dc3` and its built wheel. This is a
development preview capture. Platform distribution qualification is described in
[Desktop validation](VALIDATION.md).

To refresh these images, create isolated demonstration Scopes, save project Sources
and memories, and commit a Handoff. Select the matching language in each application,
open the exact saved revision, and capture at the resolutions above. Use the real
native Desktop connection flow and a tested compatibility profile. Inspect each
image for readability and accidental credentials before replacing the shared assets.
