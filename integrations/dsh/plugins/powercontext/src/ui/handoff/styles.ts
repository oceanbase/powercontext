/*
 * Copyright (c) 2026 OceanBase.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

export const HANDOFF_STYLES = `
@keyframes pc-handoff-spin { to { transform: rotate(360deg); } }
.pc-handoff-spinner { display: inline-block; width: 12px; height: 12px; border: 2px solid currentColor; border-right-color: transparent; border-radius: 50%; animation: pc-handoff-spin 1s linear infinite; }
@media (prefers-reduced-motion: reduce) { .pc-handoff-spinner { animation: none; border-right-color: currentColor; } }
.pc-handoff-action { display: inline-flex; align-items: center; justify-content: center; box-sizing: border-box; width: calc(28px + var(--dsh-content-font-delta, 0px)); height: calc(28px + var(--dsh-content-font-delta, 0px)); padding: 6px; border: 0; border-radius: var(--dsw-radius-sm); color: var(--dsw-alias-label-tertiary); background: transparent; cursor: pointer; }
.pc-handoff-action svg { width: calc(15px + var(--dsh-content-font-delta, 0px)); height: calc(15px + var(--dsh-content-font-delta, 0px)); }
@media (hover: hover) { .pc-handoff-action:hover { background: var(--dsw-alias-interactive-bg-hover); color: var(--dsw-alias-label-secondary); } }
.pc-handoff-action:focus-visible, .pc-handoff-menu-footer :focus-visible, .pc-handoff-panel :focus-visible { outline: var(--dsw-focus-ring-width) solid var(--dsw-focus-ring-color, var(--dsw-alias-state-business-primary)); outline-offset: 2px; }
.pc-handoff-menu { max-width: min(320px, calc(100vw - 24px)); max-height: min(360px, calc(100dvh - 24px)); overflow-y: auto; }
/* rc.3's right-side fallback otherwise pins a tall menu to the window top. */
.pc-handoff-menu.pc-handoff-menu[data-anchor-side="right"] { position-try-fallbacks: --dsh-menu-left, --pc-handoff-above-right, --pc-handoff-above-left, --dsh-menu-wide-above, --dsh-menu-window, --dsh-menu-wide-window; }
@position-try --pc-handoff-above-right { top: var(--dsh-menu-window-top); bottom: max(12px, calc(anchor(top) + 4px)); align-self: end; }
@position-try --pc-handoff-above-left { top: var(--dsh-menu-window-top); bottom: max(12px, calc(anchor(top) + 4px)); left: 12px; right: max(12px, calc(anchor(left) + 4px)); align-self: end; justify-self: end; }
.pc-handoff-brand { display: flex; align-items: center; gap: 6px; margin: 0 6px 3px; padding: 5px 4px 6px; border-bottom: .5px solid var(--dsw-alias-border-l2); color: var(--dsw-alias-label-secondary); font-size: 12px; font-weight: 500; line-height: 16px; }
.pc-handoff-menu-footer { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin: 3px 6px 0; padding: 6px 0 2px; border-top: .5px solid var(--dsw-alias-border-l2); }
.pc-handoff-menu-footer button { appearance: none; display: inline-flex; align-items: center; justify-content: center; gap: 5px; min-height: 26px; padding: 3px 8px; border: 0; border-radius: 999px; background: transparent; color: var(--dsw-alias-label-secondary); font-family: inherit; font-size: 12px; font-weight: 400; line-height: 18px; white-space: nowrap; cursor: pointer; }
.pc-handoff-menu-footer button span { display: inline-flex; color: var(--dsw-alias-label-tertiary); }
.pc-handoff-menu-footer button svg { width: 13px; height: 13px; }
.pc-handoff-menu-footer button:active:not(:disabled) { background: var(--dsw-alias-interactive-bg-active); }
.pc-handoff-panel { box-sizing: border-box; width: min(278px, calc(100vw - 40px)); padding: 1px 5px 5px; color: var(--dsw-alias-label-primary); font: inherit; font-size: 12px; line-height: 1.45; }
.pc-handoff-panel-header { display: grid; grid-template-columns: auto minmax(0, 1fr) auto; align-items: center; gap: 6px; padding: 0 2px 5px; margin-bottom: 3px; border-bottom: .5px solid var(--dsw-alias-border-l2); color: var(--dsw-alias-label-secondary); font-weight: 550; }
.pc-handoff-panel-header > span:nth-child(2) { overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
.pc-handoff-header-mark { display: grid; place-items: center; margin-inline-end: 4px; }
.pc-handoff-panel button, .pc-handoff-panel a { appearance: none; box-sizing: border-box; display: inline-flex; align-items: center; justify-content: center; gap: 5px; min-height: 27px; padding: 3px 9px; border: 0; border-radius: 999px; background: transparent; color: var(--dsw-alias-label-secondary); font: inherit; font-size: 11px; text-decoration: none; white-space: nowrap; cursor: pointer; }
.pc-handoff-panel button.pc-handoff-emphasis { background: var(--dsw-alias-interactive-bg-hover); color: var(--dsw-alias-label-primary); font-weight: 600; }
.pc-handoff-panel button.pc-handoff-back { width: 26px; min-height: 26px; padding: 5px; }
.pc-handoff-back svg, .pc-handoff-stage-icon svg { width: 14px; height: 14px; }
@media (hover: hover) {
  .pc-handoff-panel button:hover:not(:disabled), .pc-handoff-panel a:hover, .pc-handoff-menu-footer button:hover:not(:disabled) { background: var(--dsw-alias-interactive-bg-hover); color: var(--dsw-alias-label-primary); }
  .pc-handoff-panel button.pc-handoff-emphasis:hover:not(:disabled) { background: var(--dsw-alias-interactive-bg-active); }
}
.pc-handoff-panel button:active:not(:disabled), .pc-handoff-panel a:active { background: var(--dsw-alias-interactive-bg-active); }
.pc-handoff-panel button:disabled, .pc-handoff-menu-footer button:disabled { opacity: .5; cursor: not-allowed; }
.pc-handoff-panel p { margin: 0; overflow-wrap: anywhere; }
.pc-handoff-progress { display: flex; align-items: center; min-height: 32px; gap: 8px; padding: 2px 9px; color: var(--dsw-alias-label-secondary); }
.pc-handoff-spinner { box-sizing: border-box; flex: none; }
.pc-handoff-menu .pc-handoff-notice { margin: 6px 3px; padding: 7px 9px; border-radius: var(--dsw-radius-md, 10px); background: var(--dsw-alias-interactive-bg-hover); color: var(--dsw-alias-label-secondary); font-size: 11px; line-height: 1.55; overflow-wrap: anywhere; }
.pc-handoff-stage { padding: 7px 3px 4px; }
.pc-handoff-stage + .pc-handoff-stage { border-top: .5px solid var(--dsw-alias-border-l2); }
.pc-handoff-stage-heading { display: grid; grid-template-columns: 25px minmax(0, 1fr); gap: 8px; align-items: start; margin: 0 2px 5px; }
.pc-handoff-stage-icon { display: grid; width: 25px; height: 25px; place-items: center; border-radius: 50%; background: var(--dsw-alias-interactive-bg-hover); color: var(--dsw-alias-label-secondary); }
.pc-handoff-stage h3 { margin: 0; color: var(--dsw-alias-label-primary); font-size: 12px; font-weight: 600; line-height: 18px; overflow-wrap: anywhere; }
.pc-handoff-stage p { margin-top: 2px; color: var(--dsw-alias-label-secondary); font-size: 11px; line-height: 16px; }
.pc-handoff-actions, .pc-handoff-history-actions { display: flex; flex-wrap: wrap; align-items: center; gap: 3px; }
.pc-handoff-stage .pc-handoff-actions, .pc-handoff-details { margin-inline-start: 28px; }
.pc-handoff-stop { justify-content: end; padding-inline: 3px; }
.pc-handoff-details { margin-top: 4px; }
.pc-handoff-panel summary { width: fit-content; padding: 2px 5px; border-radius: 999px; color: var(--dsw-alias-label-secondary); cursor: pointer; font-size: 11px; }
.pc-handoff-details[open] { padding-bottom: 4px; }
.pc-handoff-details .pc-handoff-actions { margin-inline-start: 0; }
.pc-handoff-panel label { display: grid; gap: 3px; }
.pc-handoff-fields { display: grid; gap: 7px; padding: 7px 4px 4px; color: var(--dsw-alias-label-secondary); font-size: 11px; }
.pc-handoff-panel textarea, .pc-handoff-panel select { box-sizing: border-box; width: 100%; padding: 5px 8px; color: var(--dsw-alias-label-primary); background: var(--dsw-alias-bg-layer-1); border: .5px solid var(--dsw-alias-border-l3); border-radius: var(--dsw-radius-md, 10px); font: inherit; }
.pc-handoff-panel textarea { min-height: 43px; resize: vertical; }
.pc-handoff-fields .pc-handoff-actions { margin-inline-start: 0; }
.pc-handoff-diagnostic-row { display: flex; align-items: center; gap: 8px; padding: 7px 4px; border-bottom: .5px solid var(--dsw-alias-border-l2); }
.pc-handoff-diagnostic-row:last-child, .pc-handoff-history-item:last-child { border-bottom: 0; }
.pc-handoff-row-content { display: grid; flex: 1; min-width: 0; gap: 1px; }
.pc-handoff-row-title { overflow: hidden; color: var(--dsw-alias-label-primary); font-size: 12px; font-weight: 500; text-overflow: ellipsis; white-space: nowrap; }
.pc-handoff-row-meta { overflow: hidden; color: var(--dsw-alias-label-secondary); font-size: 11px; line-height: 16px; text-overflow: ellipsis; white-space: nowrap; }
.pc-handoff-row-meta.pc-handoff-break, .pc-handoff-diagnostic-status { white-space: normal; overflow-wrap: anywhere; }
.pc-handoff-diagnostic-status { display: flex; align-items: center; min-height: 16px; }
.pc-handoff-history-item { padding: 7px 4px; border-bottom: .5px solid var(--dsw-alias-border-l2); }
.pc-handoff-history-actions { margin-top: 3px; }
.pc-handoff-panel .pc-handoff-empty { margin: 0; padding: 16px 4px; color: var(--dsw-alias-label-secondary); text-align: center; }
`
