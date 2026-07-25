# Changelog — Bug Fixes (2026-07-25)

Batch of 7 issue fixes merged on 2026-07-25.

---

## #1178 — Website favicon displayed "undefined" class

**Problem:** The website favicon element was rendered with a CSS class that didn't exist in the stylesheet, causing it to display as `undefined`.

**Fix:** Removed the non-existent CSS class reference. The favicon now uses only valid, existing classes.

**Impact:** Website / Dashboard only. No backend or SDK changes.

---

## #1158 — NIM reranker not exported

**Problem:** `NimRerank` and `NimRerankConfig` were implemented in the rerank module but were not included in the package's public exports, so downstream code could not import them.

**Fix:** Added `NimRerank` and `NimRerankConfig` to the rerank package `__init__` exports.

**Impact:** Anyone using the NIM reranker provider. Previously they would get an `ImportError`; now the types are accessible.

---

## #1151 — OceanBase forget-marker updates lost

**Problem:** When using OceanBase as the storage backend, the `_forget_marker_updates()` helper did not persist changes to the metadata column, causing forget markers to be silently lost.

**Fix:** `_forget_marker_updates()` now writes to the metadata column alongside the primary update, ensuring markers survive a round-trip.

**Impact:** OceanBase users relying on the forget/memory-decay feature. Memories that should have been marked as forgotten were previously retained indefinitely.

---

## #1143 — `retention_score` returned `null`

**Problem:** The `retention_score` field in memory search results could be `null` when the underlying intelligence object did not expose the expected attribute directly.

**Fix:** The code now falls back to extracting the value from `intelligence.current_retention` when the primary path is unavailable.

**Impact:** All users. Search results now consistently include a numeric `retention_score` instead of `null`.

---

## #1137 — API responses leaked raw exception strings

**Problem:** Several API endpoints returned `str(e)` (the full Python exception message) in error responses, exposing internal implementation details to callers.

**Fix:** Replaced all 21 instances of `str(e)` in API error handlers with generic, user-facing error messages. Internal details are still logged server-side for debugging.

**Impact:** Security improvement for all HTTP/MCP API consumers. Error responses no longer leak stack traces, file paths, or internal class names.

---

## #1141 — Importance scoring inconsistency

**Problem:** Different modules used different formulas to compute memory importance, leading to inconsistent scores depending on which code path created or updated a memory.

**Fix:** Unified all importance evaluation to a single six-dimension weighted scoring model, applied consistently across extraction, update, and merge paths.

**Impact:** All users. Memory importance rankings are now deterministic and consistent regardless of the module that produced them.

---

## #1149 — Linear decay replaced by Ebbinghaus forgetting curve

**Problem:** Memory retention was calculated using a simple linear decay formula, which does not accurately model how humans forget information.

**Fix:** Replaced the linear formula with the `EbbinghausAlgorithm`, which models retention as an exponential forgetting curve based on elapsed time and memory strength.

**Impact:** All users. Memories now decay following a more realistic pattern — strong memories persist longer, weak ones fade faster. This improves retrieval relevance over time.
