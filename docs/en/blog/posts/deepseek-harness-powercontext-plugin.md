---
title: Building a PowerContext plugin for DeepSeek Harness
description: Connect project memory, automatic recall, and Handoff to DeepSeek Harness through a thin Cordis adapter.
draft: false
authors:
  - knqiufan
slug: deepseek-harness-powercontext-plugin
date: 2026-08-14
updated: 2026-11-18
cover: assets/blog/deepseek-harness-powercontext-plugin/cover.png
cover_alt: A modular plugin connects a session workbench to a separate project-context archive.
---

# Building a PowerContext plugin for DeepSeek Harness

On August 13, 2026, DeepSeek open-sourced the developer preview of
[DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness). One of its most interesting design choices is the
plugin system built on [Cordis](https://github.com/cordiverse/cordis): models, tools, sessions, sandboxes, storage,
loops, scheduling, and UI can all be composed as plugins without modifying the Harness core.

That design is a natural fit for PowerContext. PowerContext already keeps project Memory, Experience, Skill, and
Handoff in an independent Server. DeepSeek Harness only needs a thin adapter that connects Cordis lifecycle events,
tools, and commands to the PowerContext HTTP API.

The following examples use the standalone [powercontext-dsh v0.0.2 plugin](https://github.com/knqiufan/powercontext-dsh/tree/v0.0.2) to explain its Cordis integration.

<!-- more -->

## DeepSeek Harness and Cordis

The shortest way to start the DeepSeek Harness Web UI is:

```bash
npx @deepseek-ai/dsh web
```

The default URL is `http://127.0.0.1:3080`. You can also run it from source:

```bash
git clone https://github.com/deepseek-ai/deepseek-harness.git
cd deepseek-harness
pnpm install
pnpm run build
pnpm dsh web
```

![DeepSeek Harness Web UI](../../../assets/blog/deepseek-harness-powercontext/dsh-web.png)

*Original product screenshot; interface and example conversation retain their source language.*

Cordis organizes the Harness around a composable configuration tree rather than capabilities embedded permanently in
the main program. Tools, commands, events, and services registered by a plugin are reversible effects; removing the
plugin removes those capabilities from the runtime as well.

<figure class="fig" id="fig-cordis-plugin-tree">
<div class="fig-canvas" style="--fw:960;--fh:300" role="img" aria-label="Capabilities compose through plugins">
<svg viewBox="0 0 960 300">
<path class="fig-wire" d="M480 76 L480 106 Q480 116 470 116 L160 116 Q150 116 150 126 L150 154" marker-end="url(#fig-arrow)"/>
<path class="fig-wire" d="M480 76 L480 154" marker-end="url(#fig-arrow)"/>
<path class="fig-wire accent" d="M480 76 L480 106 Q480 116 490 116 L800 116 Q810 116 810 126 L810 154" marker-end="url(#fig-arrow-accent)"/>
</svg>
<div class="fig-box" style="--x:180;--y:0;--w:600;--h:76"><b>Harness · Cordis kernel</b><span>Composes the capabilities its plugins provide</span></div>
<div class="fig-card" style="--x:0;--y:160;--w:300;--h:112"><b>Models and tools</b><span>Model adapters route requests</span><span>Tools register through <code>defineTool</code></span></div>
<div class="fig-card" style="--x:330;--y:160;--w:300;--h:112"><b>Sessions and UI</b><span>Workspace, sessions and the</span><span>web interface are plugins</span></div>
<div class="fig-card accent" style="--x:660;--y:160;--w:300;--h:112"><b>powercontext-dsh</b><span>Project-memory recall</span><span>and work handoff</span></div>
</div>
</figure>

Two DeepSeek Harness concepts are important here:

- A **bundle** is a distributable plugin package. Its `dsh.bundle` declaration and `cordis.patch.yml` describe how it
  enters the configuration tree.
- A **profile** is the user's runtime recipe under `$DSH_HOME/profiles/<name>`. It selects the bundles to combine for a
  particular launch.

Think of bundles as building blocks and a profile as the assembly instructions. `dsh plugin --profile web add <pkg>`
installs a bundle into the `web` profile; it does not copy plugin code into the DeepSeek Harness repository.

## Why use two processes?

There are two backend processes; the browser is a client of the Harness UI:

1. **DeepSeek Harness** manages sessions and the agent loop. The PowerContext DSH plugin runs inside this process and
   registers hooks, `pc_*` tools, `/pc` commands, and usage guidance.
2. **PowerContext Server** is an independent Python process. It listens on `http://127.0.0.1:8000` by default and owns
   persistence, retrieval, review, and Handoff.

<figure class="fig" id="fig-two-process-architecture">
<div class="fig-canvas" style="--fw:960;--fh:270" role="img" aria-label="Two processes connected over HTTP">
<svg viewBox="0 0 960 270">
<path class="fig-wire" d="M180 138 L258 138" marker-end="url(#fig-arrow)"/>
<path class="fig-wire accent" d="M536 138 L658 138" marker-end="url(#fig-arrow-accent)"/>
</svg>
<div class="fig-box" style="--x:0;--y:92;--w:180;--h:92"><b>Browser</b><span>Local Harness UI</span></div>
<div class="fig-panel" style="--x:240;--y:0;--w:320;--h:270"><header><b>DeepSeek Harness</b><span>Node process</span></header></div>
<div class="fig-box accent" style="--x:264;--y:72;--w:272;--h:132"><b>powercontext-dsh</b><span><code>prepare_context</code></span><span><code>capture_source</code></span><span><code>pc_*</code> tools</span></div>
<div class="fig-panel" style="--x:640;--y:0;--w:320;--h:270"><header><b>PowerContext Server</b><span>Python process</span></header></div>
<div class="fig-box" style="--x:664;--y:72;--w:272;--h:132"><b>HTTP /v1</b><span>Memory and retrieval</span></div>
<span class="fig-label accent center small" style="--x:600;--y:112">HTTP</span>
<span class="fig-label muted small wrap" style="--x:0;--y:200;--w:180">The browser never calls the Server</span>
</div>
</figure>

The browser never connects directly to PowerContext Server. The Harness plugin makes the HTTP calls, so the local Web
UI does not need an additional CORS path. The required network path is from the machine running `dsh web` to the Server.

This boundary also allows both sides to evolve independently. The plugin neither reimplements storage nor embeds the
Python runtime; it depends only on PowerContext's public HTTP contract.

## Connecting the plugin to Cordis

A minimal bundle needs an entry point, `package.json`, and `cordis.patch.yml`. A production plugin should also follow a
few rules:

| Rule | Purpose |
| --- | --- |
| Use named exports for function plugins | Export `name`, `inject`, `Config`, and `apply`; do not mix in a default export |
| Declare only hard dependencies in `inject` | Resolve optional services through `ctx.get(name)` so their absence does not block loading |
| Use Standard Schema for `Config` | Allow Cordis to populate defaults and validate configuration |
| Register model tools through `defineTool` | Give inputs and outputs stable schemas |
| Ship a built `lib/` directory | Profiles do not compile TypeScript for external bundles |

The entry point loads peer modules from the Harness installation before registering tools and recall hooks, so the external plugin can resolve those dependencies:

```ts
export const name = 'powercontext-dsh'
export const inject = ['tools', 'agents']

export async function apply(ctx: Context, config: Config): Promise<void> {
  const toolsMod = await loadPeer<{ defineTool: DefineTool }>('@deepseek-ai/dsh-tools')
  const llmMod = await loadPeer<{ createUserMessage: CreateUserMessage }>('@deepseek-ai/dsh-llm')
  const runtime = createRuntime(ctx, config)
  registerGuidance(ctx)
  registerTools(ctx, runtime, toolsMod.defineTool)
  registerRecall(ctx, runtime, llmMod.createUserMessage)
  registerCommands(ctx, runtime)
  registerSkill(ctx)
}
```

Its default patch inserts the plugin into the configuration tree and connects to the local Server:

```yaml
- insert:
    - id: powercontext-dsh
      name: powercontext-dsh
      config:
        baseUrl: http://127.0.0.1:8000
        timeoutMs: 4000
        requestTimeoutMs: 1000
        maxBytes: 8000
        capturePrompts: true
        flushOnCapture: false
```

Do not put credentials in the patch. `--dump-config` prints the complete merged configuration tree, so secrets belong
in environment variables.

## Four layers of capability exposure

Registering every PowerContext operation as a model tool would flood the tool table with low-frequency operations.
The plugin instead covers the complete API through four layers:

| Layer | Caller | Purpose |
| --- | --- | --- |
| HTTP Client | Plugin internals | Covers the complete public API |
| Automatic hooks | Agent loop | Prepare context and capture Source before each model step |
| Named `pc_*` tools | Model | Common Memory, Handoff, Source, Experience, and Skill paths |
| `/pc` and generic calls | Human and model | Diagnostics, review, and less common operations |

Common named tools include:

- Memory: `pc_search`, `pc_remember`, `pc_memory_get`, `pc_memory_revise`, and `pc_memory_retire`;
- Context: `pc_prepare_context` and `pc_capture_source`;
- Handoff: `pc_handoff_activate`, `pc_handoff_prepare`, `pc_handoff_finalize`, `pc_handoff_commit`, and
  `pc_handoff_continue`;
- Experience and Skill: `pc_experience_generate`, `pc_experience_get`, `pc_skill_generate`, and `pc_skill_get`;
- Review: `pc_review_list` and `pc_review_get`.

The operation table used by the tools and generic call path comes from `openapi/powercontext.yaml`. The runtime does not
read PowerContext's Python source.

## Recall first, then capture

Before each model step, the plugin performs two operations through the `agent/pre-step` hook:

1. Call `prepare_context` to obtain relevant, size-bounded project context and inject it as reference material.
2. Call `capture_content_source` to preserve the step's text input as raw Source evidence.

Capturing Source is not the same as extracting Memory. Explicit Memory writes do not require a generation model. A
generation model is still required when the Server should extract Memory automatically from captured Sources.

<figure class="fig" id="fig-recall-timeline">
<div class="fig-canvas" style="--fw:960;--fh:236" role="img" aria-label="Recall and capture before a model step">
<svg viewBox="0 0 960 236">
<path class="fig-wire" d="M172 156 L191 156" marker-end="url(#fig-arrow)"/>
<path class="fig-wire" d="M369 156 L388 156" marker-end="url(#fig-arrow)"/>
<path class="fig-wire" d="M566 156 L585 156" marker-end="url(#fig-arrow)"/>
<path class="fig-wire" d="M763 156 L782 156" marker-end="url(#fig-arrow)"/>
<path class="fig-wire muted thin" d="M197 48 L197 40 Q197 36 201 36 L759 36 Q763 36 763 40 L763 48"/>
<path class="fig-wire muted thin" d="M788 48 L788 40 Q788 36 792 36 L956 36 Q960 36 960 40 L960 48"/>
</svg>
<span class="fig-label muted small" style="--x:0;--y:74">01</span>
<div class="fig-box start" style="--x:0;--y:96;--w:172;--h:120"><b>User message</b><span>Enters Harness</span></div>
<span class="fig-label muted small" style="--x:197;--y:74">02</span>
<div class="fig-box start accent" style="--x:197;--y:96;--w:172;--h:120"><b><code>prepare_context</code></b><span>Bounded context from the Server</span></div>
<span class="fig-label muted small" style="--x:394;--y:74">03</span>
<div class="fig-box start" style="--x:394;--y:96;--w:172;--h:120"><b>Inject evidence</b><span>As untrusted history, never as instructions</span></div>
<span class="fig-label muted small" style="--x:591;--y:74">04</span>
<div class="fig-box start accent" style="--x:591;--y:96;--w:172;--h:120"><b><code>capture_source</code></b><span>Store the user input as a Source</span></div>
<span class="fig-label muted small" style="--x:788;--y:74">05</span>
<div class="fig-box start" style="--x:788;--y:96;--w:172;--h:120"><b>Use memory</b><span>The model calls <code>pc_remember</code> or <code>pc_search</code></span></div>
<span class="fig-label muted small center" style="--x:480;--y:10">Before each model call</span>
<span class="fig-label muted small center" style="--x:874;--y:10">During the model step</span>
</div>
</figure>

Recall and capture fail open: a stopped Server or timeout does not interrupt the conversation. Project isolation uses
the normalized workspace Git remote when available and the workspace path otherwise. Set a stable override with
`POWERCONTEXT_DSH_SCOPE_ID` when required.

## Install and verify

The early standalone plugin described here requires a PowerContext Server with a matching HTTP contract. For installation and configuration of the current version, see the [DeepSeek Harness integration guide](/en/docs/integrations/dsh/).

Start a configured Server:

```bash
powercontext server run
```

Check it from another terminal:

```bash
curl http://127.0.0.1:8000/health/live
curl http://127.0.0.1:8000/health/ready
```

Create the DeepSeek Harness `web` profile, then load the matching standalone plugin [release](https://github.com/knqiufan/powercontext-dsh/releases/tag/v0.0.2):

```bash
dsh plugin --profile web add ./powercontext-dsh-0.0.2.tgz
```

An existing checkout of that plugin version can also be loaded directly:

```bash
dsh plugin --profile web add /path/to/powercontext-dsh
```

Start DeepSeek Harness:

```bash
dsh web
```

Use dump-config to confirm that the tree contains `id: powercontext-dsh`:

```bash
dsh --profile web --dump-config
```

![powercontext-dsh in the plugin list](../../../assets/blog/deepseek-harness-powercontext/plugin-list.png)

*Original product screenshot; interface and example conversation retain their source language.*

Enter `/pc` in the Web UI to list commands or `/pc doctor` to check Server connectivity. Once loaded, the plugin recalls
context and captures user input automatically. The agent invokes the corresponding `pc_*` tools for explicit Memory or
Handoff operations.

![Using a PowerContext command in DeepSeek Harness](../../../assets/blog/deepseek-harness-powercontext/pc-command.png)

*Original product screenshot; interface and example conversation retain their source language.*

## Remote Server and privacy controls

For a remote deployment, provide the URL and complete Authorization header through environment variables:

```bash
export POWERCONTEXT_DSH_BASE_URL=https://pc.example.com
export POWERCONTEXT_DSH_AUTHORIZATION="Bearer <long-random-secret>"
dsh web
```

The main settings are:

| Field | Environment variable | Default | Meaning |
| --- | --- | --- | --- |
| `baseUrl` | `POWERCONTEXT_DSH_BASE_URL` | `http://127.0.0.1:8000` | Server root URL |
| `authorization` | `POWERCONTEXT_DSH_AUTHORIZATION` | empty | Complete `Bearer <token>` value |
| `scopeId` | `POWERCONTEXT_DSH_SCOPE_ID` | inferred | Override the project scope |
| `timeoutMs` | — | `4000` | Shared automatic recall and capture budget |
| `requestTimeoutMs` | — | `1000` | Per-request HTTP timeout |
| `maxBytes` | — | `8000` | Prepared-context size budget |
| `capturePrompts` | `POWERCONTEXT_DSH_CAPTURE_PROMPTS` | `true` | Capture user prompts as Source |
| `flushOnCapture` | `POWERCONTEXT_DSH_FLUSH_ON_CAPTURE` | `false` | Flush immediately after capture |

Disable prompt capture before starting the Harness when the current work must not be persisted:

```bash
export POWERCONTEXT_DSH_CAPTURE_PROMPTS=false
dsh web
```

Never put credentials in prompts, Memory, patches, or configuration that `--dump-config` can print.

## Conclusion

DeepSeek Harness determines how a model acts in its environment. PowerContext preserves project context across sessions
and agents. Cordis lets the two systems combine as an installable, replaceable, and removable layer. The memory system
and coding agent can therefore evolve independently instead of becoming one monolithic runtime.

Related projects:

- [PowerContext](https://github.com/oceanbase/powercontext)
- [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness)
- [powercontext-dsh](https://github.com/knqiufan/powercontext-dsh)
- [Cordis](https://github.com/cordiverse/cordis)
- [A Programming Paradigm for Spatiotemporal Composability](https://github.com/cordiverse/paper)
- [DeepSeek Harness introduction](https://deepseek.com/harness/en/)
