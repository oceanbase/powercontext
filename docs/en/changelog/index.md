---
template: listing.html
page_type: changelog
title: Changelog
description: User-visible behavior changes in tagged PowerContext releases.
hide:
  - footer
---

<header class="editorial-header">
  <h1>Releases</h1>
  <p>Tagged PowerContext releases and changes that affect users. Design proposals remain in RFCs until they ship.</p>
</header>

<div class="editorial-list release-list pc-listing-list">
  <article class="editorial-row release-entry pc-listing-row">
    <div class="editorial-meta release-meta pc-listing-meta"><strong>v0.1.0</strong><span>Aug 31, 2026</span><span>Latest release</span></div>
    <div class="editorial-body release-body pc-listing-content">
      <h2>A broader, safer context runtime</h2>
      <p>This release expands PowerContext across agent hosts while strengthening local and service deployments.</p>
      <ul class="release-highlights">
        <li>Connect Hermes Agent, OpenCode, Pi Coding Agent, OpenClaw, WorkBuddy, Pydantic AI, LangChain, and LangGraph through first-class integrations and diagnostics.</li>
        <li>Run scheduled processing with tracing, inspect scoped Handoff Reports, and configure PowerContext interactively from the CLI.</li>
        <li>Use embedded seekDB or bundled sqlite-vec persistence with explicit embedding dimensions and clearer readiness failures.</li>
        <li>Apply safer transport defaults, secret-safe persistence diagnostics, and more complete installation, deployment, and HTTP API guidance.</li>
      </ul>
      <div class="release-install"><code>uv tool install --force "powercontext[cli,server]==0.1.0"</code></div>
      <div class="listing-actions"><a class="primary-button large" href="https://github.com/oceanbase/powercontext/releases/tag/powercontext-v0.1.0">Read release notes</a><a class="text-link" href="../docs/">Open documentation <span aria-hidden="true">→</span></a></div>
    </div>
  </article>

  <article class="editorial-row release-entry pc-listing-row">
    <div class="editorial-meta release-meta pc-listing-meta"><strong>v0.0.2</strong><span>Aug 20, 2026</span><span>Previous release</span></div>
    <div class="editorial-body release-body pc-listing-content">
      <h2>Work continuity across agents</h2>
      <p>This release adds an evidence-backed workflow for carrying work across sessions and agent hosts.</p>
      <ul class="release-highlights">
        <li>Record a Work Contract, prepare a Handoff, acknowledge receipt, and preserve the Task Outcome.</li>
        <li>Use official integrations for Codex, Claude Code, DeepSeek Harness, and Hermes Agent.</li>
        <li>Inspect current work through the default Handoff Report, with period comparison and Markdown export.</li>
        <li>Handle concurrent Memory changes explicitly and trace bounded context-building stages.</li>
      </ul>
      <div class="release-install"><code>uv tool install --force "powercontext[cli,server]==0.0.2"</code></div>
      <div class="listing-actions"><a class="primary-button large" href="https://github.com/oceanbase/powercontext/releases/tag/v0.0.2">Read release notes</a><a class="text-link" href="../docs/">Open documentation <span aria-hidden="true">→</span></a></div>
    </div>
  </article>

  <article class="editorial-row release-entry pc-listing-row">
    <div class="editorial-meta release-meta pc-listing-meta"><strong>v0.0.1</strong><span>Aug 13, 2026</span><span>Earlier release</span></div>
    <div class="editorial-body release-body pc-listing-content">
      <h2>First PowerContext release</h2>
      <p>This release establishes durable, project-scoped context across agent sessions.</p>
      <ul class="release-highlights">
        <li>Remember, search, revise, and retire Memory with citations and revision history.</li>
        <li>Connect Codex through the plugin, or use the CLI, Python client, HTTP, and MCP interfaces.</li>
        <li>Run the local Server with persistent SQLite storage and no required inference provider.</li>
      </ul>
      <div class="release-install"><code>uv tool install "powercontext[cli,server]==0.0.1"</code></div>
      <div class="listing-actions"><a class="primary-button large" href="https://github.com/oceanbase/powercontext/releases/tag/v0.0.1">Read release notes</a><a class="text-link" href="../docs/">Open documentation <span aria-hidden="true">→</span></a></div>
    </div>
  </article>

  <section class="editorial-row release-archive pc-listing-row" aria-labelledby="powermem-archive">
    <div class="editorial-meta pc-listing-meta"><strong>PowerMem</strong><span>Earlier project</span></div>
    <div class="editorial-body pc-listing-content">
      <h2 id="powermem-archive">PowerMem release archive</h2>
      <p>PowerContext succeeds PowerMem. Earlier PowerMem versions remain available in the shared GitHub release history.</p>
      <a class="row-action" href="https://github.com/oceanbase/powercontext/releases">View all releases <span aria-hidden="true">→</span></a>
    </div>
  </section>
</div>
