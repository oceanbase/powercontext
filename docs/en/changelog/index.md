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
    <div class="editorial-meta release-meta pc-listing-meta"><strong>v0.0.1</strong><span>Aug 13, 2026</span><span>Latest release</span></div>
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
