# README screenshots and recordings

Captured on 2026-11-02 from the v1.5.0 release branch. The Dashboard and Desktop use
the same isolated SQLite Server and synthetic **Atlas Commerce · Demo** /
**Atlas 商城 · 演示** project Scopes.

| Screen | English | Chinese | Resolution |
| --- | --- | --- | --- |
| Dashboard memories | [Image](../docs/assets/readme-dashboard-memories.png) | [Image](../docs/assets/readme-dashboard-memories-zh.png) | 1600 × 1084 |
| Dashboard Handoff | [Image](../docs/assets/readme-dashboard-handoff.png) | [Image](../docs/assets/readme-dashboard-handoff-zh.png) | 1600 × 1000 |
| Desktop memories | [Image](../docs/assets/readme-desktop-memories.png) | [Image](../docs/assets/readme-desktop-memories-zh.png) | 1440 × 1100 |

## Recordings

| Flow | English | Chinese | Resolution | Duration |
| --- | --- | --- | --- | --- |
| Dashboard: search → saved memory revision → Handoff | [GIF](../docs/assets/readme-dashboard-demo.gif) | [GIF](../docs/assets/readme-dashboard-demo-zh.gif) | 1280 × 900 | 19.5 s |
| Native Desktop: select Scope → search → exact revision | [GIF](../docs/assets/readme-desktop-demo.gif) | [GIF](../docs/assets/readme-desktop-demo-zh.gif) | 1280 × 978 | 19 s |

The GIFs loop at 10 frames per second. Dashboard recordings use Playwright's browser
video capture; Desktop recordings capture the native application on its X11 display.
Both follow real clicks and keyboard input against the same Server. Recordings were
resized and palette-encoded at normal playback speed, with idle lead-in and trailing
time trimmed where needed. Static images remain linked below each README animation
for reading individual screens.

## Capture source

The demonstration follows a checkout reliability task: idempotent retries, payment
confirmation, mobile accessibility, bilingual field parity, and the next acceptance
scenario. Sources, memories, and Handoffs were persisted through public APIs and read
back through exact Artifact revisions. The configured LLM extracted memories and
prepared both Handoff drafts; reviewed drafts were committed. Explicit memories
provide additional decisions and constraints in each language. Selected memories
show revision 2 and retain their source references. The English Handoff shows
revision 2; the Chinese Handoff shows revision 1.

Dashboard images were captured from the actual Chromium-rendered application.
Desktop images were captured from the actual Linux Tauri application using Wry 0.55.1,
WebKitGTK 2.50.6, and a virtual display. Native IPC and HTTP requests use the running
Server. The desktop view is scrolled to the search results and exact-revision reader.
Search displays up to ten matching memories.
No UI responses, DOM content, or image pixels were substituted.

Desktop capture uses the disposable `desktop-ci-fixture` compatibility profile bound
to Server source `112db11e885c38b997da2177e03a4065a7e35dc3` and its built wheel. This is a
development preview capture. Platform distribution qualification is described in
[Desktop validation](VALIDATION.md).

To refresh these captures, create isolated demonstration Scopes, save project Sources
and memories, and commit a Handoff. Select the matching language in each application,
open the exact saved revision, and capture at the resolutions above. For recordings,
leave the selected memory and Handoff readable for several seconds. Use the real
native Desktop connection flow and a tested compatibility profile. Inspect each
image and recording for readability and accidental credentials before replacing the
shared assets.
