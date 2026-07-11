# Interactive Hero Memory Demo Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the static hero code panel with a click-to-run PowerMem memory extraction demo.

**Architecture:** Keep the feature in the existing Hero component. Model the UI as `idle | running | complete`, schedule deterministic transitions in one cleaned-up effect, and derive syntax colors from Docusaurus ColorMode.

**Tech Stack:** React 19, TypeScript, CSS Modules, Prism React Renderer, Docusaurus ColorMode.

---

### Task 1: Implement deterministic demo state

**Files:**
- Modify: `docs/website/src/components/Hero/index.tsx`

- [ ] Add `DemoState = 'idle' | 'running' | 'complete'`, state, timer cleanup, and a click handler.
- [ ] Render a Python label, status text, and native button with idle, running, and replay labels.
- [ ] Render an `aria-live="polite"` result region containing preference, scope, and persistence status.
- [ ] Detect reduced motion in the click handler and move directly to complete.
- [ ] Run `npm run typecheck`; expect exit 0.

### Task 2: Build the product-specific technical canvas

**Files:**
- Modify: `docs/website/src/components/Hero/styles.module.css`

- [ ] Replace the window-dot header with a toolbar and compact Run control.
- [ ] Add code execution, progress, result-grid, success, hover, focus, disabled, responsive, and reduced-motion styles.
- [ ] Keep one border without a wide decorative shadow; reserve blue for execution and green for persistence.
- [ ] Run the Impeccable detector on the Hero TSX and CSS; expect an empty JSON array.

### Task 3: Verify in production

**Files:**
- Modify only if browser evidence reveals a defect.

- [ ] Run `npm run typecheck && npm run build`; expect exit 0.
- [ ] At 1440×1000, verify idle, running, complete, and replay in light and dark modes.
- [ ] At 390×844, verify no overflow and a readable result stack.
- [ ] Inspect screenshots directly, remove QA artifacts, and commit as `feat: add interactive memory hero demo`.

