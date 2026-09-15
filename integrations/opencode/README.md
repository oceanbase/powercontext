# OpenCode integration

`community`

`plugins/powercontext` contains the native PowerContext plugin for OpenCode 1.x.

Install PowerContext and the plugin from the same Git ref:

```bash
uv tool install --force "powercontext[cli,server] @ git+https://github.com/oceanbase/powercontext.git@master"
powercontext setup opencode --source oceanbase/powercontext --ref master
powercontext server run
opencode
```

The plugin is a thin HTTP client. It does not embed storage or start the Server. It recalls bounded project context
for each user turn, captures eligible prompts as Source evidence, and exposes curated `pc_*` tools. Server failures
never block normal OpenCode work.

The same package provides a separate OpenCode TUI entrypoint. In interactive OpenCode, run `/pc` to access the
DSH-compatible PowerContext command set. The session prompt statusline also shows the current project's token savings
when comparable recall statistics are available.
