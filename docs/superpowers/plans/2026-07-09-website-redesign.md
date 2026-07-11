# PowerMem Website Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a cohesive light/dark PowerMem marketing and documentation website using Docusaurus Classic and the logo-derived `#0083FF` brand color.

**Architecture:** Keep the existing Docusaurus application, routes, locale files, and content pipeline. Replace forced-dark global CSS with semantic theme tokens, update existing React page components to consume those tokens, and rely exclusively on Docusaurus ColorMode for system preference, switching, and persistence.

**Tech Stack:** Docusaurus 3.9.2, React 19, TypeScript 5.6, CSS Modules, Infima design tokens, Playwright browser verification.

---

## File Structure

- `docs/website/docusaurus.config.ts`: native light/dark behavior and navbar controls.
- `docs/website/src/css/custom.css`: shared semantic tokens and documentation styling.
- `docs/website/src/components/{Hero,Features,ValueProps1,QuickStart}/`: homepage sections.
- `docs/website/src/pages/{features,benchmark,community}.{tsx,module.css}`: product pages.
- `docs/website/src/theme/`: only retained brand-specific Docusaurus overrides.

### Task 1: Establish the theme system

**Files:**
- Modify: `docs/website/docusaurus.config.ts`
- Replace: `docs/website/src/css/custom.css`

- [ ] **Step 1: Record the current forced-dark failure**

Run:

```bash
rg -n "disableSwitch: true|defaultMode: 'dark'|Force dark mode|background-color: #000000" docs/website/docusaurus.config.ts docs/website/src/css/custom.css
```

Expected: matches prove that light mode is disabled and black surfaces are forced.

- [ ] **Step 2: Enable native Docusaurus ColorMode**

Use exactly one implementation:

```ts
colorMode: {
  respectPrefersColorScheme: true,
  defaultMode: 'light',
  disableSwitch: false,
},
```

Do not add custom storage, media-query listeners, or fallback theme logic.

- [ ] **Step 3: Define semantic theme tokens**

Start `custom.css` with:

```css
:root {
  --ifm-color-primary: #0083ff;
  --ifm-color-primary-dark: #0074e3;
  --ifm-color-primary-darker: #006dd6;
  --ifm-color-primary-darkest: #005ab0;
  --ifm-color-primary-light: #2997ff;
  --ifm-color-primary-lighter: #42a3ff;
  --ifm-color-primary-lightest: #80c1ff;
  --pm-bg: #ffffff;
  --pm-surface: #f7f9fc;
  --pm-surface-raised: #ffffff;
  --pm-text: #171a1f;
  --pm-text-muted: #626b78;
  --pm-border: #e3e7ed;
  --pm-code-bg: #f6f8fa;
  --pm-shadow: 0 16px 40px rgb(19 28 45 / 8%);
  --pm-radius-sm: 8px;
  --pm-radius-md: 12px;
  --pm-content-width: 760px;
}

[data-theme='dark'] {
  --pm-bg: #0c0f13;
  --pm-surface: #11151a;
  --pm-surface-raised: #151a20;
  --pm-text: #f2f5f8;
  --pm-text-muted: #9aa4b2;
  --pm-border: #272d35;
  --pm-code-bg: #11161c;
  --pm-shadow: 0 18px 48px rgb(0 0 0 / 24%);
}
```

Add typography, navbar, footer, article, sidebar, TOC, code, table, admonition, focus, responsive, and reduced-motion rules using semantic variables.

- [ ] **Step 4: Verify forced-dark rules are gone**

Run:

```bash
rg -n "disableSwitch: true|Force dark mode|html\[data-theme='light'\].*#000000" docs/website/docusaurus.config.ts docs/website/src/css/custom.css
```

Expected: no matches.

- [ ] **Step 5: Type-check and commit**

Run:

