# PowerContext for ZCode

This plugin connects the [open-source ZCode CLI](https://github.com/zai-org/ZCode) and the official Windows desktop app to a separately running PowerContext Server. The `UserPromptSubmit` Hook prepares bounded context and captures prompt Source evidence; the native MCP client exposes explicit Memory and Handoff tools. A Skill guides explicit writes.

Install and diagnose it with `powercontext setup zcode --source /path/to/powercontext` and `powercontext doctor zcode`. The installer keeps the Hook and MCP on one Server URL, preserves other ZCode configuration, and supports local checkouts or a GitHub source with `--ref`. Start the Server before setup and restart ZCode afterwards.

See the [English guide](../../docs/en/docs/integrations/zcode.md) or [中文指南](../../docs/zh/docs/integrations/zcode.md) for Scope setup, `.env`, authentication, capture controls, remote transport, validation, and uninstall instructions.

The official Windows desktop version 3.14.3 has been verified with live prompt capture, scheduled Memory generation, context injection and fresh-session recall, MCP Memory and Handoff calls, local Bearer authentication, recovery after a Server outage, and a fresh user-owned directory installation. These checks used local Servers. Remote HTTPS, other official versions, and a combined real-model/real-Server open-source CLI run remain unverified.
