# PowerContext Hermes Command Companion

This standalone Hermes plugin registers `/pc` and `/powercontext` during normal
plugin discovery, before Hermes creates its first Agent. It forwards either
command to the PowerContext Memory Provider for the current interactive Agent
once that provider is initialized.

Typing `/pc ` or `/powercontext ` and pressing Tab/Down shows the available
first-level PowerContext commands in Hermes' autocomplete menu.

The companion is installed alongside
[`plugins/powercontext`](../powercontext/README.md) by:

```bash
powercontext setup hermes --source oceanbase/powercontext --ref master
```

It requires Hermes Agent v0.20.4 or newer. The companion does not provide
memory storage or lifecycle hooks; those remain owned by the exclusive
`powercontext` Memory Provider. Hermes v0.20.4 does not provide gateway plugin
commands with caller session or scope context, so the companion fails closed
in gateway sessions to prevent cross-session memory access. Use the provider's
Hermes tools for gateway sessions until Hermes exposes that context.

The [provider README](../powercontext/README.md) contains the citation format
and examples for memory entry operations such as `/pc get` and `/pc retire`.
