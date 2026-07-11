# PowerMem Full-Site Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove visible and latent AI-template patterns and deliver a consistent flagship-quality PowerMem website.

**Architecture:** Keep Docusaurus Classic, existing routes, localization, and content. Consolidate visual decisions into shared semantic tokens, simplify each page's information shape, and remove confirmed-unused legacy visual code rather than hiding it.

**Tech Stack:** Docusaurus 3.9.2, React 19, TypeScript, CSS Modules, Prism React Renderer, Playwright CLI, Impeccable detector.

---

### Task 1: Consolidate the global design system

**Files:**
- Modify: `docs/website/src/css/custom.css`

- [ ] Format and extend semantic tokens for typography, spacing, code colors, surfaces, borders, and state colors.
- [ ] Remove the unverified Inter family, excessive heading tracking, thick alert side border, and weak muted contrast.
- [ ] Add Ocean Pair light/dark token roles and documentation Prism overrides.
- [ ] Run Impeccable detector on `src/css/custom.css`; expect no findings.
- [ ] Run `npm run typecheck`; expect exit 0.
- [ ] Commit as `feat: refine website design system`.

### Task 2: Simplify the homepage

**Files:**
- Modify: `docs/website/src/components/Hero/index.tsx`
- Modify: `docs/website/src/components/Hero/styles.module.css`
- Modify: `docs/website/src/components/Features/index.tsx`
- Modify: `docs/website/src/components/Features/styles.module.css`
- Modify: `docs/website/src/components/ValueProps1/index.tsx`
- Modify: `docs/website/src/components/ValueProps1/styles.module.css`
- Modify: `docs/website/src/components/QuickStart/index.tsx`
- Modify: `docs/website/src/components/QuickStart/styles.module.css`

- [ ] Remove all interactive Hero demo state, timers, Run controls, and output UI.
- [ ] Render one static concise Python sample with separate Ocean Pair light and dark Prism themes.
- [ ] Convert five identical feature cards into three primary capability columns and one secondary two-item strip.
- [ ] Convert performance proof into a compact three-metric comparison band without nested cards.
- [ ] Reduce Quick Start to an install command, one code example, and one documentation action.
- [ ] Run detector and TypeScript; expect no findings and exit 0.
- [ ] Commit as `feat: distill homepage content`.

### Task 3: Fix product page hierarchy

**Files:**
- Modify: `docs/website/src/pages/features.tsx`
- Modify: `docs/website/src/pages/features.module.css`
- Modify: `docs/website/src/pages/benchmark.tsx`
- Modify: `docs/website/src/pages/benchmark.module.css`
- Modify: `docs/website/src/pages/community.tsx`
- Modify: `docs/website/src/pages/community.module.css`

- [ ] Rebuild Features rows so icon/title, description, and details receive useful widths; remove the More placeholder.
- [ ] Tighten Benchmark hero spacing, metric strip, and table typography without changing data.
- [ ] Render Community as three equal channels and an open contribution section with no empty grid cell.
- [ ] Verify 1440px and 390px layouts in both themes.
- [ ] Run TypeScript and commit as `feat: polish product pages`.

### Task 4: Polish documentation

**Files:**
- Modify: `docs/website/src/css/custom.css`

- [ ] Verify article measure, headings, paragraphs, lists, tables, tabs, callouts, code blocks, sidebar, and TOC in Ocean Pair.
- [ ] Keep alert borders uniform and remove one-sided accent treatment.
- [ ] Verify long code, long TOC labels, and mobile article layout.
- [ ] Run both-locale build and commit as `feat: polish documentation surfaces`.

### Task 5: Remove legacy visual code

**Files:**
- Delete only files proven unused by `rg` import analysis.

- [ ] Build an import inventory for AnimatedBackground, GridBackground, MouseGlow, Benchmark/Community homepage components, ValueProps, and ValueProps2–6.
- [ ] Delete only zero-consumer components and their CSS/icons.
- [ ] Run `npm run typecheck && npm run build`; expect exit 0.
- [ ] Run detector across `docs/website/src`; expect no findings.
- [ ] Commit as `refactor: remove obsolete website visuals`.

### Task 6: Full visual QA

**Files:**
- Modify only to fix observed defects.

- [ ] Verify home, Features, Benchmark, Community, and Sub Stores docs in English and Chinese.
- [ ] Verify light and dark at 1440×1000 and mobile at 390×844.
- [ ] Inspect screenshots for overflow, clipping, empty regions, repeated card grammar, contrast, and spacing.
- [ ] Run final `npm run typecheck && npm run build`.
- [ ] Remove QA artifacts, confirm clean status, and commit any fixes as `fix: finish full-site visual polish`.

