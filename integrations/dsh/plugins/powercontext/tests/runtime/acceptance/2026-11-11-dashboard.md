# DSH Dashboard embedding acceptance — 2026-11-11

## Build and environment

- Source branch: task 2 based on the open, unmerged #2569 head `7bdbc7b7bb95b5bc1cffb905715ddf47897552fa`. RFC #2528 is merged. The test used an isolated worktree and did not edit DSH source.
- DSH Web CLI and mounted Desktop app: official `0.2.2-rc.1` release. Desktop DMG `deepseek-harness-0.2.2-rc.1-mac-arm64.dmg`, SHA-256 `c3a594d8990e96fb440f5bb63d72414c42c3e27b691b8056822a71ee97816028`.
- PowerContext Server: `1.5.2.dev15+g7bdbc7b7b.d20261111`, isolated home and database at `/tmp/dsh-dashboard-task2/pc-data`, bound to `127.0.0.1:18080`.
- Final plugin archive: `powercontext-dsh-0.1.0.tgz`, SHA-256 `dcede0cd4ef445dbc90bf87152dd0917ea6ac162259412b4d4790019ea3d415d`. It was installed independently into isolated DSH Web and Desktop profiles. Both installed `lib/index.js` hashes matched the final build. Full interactive checks ran on the immediately preceding archive; the final archive repeated Web no-token and token embedding, Web wrong-credential classification, and Desktop token sign-in on a clean release window. The final source change only reordered Dashboard failure classification when Scope resolution also fails.
- Browser and Desktop profiles, Server token, and workspaces were temporary test fixtures. The test token was generated locally and was never placed in a Dashboard URL, page script, or log.

## Real host observations

| Surface and configuration | Result |
| --- | --- |
| Web, Dashboard enabled, no token | The PowerContext control opened the existing Dashboard in DSH's right Browser iframe. Styles and content loaded; Notes navigation, Chinese/English and light/dark preferences worked. Setting the real DSH panel to 320 px through test instrumentation rendered without horizontal document overflow. The final archive repeated the embedded home load. |
| Web, Dashboard enabled, token enforced | The embedded iframe showed the Dashboard sign-in form. Entering the token in the frame loaded the selected non-default Scope. The preceding archive repeated sign-in and retained `scope` after redirect; the final archive opened the same authenticated Scope in the embedded frame. A wrong Host credential on the final archive reported Dashboard authentication instead of an unresolved Scope. |
| Web, two selected sessions | A test-only Host fixture created two genuine DSH sessions in separate workspaces, each bound to a distinct PowerContext Scope. Selecting each session and opening Dashboard produced the matching `scope` URL and heading. This interactive switch ran before the final archive rebuild; the only later browser-code change tightened validation of the same Scope/URL pair. |
| Desktop, Dashboard enabled, no token | The mounted release app loaded the preceding archive in an isolated Browser webview. Home and Notes opened with the selected non-default Scope. The final archive was installed and the no-token app started, but the computer-control accessibility service stopped exposing that window before a final click-through. |
| Desktop, Dashboard enabled, token enforced | The webview showed sign-in. An invalid token showed the Dashboard's rejection while the PowerContext API status remained green. A valid token opened the final archive's non-default Scope and kept it in the post-login URL. |
| Desktop presentation and layout | The existing Dashboard switched Chinese/English and light/dark modes inside the webview. The narrowed right panel kept the heading, cards, and navigation usable; DSH disabled its own split button at that width. |
| Dashboard disabled, API healthy | In the real Web and Desktop hosts, the entry reported that Dashboard was disabled. The authenticated Host API status still reported ready; Desktop's visible PowerContext control stayed green after refresh. No Server setting changed automatically. |
| Desktop Browser navigation failure | Navigating the isolated Browser tab to a closed loopback port displayed DSH's `页面加载失败 (-102): ERR_CONNECTION_REFUSED` message. This verifies the release container's failure surface, not a PowerContext Server failure. |

The Dashboard was rendered inside DSH on both surfaces. Opening the same URL in an external browser was not used as an embedding pass criterion.

## Automated checks

- `pnpm test:all`: 531 plugin tests passed, including disabled Dashboard, no selected session, two Scope selectors, authentication before unresolved Scope, invalid route content, network failure, DSH authorization, and URL/credential rejection.
- `pnpm test:package`: release archive install/load passed.
- `uv run pytest tests/test_dashboard.py tests/test_dashboard_profile_handoff.py -q`: 88 passed. The login regression covers a non-default Scope and preserved language/theme query.
- `make check`: passed, including lock check, hooks, type checks, and 61 integration-manifest tests.
- `make docs-test`: passed, including bilingual link validation and static website build.

## Remaining acceptance limits

- The released DSH host selected or restored a conversation on startup, so the no-conversation external-browser entry was covered by the browser/Host behavior test rather than observed in a real empty DSH window. No empty conversation was created by the plugin.
- A two-session Scope switch was observed in Web; Desktop was observed with a non-default bound Scope, but a two-session switch in the packaged Desktop shell was not performed.
- The final archive's Desktop no-token UI click-through was not repeated because the computer-control accessibility service lost the clean app window. The preceding archive passed that real Desktop path; the final archive's Desktop token path passed after installation.
- The custom authentication Provider plus Dashboard combination is rejected by PowerContext Server startup. It was not started as a live configuration; the UI reports Server unavailability separately from an embedded Browser load failure and directs diagnosis to Server startup logs.
- No other DSH release, remote/shared host, or production deployment was qualified.
