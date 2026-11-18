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

/**
 * Sidebar status and diagnostics styles. Every surface reads DSH's semantic
 * theme tokens, so light/dark and collapsed/rail layouts share one rule set.
 */
export const STATUS_STYLES = `
/* finesse · component: diagnostics · register=product · tokens: inherited DSH theme */
.pc-language { margin: 8px 14px 0; border: 1px solid var(--dsw-alias-border-l1, GrayText); border-radius: 8px; }
.pc-language label { display: inline-flex; align-items: center; gap: 5px; min-height: 36px; margin-inline-end: 12px; cursor: pointer; }
.pc-details { margin-top: 14px; }
.pc-details summary { cursor: pointer; padding: 10px 0; color: var(--dsw-alias-label-secondary, CanvasText); }
.pc-details summary:focus-visible, .pc-language input:focus-visible { outline: 2px solid var(--dsw-alias-state-focus, Highlight); outline-offset: 2px; }
.pc-detail-row { display: contents; }
.pc-section-title { margin-bottom: 8px !important; }
.pc-icon-button:active, .pc-text-button:active { background: var(--dsw-alias-interactive-bg-hover, Canvas); }
@media (pointer: coarse) { .pc-icon-button { min-width: 44px; min-height: 44px; } }
.pc-status { position: relative; display: flex; align-items: center; width: 100%; min-width: 0; }
.pc-trigger {
  display: flex; align-items: center; gap: 10px; width: 100%; min-width: 0; box-sizing: border-box;
  height: 42px; margin: 8px 0 0; padding: 0 var(--dsh-sidebar-row-padding, 12px);
  border: 0; border-radius: 12px; corner-shape: round;
  background: transparent; color: var(--dsw-alias-label-primary, CanvasText);
  font: inherit; text-align: start; cursor: pointer;
}
@media (hover: hover) { .pc-trigger:hover { background: var(--dsw-alias-interactive-bg-hover, rgba(127,127,127,.12)); } }
.pc-trigger[aria-expanded="true"] { background: var(--dsw-alias-interactive-bg-hover, rgba(127,127,127,.12)); }
.pc-trigger:focus-visible { outline: 2px solid var(--dsw-alias-state-focus, Highlight); outline-offset: 1px; }
.pc-trigger-rail { justify-content: center; gap: 0; width: 36px; height: 36px; margin: 0; padding: 0; border-radius: 50%; corner-shape: round; }
/* Official Dashboard favicon assets; DSH owns the active theme attribute. */
.pc-brand-icon { display: inline-flex; flex: none; }
.pc-brand-icon img { display: block; width: 100%; height: 100%; }
.pc-brand-icon .pc-brand-icon-reverse { display: none; }
[data-ds-dark-theme] .pc-brand-icon .pc-brand-icon-color { display: none; }
[data-ds-dark-theme] .pc-brand-icon .pc-brand-icon-reverse { display: block; }
.pc-mark { position: relative; display: inline-flex; flex: none; align-items: center; justify-content: center; width: 20px; height: 20px; color: var(--dsw-alias-label-secondary, currentColor); }
.pc-trigger-rail .pc-mark { width: 20px; height: 20px; }
.pc-dot { display: inline-block; flex: none; width: 8px; height: 8px; border-radius: 50%; corner-shape: round; background: var(--dsw-alias-state-idle-primary, GrayText); box-shadow: 0 0 0 2px var(--dsw-alias-bg-l1, Canvas); }
.pc-trigger .pc-dot { position: absolute; right: 0; bottom: 0; width: 6px; height: 6px; box-shadow: 0 0 0 1.5px var(--dsw-alias-bg-l1, Canvas); }
.pc-dot-ready { background: var(--dsw-alias-state-success-primary, #2f9e63); }
.pc-dot-connecting, .pc-dot-stale { background: var(--dsw-alias-state-warn-primary, #c58a12); }
.pc-dot-failed { background: var(--dsw-alias-state-error-primary, #cf3d3d); }
.pc-dot-connecting { animation: pc-pulse 1.6s ease-in-out infinite; }
.pc-trigger-label { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 14px; line-height: 20px; }
.pc-trigger-state { flex: none; color: var(--dsw-alias-label-tertiary, GrayText); font-size: 12px; line-height: 16px; white-space: nowrap; }
.pc-trigger-rail .pc-trigger-label, .pc-trigger-rail .pc-trigger-state { display: none; }
.pc-panel {
  position: fixed; z-index: 30; display: flex; flex-direction: column;
  width: 392px; max-width: calc(100vw - 24px); max-height: min(72vh, 720px);
  overflow: hidden; border: 1px solid var(--dsw-alias-border-l1, rgba(127,127,127,.28)); border-radius: 12px;
  background: var(--dsw-specific-menu, Canvas); color: var(--dsw-alias-label-primary, CanvasText);
  box-shadow: var(--dsw-elevation-prominent, 0 12px 36px rgba(18, 30, 48, .22));
  backdrop-filter: var(--dsw-menu-backdrop-filter, none);
  font-family: var(--dsw-font-family, system-ui, sans-serif); font-size: 13px; line-height: 18px;
}
.pc-panel *, .pc-panel *::before, .pc-panel *::after { box-sizing: border-box; }
.pc-panel-head { flex: none; display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 14px 14px 10px; border-bottom: 1px solid var(--dsw-alias-border-l1, rgba(127,127,127,.2)); }
.pc-brand { display: flex; align-items: center; gap: 9px; min-width: 0; }
.pc-brand .pc-mark { width: 22px; height: 22px; }
.pc-title { margin: 0; font-size: 14px; line-height: 18px; font-weight: 600; letter-spacing: -.01em; }
.pc-subtitle { color: var(--dsw-alias-label-tertiary, GrayText); font-size: 11px; line-height: 15px; }
.pc-actions { display: flex; align-items: center; gap: 4px; }
.pc-icon-button { display: inline-flex; align-items: center; justify-content: center; width: 28px; height: 28px; padding: 0; border: 0; border-radius: 8px; background: transparent; color: var(--dsw-alias-label-secondary, CanvasText); font: inherit; font-size: 16px; cursor: pointer; }
@media (hover: hover) { .pc-icon-button:hover { background: var(--dsw-alias-interactive-bg-hover, rgba(127,127,127,.12)); } }
.pc-icon-button:focus-visible { outline: 2px solid var(--dsw-alias-state-focus, Highlight); outline-offset: 1px; }
.pc-icon-button:disabled { opacity: .5; cursor: not-allowed; }
.pc-panel-body { min-height: 0; overflow: auto; padding: 12px 14px 14px; overscroll-behavior: contain; }
.pc-dashboard-entry { display: grid; gap: 9px; margin-bottom: 14px; padding-bottom: 14px; border-bottom: 1px solid var(--dsw-alias-border-l1, rgba(127,127,127,.2)); }
.pc-dashboard-intro { display: grid; gap: 2px; color: var(--dsw-alias-label-secondary, CanvasText); font-size: 12px; line-height: 17px; }
.pc-dashboard-intro strong { color: var(--dsw-alias-label-primary, CanvasText); font-size: 13px; line-height: 18px; }
.pc-dashboard-button { min-height: 36px; padding: 6px 12px; border: 0; border-radius: 8px; background: var(--dsw-alias-button-primary-fill, var(--dsw-alias-brand-primary, CanvasText)); color: var(--dsw-alias-label-primary-inverted, Canvas); font: inherit; font-weight: 600; white-space: nowrap; cursor: pointer; }
@media (hover: hover) { .pc-dashboard-button:not(:disabled):hover { background: var(--dsw-alias-button-primary-hover, var(--dsw-alias-brand-primary, CanvasText)); } }
.pc-dashboard-button:not(:disabled):active { background: var(--dsw-alias-button-primary-hover, var(--dsw-alias-brand-primary, CanvasText)); transform: translateY(1px); }
.pc-dashboard-button:disabled { opacity: .5; cursor: wait; }
.pc-dashboard-button:focus-visible { outline: 2px solid var(--dsw-alias-state-focus, Highlight); outline-offset: 2px; }
@media (pointer: coarse) { .pc-dashboard-button { min-height: 44px; } }
.pc-dashboard-notice { margin: 0; color: var(--dsw-alias-label-secondary, CanvasText); font-size: 12px; line-height: 17px; overflow-wrap: anywhere; }
.pc-dashboard-entry a.pc-text-button { text-align: center; text-decoration: none; }
.pc-summary { display: flex; align-items: flex-start; gap: 9px; padding: 10px 11px; border: 1px solid var(--dsw-alias-border-l1, rgba(127,127,127,.2)); border-radius: 10px; background: var(--dsw-alias-bg-l2, rgba(127,127,127,.05)); }
.pc-summary .pc-dot { position: static; margin-top: 5px; }
.pc-summary-copy { min-width: 0; flex: 1; }
.pc-summary-label { font-weight: 600; font-size: 13px; line-height: 18px; }
.pc-summary-detail { margin-top: 1px; color: var(--dsw-alias-label-secondary, CanvasText); font-size: 12px; line-height: 17px; overflow-wrap: anywhere; }
.pc-summary-time { flex: none; color: var(--dsw-alias-label-tertiary, GrayText); font-size: 11px; line-height: 16px; font-variant-numeric: tabular-nums; }
.pc-alert { margin-top: 10px; padding: 9px 10px; border-inline-start: 2px solid var(--dsw-alias-state-error-primary, #cf3d3d); border-radius: 8px; background: var(--dsw-alias-state-error-tertiary, rgba(207,61,61,.08)); }
.pc-alert-title { font-weight: 600; font-size: 12px; line-height: 17px; }
.pc-alert-text { margin: 2px 0 0; color: var(--dsw-alias-label-secondary, CanvasText); font-size: 12px; line-height: 17px; overflow-wrap: anywhere; }
.pc-alert-actions { margin-top: 7px; }
.pc-text-button { padding: 2px 8px; border: 1px solid var(--dsw-alias-border-l1, rgba(127,127,127,.28)); border-radius: 7px; background: transparent; color: var(--dsw-alias-label-primary, CanvasText); font: inherit; font-size: 12px; cursor: pointer; }
.pc-text-button:hover { background: var(--dsw-alias-interactive-bg-hover, rgba(127,127,127,.12)); }
.pc-text-button:focus-visible { outline: 2px solid var(--dsw-alias-state-focus, Highlight); outline-offset: 1px; }
.pc-section { margin-top: 14px; }
.pc-section-head { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; margin-bottom: 6px; }
.pc-section-title { margin: 0; color: var(--dsw-alias-label-secondary, CanvasText); font-size: 11px; line-height: 15px; font-weight: 600; letter-spacing: .04em; text-transform: uppercase; }
.pc-section-note { color: var(--dsw-alias-label-tertiary, GrayText); font-size: 11px; line-height: 15px; }
.pc-kv { display: grid; grid-template-columns: 92px minmax(0, 1fr); gap: 5px 10px; align-items: baseline; }
.pc-key { color: var(--dsw-alias-label-tertiary, GrayText); font-size: 12px; line-height: 17px; }
.pc-value { min-width: 0; font-size: 12px; line-height: 17px; overflow-wrap: anywhere; }
.pc-mono { font-family: var(--ds-font-family-code, ui-monospace, SFMono-Regular, Menlo, monospace); font-size: 11px; }
.pc-muted { color: var(--dsw-alias-label-tertiary, GrayText); }
.pc-empty { margin: 0; padding: 9px 10px; border: 1px dashed var(--dsw-alias-border-l1, rgba(127,127,127,.32)); border-radius: 8px; color: var(--dsw-alias-label-secondary, CanvasText); font-size: 12px; line-height: 17px; }
.pc-stage-list { display: flex; flex-direction: column; gap: 0; border: 1px solid var(--dsw-alias-border-l1, rgba(127,127,127,.2)); border-radius: 9px; overflow: hidden; }
.pc-stage { display: grid; grid-template-columns: 8px minmax(0, 1fr); gap: 8px; padding: 8px 10px; background: var(--dsw-alias-bg-l1, transparent); }
.pc-stage + .pc-stage { border-top: 1px solid var(--dsw-alias-border-l1, rgba(127,127,127,.16)); }
.pc-stage .pc-dot { position: static; margin-top: 5px; }
.pc-stage-main { min-width: 0; }
.pc-stage-head { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; }
.pc-stage-name { font-size: 12px; line-height: 17px; font-weight: 500; }
.pc-stage-state { flex: none; color: var(--dsw-alias-label-tertiary, GrayText); font-size: 11px; line-height: 16px; text-align: end; }
.pc-stage-message { margin: 2px 0 0; color: var(--dsw-alias-label-secondary, CanvasText); font-size: 11px; line-height: 16px; overflow-wrap: anywhere; }
.pc-stage-meta { margin: 2px 0 0; color: var(--dsw-alias-label-tertiary, GrayText); font-size: 10px; line-height: 14px; }
.pc-coverage { margin: 7px 0 0; color: var(--dsw-alias-label-tertiary, GrayText); font-size: 10px; line-height: 15px; overflow-wrap: anywhere; }
.pc-panel-foot { flex: none; display: flex; align-items: center; justify-content: space-between; gap: 10px; min-height: 42px; padding: 5px 14px; border-top: 1px solid var(--dsw-alias-border-l1, rgba(127,127,127,.2)); font-size: 12px; line-height: 17px; }
.pc-release-link { color: var(--dsw-static-deepseek-600, LinkText); text-decoration: none; font-weight: 500; }
[data-ds-dark-theme] .pc-release-link { color: var(--dsw-alias-link, LinkText); }
.pc-panel-foot .pc-release-link { flex: none; white-space: nowrap; }
.pc-release-link:focus-visible, .pc-update-button:focus-visible { outline: 2px solid var(--dsw-alias-state-focus, Highlight); outline-offset: 2px; }
.pc-version-actions { display: flex; min-width: 0; align-items: center; gap: 5px; }
.pc-running-version { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--dsw-alias-label-secondary, CanvasText); }
.pc-running-version .pc-mono { color: var(--dsw-alias-label-primary, CanvasText); }
.pc-panel-foot .pc-update-check { flex: none; width: 30px; height: 30px; }
.pc-update-button { flex: none; min-height: 28px; padding: 0 9px; border: 0; border-radius: 6px; font: inherit; font-weight: 600; white-space: nowrap; cursor: pointer; color: var(--dsw-static-neutral-bluish-00, HighlightText); background: var(--dsw-static-deepseek-600, Highlight); }
@media (hover: hover) {
  .pc-release-link:hover, .pc-update-button:hover { filter: brightness(.9); }
  [data-ds-dark-theme] .pc-release-link:hover { filter: brightness(1.1); }
}
.pc-update-button:active:not(:disabled) { filter: brightness(.9); }
.pc-update-button:disabled { opacity: .5; cursor: not-allowed; }
.pc-update-guide { grid-column: 2; font-size: 11px; color: var(--dsw-alias-label-secondary, CanvasText); }
.pc-update-check[aria-busy="true"] svg { animation: pc-update-spin 1.1s linear infinite; }
@keyframes pc-update-spin { to { transform: rotate(360deg); } }
@media (pointer: coarse) {
  .pc-panel-foot .pc-update-check, .pc-update-button { min-height: 44px; min-width: 44px; }
}
.pc-sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0; }
@keyframes pc-pulse { 0%,100% { opacity: 1; } 50% { opacity: .42; } }
@media (prefers-reduced-motion: reduce) {
  .pc-dot-connecting, .pc-update-check[aria-busy="true"] svg { animation: none; }
  .pc-trigger, .pc-panel, .pc-icon-button, .pc-text-button { transition: none; }
}
`
