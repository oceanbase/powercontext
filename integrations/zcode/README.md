# PowerContext for ZCode

This plugin connects the [open-source ZCode CLI](https://github.com/zai-org/ZCode) and the official Windows desktop app to a separately running PowerContext Server. The `UserPromptSubmit` Hook prepares bounded context and captures prompt Source evidence; the native MCP client exposes explicit Memory and Handoff tools. The Hook also exposes current-request binding metadata. Content-free runtime observations, a Node status script and readonly doctor probes distinguish installation, historical Hook results and current connectivity. An installed Node Scope script verifies or explicitly changes workspace bindings; a routing Skill provides Memory, Handoff and candidate-review workflows.

Install and diagnose it with `powercontext setup zcode --source /path/to/powercontext` and `powercontext doctor zcode`. The installer keeps the Hook and MCP on one Server URL, preserves other ZCode configuration, and supports local checkouts or a GitHub source with `--ref`. Start the Server before setup and restart ZCode afterwards.

See the [English guide](../../docs/en/docs/integrations/zcode.md) or [中文指南](../../docs/zh/docs/integrations/zcode.md) for Scope setup, `.env`, authentication, capture controls, remote transport, validation, and uninstall instructions.

The official Windows desktop version 3.14.3 has been verified with live prompt capture, scheduled Memory generation, context injection and fresh-session recall, MCP Memory citation conflicts, nullable Handoff carriers, exact-revision Receipt/Outcome association, candidate version authorization, accurate Scope binding, readonly runtime diagnostics, and a fresh user-owned directory installation. Desktop checks used local Servers. The open-source CLI also passed real-model MCP and prompt capture against a remote HTTPS Server through SSH forwarding. CLI 0.16.9 has separately passed two independent controlled-model and two independent live-model core acceptance runs. Direct HTTPS ingress and other official versions remain unverified.

See [repeatable CLI acceptance](acceptance/README.md) and [manual Windows desktop acceptance](acceptance/desktop.md).
The CLI suite distinguishes controlled inference from live models and records individual scenario outcomes;
partial runs do not establish complete acceptance.

SessionStart resolves Scope; resume/compact handlers can restore readonly context. The tested open-source CLI emitted
startup/resume; `/compact` did not emit a compact event. Stop optionally processes pending Sources with a 1000 ms budget,
defaulting to disabled. Cursor progress, Memory creation and model reception remain separate evidence. Unknown flushes
pause automatic retry, and runtime status exposes pending tracking. Official Windows desktop 3.14.3 also completed
`/compact` without a compact Hook. Desktop resume emitted its Hook and readonly context; an opt-in Stop timed out
within budget and preserved unknown tracking. See the guides for these support limits and explicit unknown-result recovery.

During a desktop Server outage, an ordinary task continued. After the Server restarted, a single native MCP call in
the same task failed with `Session not found`; creating a new task restored native MCP without restarting the app.
This is a manual recovery path. CLI 0.16.9 also retained
an invalid MCP session, and closing/resuming that persisted session in the same process restored it.
