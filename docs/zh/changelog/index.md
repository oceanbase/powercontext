---
template: listing.html
page_type: changelog
title: 更新日志
description: PowerContext 正式版本中面向用户的行为变化。
hide:
  - footer
---

<header class="editorial-header">
  <h1>版本发布</h1>
  <p>这里记录 PowerContext 的正式版本，以及会影响使用方式的变化。尚未交付的设计仍保留在 RFC 中。</p>
</header>

<div class="editorial-list release-list pc-listing-list">
  <article class="editorial-row release-entry pc-listing-row">
    <div class="editorial-meta release-meta pc-listing-meta"><strong>v0.0.2</strong><span>2026 年 8 月 20 日</span><span>最新版本</span></div>
    <div class="editorial-body release-body pc-listing-content">
      <h2>跨 Agent 的工作连续性</h2>
      <p>这个版本提供基于证据的工作流，用于在不同会话和 Agent host 之间延续工作。</p>
      <ul class="release-highlights">
        <li>记录 Work Contract，准备 Handoff，确认接收状态，并保留 Task Outcome。</li>
        <li>使用 Codex、Claude Code、DeepSeek Harness 和 Hermes Agent 正式集成。</li>
        <li>通过默认启用的 Handoff Report 查看当前工作，并进行周期比较和 Markdown 导出。</li>
        <li>显式处理并发 Memory 变更，并追踪有界的上下文构建阶段。</li>
      </ul>
      <div class="release-install"><code>uv tool install --force "powercontext[cli,server]==0.0.2"</code></div>
      <div class="listing-actions"><a class="primary-button large" href="https://github.com/oceanbase/powercontext/releases/tag/v0.0.2">查看发布说明</a><a class="text-link" href="../docs/">打开文档 <span aria-hidden="true">→</span></a></div>
    </div>
  </article>

  <article class="editorial-row release-entry pc-listing-row">
    <div class="editorial-meta release-meta pc-listing-meta"><strong>v0.0.1</strong><span>2026 年 8 月 13 日</span><span>历史版本</span></div>
    <div class="editorial-body release-body pc-listing-content">
      <h2>PowerContext 首个版本</h2>
      <p>这个版本提供跨 Agent 会话、按项目划分的持久上下文。</p>
      <ul class="release-highlights">
        <li>通过引用与修订历史记录 Memory，并支持搜索、修订和停用。</li>
        <li>通过插件连接 Codex，也可以使用 CLI、Python 客户端、HTTP 和 MCP 接口。</li>
        <li>本地 Server 使用 SQLite 持久化存储，基础功能不依赖推理服务。</li>
      </ul>
      <div class="release-install"><code>uv tool install "powercontext[cli,server]==0.0.1"</code></div>
      <div class="listing-actions"><a class="primary-button large" href="https://github.com/oceanbase/powercontext/releases/tag/v0.0.1">查看发布说明</a><a class="text-link" href="../docs/">打开文档 <span aria-hidden="true">→</span></a></div>
    </div>
  </article>

  <section class="editorial-row release-archive pc-listing-row" aria-labelledby="powermem-archive">
    <div class="editorial-meta pc-listing-meta"><strong>PowerMem</strong><span>前身项目</span></div>
    <div class="editorial-body pc-listing-content">
      <h2 id="powermem-archive">PowerMem 版本存档</h2>
      <p>PowerContext 是 PowerMem 的后续项目。早期 PowerMem 版本仍保留在同一份 GitHub 发布记录中。</p>
      <a class="row-action" href="https://github.com/oceanbase/powercontext/releases">查看全部版本 <span aria-hidden="true">→</span></a>
    </div>
  </section>
</div>