```bash
cd docs/website && npm ci && npm run typecheck
git add docs/website/docusaurus.config.ts docs/website/src/css/custom.css docs/website/package-lock.json
git commit -m "feat: add unified light and dark themes"
```

Expected: TypeScript exits successfully.

### Task 2: Rebuild the homepage

**Files:**
- Modify: `docs/website/src/pages/index.tsx`
- Modify: `docs/website/src/components/Hero/index.tsx`
- Replace: `docs/website/src/components/Hero/styles.module.css`
- Modify: `docs/website/src/components/Features/index.tsx`
- Replace: `docs/website/src/components/Features/styles.module.css`
- Modify: `docs/website/src/components/ValueProps1/index.tsx`
- Replace: `docs/website/src/components/ValueProps1/styles.module.css`
- Modify: `docs/website/src/components/QuickStart/index.tsx`
- Replace: `docs/website/src/components/QuickStart/styles.module.css`

- [ ] **Step 1: Remove decorative background dependencies**

Compose the homepage as:

```tsx
<main>
  <Hero />
  <Features />
  <ValueProps1 />
  <QuickStart />
</main>
```

Remove `GridBackground` and `MouseGlow` imports and calls instead of hiding them.

- [ ] **Step 2: Implement the approved hero**

Use localized strings for the approved value proposition, `Get started`, `View GitHub`, and a native code block containing:

```python
from powermem import Memory

memory = Memory()
memory.add(messages, user_id="seven")
```

Use a two-column desktop layout and one-column mobile layout.

- [ ] **Step 3: Build the open capability system**

Use three primary groups—intelligent memory, agent-native isolation, and hybrid retrieval—with multimodal and storage details in a quieter secondary row. Each item has one heading and one description, with no duplicate English subtitle.

- [ ] **Step 4: Build the memory workflow**

Use three semantic steps:

```ts
const steps = [
  {key: 'capture', number: '01'},
  {key: 'organize', number: '02'},
  {key: 'retrieve', number: '03'},
];
```

Do not add animated canvases, glowing layers, or alternate layouts.

- [ ] **Step 5: Simplify QuickStart**

Create one final action with an installation command, documentation link, and GitHub link. Keep the command selectable and keyboard accessible.

- [ ] **Step 6: Type-check and commit**

Run:

```bash
cd docs/website && npm run typecheck
git add src/pages/index.tsx src/components/Hero src/components/Features src/components/ValueProps1 src/components/QuickStart
git commit -m "feat: redesign PowerMem homepage"
```

Expected: TypeScript exits successfully.

### Task 3: Redesign Features, Benchmark, and Community

**Files:**
- Modify: `docs/website/src/pages/features.tsx`
- Replace: `docs/website/src/pages/features.module.css`
- Modify: `docs/website/src/pages/benchmark.tsx`
- Replace: `docs/website/src/pages/benchmark.module.css`
- Modify: `docs/website/src/pages/community.tsx`
- Replace: `docs/website/src/pages/community.module.css`

- [ ] **Step 1: Refactor Features**

Keep existing localized claims but present them as compact rows. Use this shared shape:

```tsx
<article className={styles.featureRow}>
  <div className={styles.featureIcon}><Icon /></div>
  <div>
    <Heading as="h2">{t(`feature.${feature.key}.title`)}</Heading>
    <p>{t(`feature.${feature.key}.desc`)}</p>
  </div>
</article>
```

- [ ] **Step 2: Make Benchmark evidence-first**

Preserve existing benchmark values and links. Group them into introduction, metric summary, methodology, and readable result sections. Never invent benchmark numbers.

- [ ] **Step 3: Simplify Community**

Preserve existing destinations and localized copy. Make GitHub contribution the primary action and present Discord, X, issues, and discussions as a compact list.

- [ ] **Step 4: Verify page CSS uses theme variables**

Run:

```bash
rg -n "background:\s*#000|background-color:\s*#000|color:\s*#fff" docs/website/src/pages/{features,benchmark,community}.module.css
```

