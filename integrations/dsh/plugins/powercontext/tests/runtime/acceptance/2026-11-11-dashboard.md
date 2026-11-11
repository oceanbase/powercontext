# DSH Dashboard embedding acceptance — 2026-11-11

## Build and environment

- Source branch: task 2 rebased onto the open, unmerged #2569 head `a9ae5671b462313494de87ee632089207d837947`. RFC #2528 is merged. The host acceptance archive was built before that rebase, when #2569 was at `7bdbc7b7bb95b5bc1cffb905715ddf47897552fa`; its intervening commit changes only `tests/test_dsh_transport.py`, not the plugin or Server. The test used an isolated worktree and did not edit DSH source.
- DSH Web CLI and mounted Desktop app: official `0.2.2-rc.1` release. Desktop DMG `deepseek-harness-0.2.2-rc.1-mac-arm64.dmg`, SHA-256 `c3a594d8990e96fb440f5bb63d72414c42c3e27b691b8056822a71ee97816028`.
- PowerContext Server: `1.5.2.dev15+g7bdbc7b7b.d20261111`, isolated home and database at `/tmp/dsh-dashboard-task2/pc-data`, bound to `127.0.0.1:18080`.
- Final plugin archive: `powercontext-dsh-0.1.0.tgz`, SHA-256 `48656ad3b0db0836df575aaf10f38bc31d7bc88a122a4414b79fa7bb8a688e11`. It was installed independently into isolated DSH Web and Desktop profiles. Both installed `lib/client.js` files matched the final build at SHA-256 `0974dc190fad3fac38ddc68712b1bb023e5e9b4bfd835b8983731d3041b52948`.
- Browser and Desktop profiles, Server token, and workspaces were temporary test fixtures. The test token was generated locally and was never placed in a Dashboard URL, page script, or log.

## Real host observations

| Surface and configuration | Result |
| --- | --- |
| Web, Dashboard enabled, no token | The final archive opened the existing Dashboard in DSH's right Browser iframe, with the bound `workspace-b` Scope and matching `Dashboard fixture b` heading. Styles and content loaded. Notes navigation and language/theme preferences were also exercised in the host. At a test-instrumented 320 px panel width, the Dashboard button label fit without wrapping. |
| Web, Dashboard enabled, token enforced | The final archive opened the authenticated non-default Scope in the embedded iframe; this Chrome test profile retained a prior Dashboard login cookie. A clean embedded sign-in, invalid token, and Scope-preserving redirect were exercised before the visual-only button change. A wrong Host credential reported Dashboard authentication instead of an unresolved Scope. |
| Web, two selected sessions | A test-only Host fixture created two genuine DSH sessions in separate workspaces, each bound to a distinct PowerContext Scope. Selecting each through the host's persisted current-session entry and opening Dashboard produced the matching `scope` URL and heading. |
| Desktop, Dashboard enabled, no token | The final archive's PowerContext control opened Dashboard in the mounted release app's isolated Browser webview. The non-default `workspace-b` Scope and `Dashboard fixture b` heading rendered; Home and Notes navigation had also passed in the release container. |
| Desktop, Dashboard enabled, token enforced | A fresh isolated webview showed the Dashboard sign-in form. Entering the local test token loaded the non-default `workspace-b` Scope and retained it in the post-login URL, without a token in the URL. Invalid-token rejection was exercised separately while the PowerContext API status remained green. The final archive's visual-only button change also opened the sign-in page in a new dark-theme window. |
| Desktop, two selected sessions | A test-only Host plugin created genuine `session-1` and `session-2` in separate workspaces via `ctx.sessions.create`. Selecting each through DSH's persisted current-session field and clicking the final archive's PowerContext entry loaded different Scopes in the actual Electron Browser webview, with matching `Dashboard fixture a` and `Dashboard fixture b` headings. Selection used test instrumentation rather than clicking two visible conversation rows. |
| No visible conversation and Browser unavailable | In the release Desktop's Plugins main view, the right Browser container was unavailable. The final archive kept diagnostics visible, showed the container failure, and offered **Open in browser**. The count of session rows did not change. |
| Dashboard button appearance | The released Desktop button was enabled but rendered pale gray with white text because the old CSS token was absent. The final archive uses the shipped primary-fill and inverted-label tokens. Actual light-theme computed colors were `rgb(15, 17, 21)`/`rgb(255, 255, 255)`; dark-theme colors were `rgb(249, 250, 251)`/`rgb(53, 54, 56)`. [Light](button-light.png) and [dark](button-dark.png) captures show the resulting control. A test-instrumented 320 px panel kept its label on one line. |
| Desktop presentation and layout | The existing Dashboard switched Chinese/English and light/dark modes inside the webview. The narrowed right panel kept the heading, cards, and navigation usable; DSH disabled its own split button at that width. |
| Dashboard disabled, API healthy | In the real Web and Desktop hosts, the entry reported that Dashboard was disabled. The authenticated Host API status still reported ready; Desktop's visible PowerContext control stayed green after refresh. No Server setting changed automatically. |
| Desktop Browser navigation failure | Navigating the isolated Browser tab to a closed loopback port displayed DSH's `页面加载失败 (-102): ERR_CONNECTION_REFUSED` message. This verifies the release container's failure surface, not a PowerContext Server failure. |
| Custom authentication Provider with Dashboard | A separate real Uvicorn startup process configured both options. It exited with `Dashboard supports only the built-in static Bearer profile` before binding port `18081`; there was no Browser page to load. This is a Server configuration rejection, not a Dashboard embed failure. |

The Dashboard was rendered inside DSH on both surfaces. Opening the same URL in an external browser was not used as an embedding pass criterion.

## Automated checks

- `pnpm test:all`: 531 plugin tests passed on the final source, including disabled Dashboard, no selected session, two Scope selectors, authentication before unresolved Scope, invalid route content, network failure, DSH authorization, and URL/credential rejection.
- `pnpm test:package`: release archive install/load passed.
- `uv run pytest tests/test_dashboard.py tests/test_dashboard_profile_handoff.py -q`: 88 passed. The login regression covers a non-default Scope and preserved language/theme query.
- `uv run pytest tests/test_dsh_transport.py -q` after rebasing onto #2569's new test-only commit: 60 passed, 49 skipped.
- `make check`: passed, including lock check, hooks, type checks, and 61 integration-manifest tests.
- `make docs-test`: passed, including bilingual link validation and static website build.

## Remaining acceptance limits

- The released DSH host selected or restored a conversation even after the isolated profile's persisted current-session value was removed, so a stable window with no active conversation could not be held for a real click. The no-selected-session branch passed the plugin's behavior test. The reachable Plugins main-view case above exercised the external entry without creating a new session.
- Desktop's two-session selection used its persisted current-session field and a renderer reload; it did not click two visible conversation rows. The Scope and rendered page were verified inside the packaged Desktop shell after each selection.
- The final archive's Web token smoke run used an existing Dashboard cookie. Fresh token entry and the Scope-preserving post-login URL were verified in Desktop before the final CSS-only update; the final archive displayed the same login page, but token entry was not repeated after that update.
- No other DSH release, remote/shared host, or production deployment was qualified.