Expected: no matches.

- [ ] **Step 5: Type-check and commit**

Run:

```bash
cd docs/website && npm run typecheck
git add src/pages/features.tsx src/pages/features.module.css src/pages/benchmark.tsx src/pages/benchmark.module.css src/pages/community.tsx src/pages/community.module.css
git commit -m "feat: unify PowerMem product pages"
```

Expected: TypeScript exits successfully.

### Task 4: Polish documentation and global chrome

**Files:**
- Modify: `docs/website/src/css/custom.css`
- Modify: `docs/website/src/theme/Layout/index.tsx` only if the retained sprite requires theme-safe colors
- Modify: `docs/website/src/theme/NavbarItem/LocaleSwitchNavbarItem/styles.module.css`

- [ ] **Step 1: Audit custom theme overrides**

Run:

```bash
find docs/website/src/theme -type f -maxdepth 6 -print
rg -n "#000|#fff|rgb\(|rgba\(" docs/website/src/theme
```

Expected: an explicit list of overrides and hard-coded colors.

- [ ] **Step 2: Remove unnecessary overrides**

Remove copied Docusaurus components with no PowerMem-specific behavior and let Theme Classic supply them. Retain only brand behavior and the custom locale switch.

- [ ] **Step 3: Finish document surfaces**

Style these stable selectors with semantic variables:

```css
.theme-doc-sidebar-container {}
.theme-doc-markdown {}
.table-of-contents {}
.theme-code-block {}
.alert {}
.tabs {}
```

Keep article width at `var(--pm-content-width)`, use the native mobile drawer, and hide the right TOC only at Docusaurus's narrow breakpoint.

- [ ] **Step 4: Type-check and commit**

Run:

```bash
cd docs/website && npm run typecheck
git add src/css/custom.css src/theme
git commit -m "feat: polish documentation experience"
```

Expected: TypeScript exits successfully.

### Task 5: Production build and browser verification

**Files:**
- Modify only files required to fix observed failures.
- Create locally then remove: `docs/website/qa/`

- [ ] **Step 1: Build both locales**

Run:

```bash
bash scripts/deploy-frontend.sh
```

Expected: both locales build into `docs/website/build`.

- [ ] **Step 2: Start the production preview**

Run:

```bash
cd docs/website && npm run serve -- --host 127.0.0.1
```

Expected: a reachable local URL.

- [ ] **Step 3: Verify desktop routes and themes**

Inspect `/`, `/features`, `/benchmark`, `/community`, `/docs/guides/sub_stores`, and Chinese equivalents at 1440×1000. Capture light and dark homepage and docs screenshots, refresh after switching, and confirm persistence.

- [ ] **Step 4: Verify mobile routes**

Inspect the homepage and representative docs page at 390×844. Open the navbar and docs sidebar and confirm no horizontal overflow.

- [ ] **Step 5: Inspect screenshots directly**

Compare palette, typography, spacing, navigation, hero composition, documentation columns, code treatment, and light/dark parity against the approved preview.

- [ ] **Step 6: Remove QA artifacts and commit fixes**

Run:

```bash
rm -rf docs/website/qa
git status --short
git add docs/website
git commit -m "fix: finish responsive website polish"
```

Expected: temporary screenshots are not committed.

### Task 6: Final verification and handoff

**Files:**
- No source changes expected.

- [ ] **Step 1: Run the complete gate**

Run:

```bash
cd docs/website && npm run typecheck && npm run build
```

Expected: both commands exit with status 0.

- [ ] **Step 2: Audit the boundary**

Run:

```bash
git status --short
git diff HEAD~5 --stat
git log --oneline -6
```

Expected: changes are limited to website and design documentation.

- [ ] **Step 3: Handoff**

Report the clone path, preview URL, routes and viewports, build results, theme-switch result, and any intentional deviation. Do not push, deploy, or create an upstream pull request.

