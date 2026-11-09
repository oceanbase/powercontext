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
window.__ModuleLoader__.load({
	id: "powercontext-dsh",
	factory: (require) => {
		var module = { exports: {} };
		var exports = module.exports;
		(function (module, exports, require) {
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

let react = require("react");
let react_jsx_runtime = require("react/jsx-runtime");

//#region src/status-contract.ts
/**
* Wire contract between the plugin Host status bridge and its browser entry.
*
* The Host owns every field below. The browser parser reconstructs exactly the
* documented fields instead of spreading the response, so an accidentally added
* credential, path, or provider payload cannot reach the diagnostics panel.
*/
const STATUS_SCHEMA = "powercontext.dsh-status.v1";
const STATUS_ROUTE = "/powercontext/status";
const STATUS_REFRESH_INTERVAL_MS = 1e4;
const STATUS_STALE_AFTER_MS = 3e4;
const STATUS_REQUEST_TIMEOUT_MS = 8e3;
const STATUS_CONNECTING_RETRY_MS = 2e3;
const STATUS_RETRY_DELAYS_MS = [
	2e3,
	4e3,
	8e3,
	16e3,
	3e4
];
const STAGE_KEYS = [
	"scope",
	"prepare",
	"capture",
	"flush",
	"injection"
];
const CONNECTION_STATES = new Set([
	"unconfigured",
	"connecting",
	"ready",
	"failed"
]);
const PROBE_STATES = new Set([
	"ok",
	"failed",
	"unknown"
]);
const STAGE_STATES = new Set([
	"not_yet_observed",
	"running",
	"resolved",
	"ready",
	"empty",
	"accepted",
	"completed",
	"incomplete",
	"appended",
	"skipped",
	"unavailable"
]);
const MAX_TEXT = 2048;
function record(value) {
	return typeof value === "object" && value !== null && !Array.isArray(value);
}
function text(value, max = MAX_TEXT) {
	return typeof value === "string" && value.length > 0 ? value.slice(0, max) : void 0;
}
function finiteNumber(value) {
	return typeof value === "number" && Number.isFinite(value) ? value : void 0;
}
function nullableText(value) {
	return typeof value === "string" ? value : null;
}
function nullableNumber(value) {
	return typeof value === "number" && Number.isFinite(value) ? value : null;
}
function stringRecord(value) {
	if (!record(value)) return void 0;
	const result = {};
	for (const [key, item] of Object.entries(value)) if (typeof item === "string" && /^[a-zA-Z0-9._:-]{1,64}$/.test(key)) result[key] = item.slice(0, 256);
	return result;
}
function parseProbe(value) {
	if (!record(value) || !PROBE_STATES.has(value.state)) return null;
	return {
		state: value.state,
		...text(value.code) === void 0 ? {} : { code: text(value.code) },
		...text(value.message) === void 0 ? {} : { message: text(value.message) },
		...text(value.recovery) === void 0 ? {} : { recovery: text(value.recovery) },
		...finiteNumber(value.http_status) === void 0 ? {} : { http_status: finiteNumber(value.http_status) },
		...text(value.request_id, 128) === void 0 ? {} : { request_id: text(value.request_id, 128) },
		observed_at: nullableText(value.observed_at),
		age_ms: nullableNumber(value.age_ms),
		...stringRecord(value.dependencies) === void 0 ? {} : { dependencies: stringRecord(value.dependencies) }
	};
}
function parseStage(value) {
	if (!record(value) || !STAGE_STATES.has(value.state)) return null;
	return {
		operation: text(value.operation, 128) ?? "unknown",
		state: value.state,
		...text(value.code) === void 0 ? {} : { code: text(value.code) },
		...text(value.message) === void 0 ? {} : { message: text(value.message) },
		...text(value.recovery) === void 0 ? {} : { recovery: text(value.recovery) },
		...finiteNumber(value.http_status) === void 0 ? {} : { http_status: finiteNumber(value.http_status) },
		...text(value.request_id, 128) === void 0 ? {} : { request_id: text(value.request_id, 128) },
		...text(value.protocol_issue, 128) === void 0 ? {} : { protocol_issue: text(value.protocol_issue, 128) },
		...finiteNumber(value.content_bytes) === void 0 ? {} : { content_bytes: finiteNumber(value.content_bytes) },
		...value.confirmation === "rejected" || value.confirmation === "unconfirmed" ? { confirmation: value.confirmation } : {},
		observed_at: nullableText(value.observed_at),
		age_ms: nullableNumber(value.age_ms)
	};
}
function parseRuntime(value) {
	if (!record(value) || !record(value.stages)) return null;
	const stages = {};
	for (const key of STAGE_KEYS) {
		const stage = parseStage(value.stages[key]);
		if (stage !== null) stages[key] = stage;
	}
	for (const key of STAGE_KEYS) if (stages[key] === void 0) return null;
	const freshness = value.freshness;
	if (freshness !== "not_yet_observed" && freshness !== "current" && freshness !== "stale") return null;
	return {
		observation: text(value.observation, 128) ?? "local_automatic_path",
		freshness,
		...text(value.stale_reason) === void 0 ? {} : { stale_reason: text(value.stale_reason) },
		...text(value.reason) === void 0 ? {} : { reason: text(value.reason) },
		attempt: finiteNumber(value.attempt) ?? null,
		turn: typeof value.turn === "string" ? value.turn.slice(0, 32) : null,
		started_at: nullableText(value.started_at),
		age_ms: nullableNumber(value.age_ms),
		stale_after_ms: finiteNumber(value.stale_after_ms) ?? 3e5,
		observed_scope: nullableText(value.observed_scope),
		stages,
		coverage: text(value.coverage, 4096) ?? ""
	};
}
function parseSession(value) {
	if (value === null) return null;
	if (!record(value) || typeof value.id !== "string") return null;
	const scopeState = value.scope_state;
	if (scopeState !== "resolved" && scopeState !== "unresolved" && scopeState !== "not_attempted") return null;
	return {
		id: value.id.slice(0, 256),
		scope_id: nullableText(value.scope_id),
		scope_state: scopeState,
		...text(value.scope_code) === void 0 ? {} : { scope_code: text(value.scope_code) },
		...text(value.scope_message) === void 0 ? {} : { scope_message: text(value.scope_message) },
		...text(value.cwd, 4096) === void 0 ? {} : { cwd: text(value.cwd, 4096) }
	};
}
/**
* Rebuild a status payload from the exact documented fields.
* Unknown or malformed members are dropped; a malformed envelope returns null.
*/
function parseStatusPayload(value) {
	if (!record(value) || value.schema !== STATUS_SCHEMA) return null;
	const configuration = value.configuration;
	const connection = value.connection;
	const deployment = value.deployment;
	const server = value.server;
	const capabilities = value.capabilities;
	if (!record(configuration) || !record(connection) || !record(deployment) || !record(server) || !record(capabilities)) return null;
	if (!CONNECTION_STATES.has(connection.state)) return null;
	const liveness = parseProbe(server.liveness);
	const readiness = parseProbe(server.readiness);
	const capabilityProbe = parseProbe(server.capabilities);
	const runtime = parseRuntime(value.runtime);
	if (liveness === null || readiness === null || capabilityProbe === null || runtime === null) return null;
	const contextVersions = Array.isArray(capabilities.context_versions) ? capabilities.context_versions.filter((item) => typeof item === "string").slice(0, 32) : [];
	const notices = Array.isArray(value.notices) ? value.notices.filter((item) => typeof item === "string").slice(0, 16) : [];
	return {
		schema: STATUS_SCHEMA,
		observed_at: typeof value.observed_at === "string" ? value.observed_at : (/* @__PURE__ */ new Date()).toISOString(),
		plugin_version: text(value.plugin_version, 128) ?? "unknown",
		deployment: {
			kind: "local",
			mode: deployment.mode === "owner_local_web" ? "owner_local_web" : "desktop",
			owner_local: deployment.owner_local === "explicit" ? "explicit" : "automatic",
			ssh: false,
			bridge: "available",
			client: "packaged"
		},
		configuration: {
			endpoint_origin: text(configuration.endpoint_origin, 512) ?? "",
			endpoint_source: text(configuration.endpoint_source, 32) ?? "unknown",
			path_prefix: configuration.path_prefix === true,
			authorization_configured: configuration.authorization_configured === true,
			authorization_source: text(configuration.authorization_source, 32) ?? "unknown",
			scope_source: text(configuration.scope_source, 32) ?? "unknown",
			request_timeout_ms: finiteNumber(configuration.request_timeout_ms) ?? 0
		},
		connection: {
			state: connection.state,
			observed_at: nullableText(connection.observed_at),
			age_ms: nullableNumber(connection.age_ms),
			stale: connection.stale === true,
			stale_after_ms: finiteNumber(connection.stale_after_ms) ?? STATUS_STALE_AFTER_MS,
			generation: finiteNumber(connection.generation) ?? 0,
			in_flight: connection.in_flight === true,
			...text(connection.code) === void 0 ? {} : { code: text(connection.code) },
			...text(connection.message) === void 0 ? {} : { message: text(connection.message) },
			...text(connection.recovery) === void 0 ? {} : { recovery: text(connection.recovery) },
			...finiteNumber(connection.http_status) === void 0 ? {} : { http_status: finiteNumber(connection.http_status) }
		},
		session: parseSession(value.session),
		runtime,
		server: {
			liveness,
			readiness,
			capabilities: capabilityProbe
		},
		capabilities: {
			prepared_context: capabilities.prepared_context === true,
			context_versions: contextVersions,
			memory_extraction: typeof capabilities.memory_extraction === "boolean" ? capabilities.memory_extraction : null,
			handoff_generation: typeof capabilities.handoff_generation === "boolean" ? capabilities.handoff_generation : null
		},
		notices
	};
}

//#endregion
//#region src/ui/status-controller.ts
/** Reduce a Session list snapshot to the conversation currently retained by the main view. */
function activeSessionId(state) {
	return Object.values(state.byId).find((session) => (session.retainedBy?.mainView ?? 0) > 0)?.id;
}
/** Map a snapshot to the four-state sidebar palette, treating an expired success as retrying. */
function displayState(snapshot, now) {
	if (snapshot.error !== null) return snapshot.error.kind === "bridge" && [
		"host_bridge_unavailable",
		"owner_local_unverified",
		"owner_local_disabled",
		"ssh_untrusted"
	].includes(snapshot.error.code) ? "unconfigured" : "failed";
	if (snapshot.data === null) return snapshot.loading ? "connecting" : "unconfigured";
	const connection = snapshot.data.connection;
	if (connection.state === "failed") return "failed";
	if (connection.state === "unconfigured") return "unconfigured";
	if (connection.state === "ready") {
		const lastSuccess = snapshot.last_success_at ?? 0;
		return connection.stale || now - lastSuccess >= STATUS_STALE_AFTER_MS ? "stale" : "ready";
	}
	return "connecting";
}
function initialSnapshot() {
	return {
		state: "connecting",
		loading: false,
		data: null,
		error: null,
		last_success_at: null,
		session_id: null,
		generation: 0
	};
}
function normalizeSession(sessionId) {
	const value = sessionId?.trim();
	return value ? value.slice(0, 256) : void 0;
}
function errorFromResponse(response, body) {
	const code = typeof body?.code === "string" ? body.code.slice(0, 128) : void 0;
	const message = typeof body?.message === "string" ? body.message.slice(0, 1024) : void 0;
	const recovery = typeof body?.recovery === "string" ? body.recovery.slice(0, 1024) : void 0;
	if (response.status === 401) return {
		kind: "authentication",
		code: code ?? "plugin_authentication_required",
		http_status: 401,
		message: message ?? "DSH authentication is required before PowerContext status can be read.",
		recovery: recovery ?? "Sign in to DSH again, then retry."
	};
	if (response.status === 403) {
		if (code === "owner_local_unverified" || code === "owner_local_disabled" || code === "ssh_untrusted") return {
			kind: "bridge",
			code,
			http_status: 403,
			message: message ?? "This host is not a verified local DSH owner deployment.",
			recovery: recovery ?? "Configure ownerLocal only for your own local Web host, or use DSH Desktop."
		};
		return {
			kind: "forbidden",
			code: code ?? "plugin_forbidden",
			http_status: 403,
			message: message ?? "This DSH request is not allowed to read PowerContext status.",
			recovery: recovery ?? "Use the local DSH owner session; remote or shared hosts are not supported."
		};
	}
	if (response.status === 404) return {
		kind: "bridge",
		code: "host_bridge_unavailable",
		http_status: 404,
		message: "This host does not expose the PowerContext status bridge.",
		recovery: "Update the PowerContext DSH plugin and restart the host."
	};
	return {
		kind: "bridge",
		code: code ?? "status_bridge_error",
		http_status: response.status,
		message: message ?? `The PowerContext status bridge returned HTTP ${String(response.status)}.`,
		recovery: recovery ?? "Check DSH logs and the installed plugin build."
	};
}
async function readErrorBody(response) {
	try {
		const text$1 = (await response.text()).slice(0, 4096);
		const parsed = JSON.parse(text$1);
		return typeof parsed === "object" && parsed !== null && !Array.isArray(parsed) ? parsed : null;
	} catch {
		return null;
	}
}
/**
* Visibility-aware status refresher. It owns one request at a time, invalidates
* responses when the Session or a manual refresh supersedes them, pauses while
* hidden, and backs off after failures. It never reads `/pc doctor`.
*/
var StatusController = class {
	fetcher;
	now;
	visibility;
	setTimer;
	clearTimer;
	baseUrl;
	listeners = /* @__PURE__ */ new Set();
	snapshot = initialSnapshot();
	started = false;
	visible = true;
	inFlight = false;
	retry = 0;
	generation = 0;
	timer;
	abort;
	constructor(options = {}) {
		this.fetcher = options.fetch ?? ((input, init) => fetch(input, init));
		this.now = options.now ?? Date.now;
		this.visibility = options.document === void 0 ? typeof document === "undefined" ? null : document : options.document;
		this.setTimer = options.setTimeout ?? ((handler, milliseconds) => setTimeout(handler, milliseconds));
		this.clearTimer = options.clearTimeout ?? ((timer) => clearTimeout(timer));
		this.baseUrl = (options.baseUrl ?? "").replace(/\/+$/, "");
		this.visible = this.visibility?.visibilityState !== "hidden";
	}
	getSnapshot = () => this.snapshot;
	subscribe = (listener) => {
		this.listeners.add(listener);
		if (!this.started) this.start();
		return () => {
			this.listeners.delete(listener);
			if (this.listeners.size === 0) this.stop();
		};
	};
	/** Attach visibility tracking and make the first bounded request. */
	start() {
		if (this.started) return;
		this.started = true;
		this.visibility?.addEventListener("visibilitychange", this.onVisibility);
		this.visible = this.visibility?.visibilityState !== "hidden";
		if (this.visible) this.refreshNow();
	}
	/** Detach timers and listeners; retained data stays available until the next mount. */
	stop() {
		if (!this.started) return;
		this.started = false;
		this.visibility?.removeEventListener("visibilitychange", this.onVisibility);
		this.cancelScheduled();
		this.abort?.abort();
		this.abort = void 0;
		this.inFlight = false;
		this.generation += 1;
	}
	/** Release everything; called from the plugin effect's disposer. */
	dispose() {
		this.stop();
		this.listeners.clear();
	}
	/**
	* Select the active conversation. A changed identity invalidates old data and
	* supersedes any response already in flight.
	*/
	setSession(sessionId) {
		const next = normalizeSession(sessionId) ?? null;
		if (next === this.snapshot.session_id) return;
		this.generation += 1;
		this.abort?.abort();
		this.abort = void 0;
		this.inFlight = false;
		this.retry = 0;
		this.cancelScheduled();
		this.publish({
			state: "connecting",
			loading: false,
			data: null,
			error: null,
			last_success_at: null,
			session_id: next,
			generation: this.generation
		});
		if (this.started && this.visible) this.refreshNow();
	}
	/** Manual refresh: abandon an in-flight read so the user's action is not queued behind it. */
	refreshNow() {
		if (!this.started || !this.visible) return;
		this.cancelScheduled();
		if (this.inFlight) {
			this.generation += 1;
			this.abort?.abort();
			this.abort = void 0;
			this.inFlight = false;
		}
		this.refresh();
	}
	onVisibility = () => {
		const visible = this.visibility?.visibilityState !== "hidden";
		if (visible === this.visible) return;
		this.visible = visible;
		if (!visible) {
			this.cancelScheduled();
			this.generation += 1;
			this.abort?.abort();
			this.abort = void 0;
			this.inFlight = false;
			return;
		}
		this.refreshNow();
	};
	async refresh() {
		if (!this.started || !this.visible || this.inFlight) return;
		this.inFlight = true;
		const generation = ++this.generation;
		this.publish({ loading: true });
		const controller = new AbortController();
		this.abort = controller;
		const timeout = this.setTimer(() => {
			controller.abort(new DOMException("Status request exceeded its deadline", "TimeoutError"));
		}, STATUS_REQUEST_TIMEOUT_MS);
		try {
			const params = new URLSearchParams();
			if (this.snapshot.session_id !== null) params.set("session_id", this.snapshot.session_id);
			const query = params.toString();
			const response = await this.fetcher(`${this.baseUrl}${STATUS_ROUTE}${query ? `?${query}` : ""}`, {
				method: "GET",
				headers: { accept: "application/json" },
				cache: "no-store",
				credentials: "same-origin",
				redirect: "error",
				signal: controller.signal
			});
			if (generation !== this.generation || !this.started) return;
			if (!response.ok) {
				this.fail(generation, errorFromResponse(response, await readErrorBody(response)));
				return;
			}
			let parsed;
			try {
				parsed = await response.json();
			} catch {
				this.fail(generation, {
					kind: "protocol",
					code: "invalid_status_response",
					http_status: response.status,
					message: "The PowerContext status bridge returned a non-JSON response.",
					recovery: "Check the installed plugin build and DSH logs."
				});
				return;
			}
			const payload = parseStatusPayload(parsed);
			if (payload === null) {
				this.fail(generation, {
					kind: "protocol",
					code: "invalid_status_response",
					http_status: response.status,
					message: "The PowerContext status response did not match its documented schema.",
					recovery: "Install matching PowerContext Server and DSH plugin versions."
				});
				return;
			}
			if (generation !== this.generation || !this.started) return;
			this.retry = 0;
			const now = this.now();
			this.publish({
				state: displayState({
					...this.snapshot,
					data: payload,
					error: null,
					last_success_at: now
				}, now),
				loading: false,
				data: payload,
				error: null,
				last_success_at: now,
				session_id: payload.session?.id ?? null,
				generation
			});
			this.schedule(payload.connection.state === "ready" ? STATUS_REFRESH_INTERVAL_MS : STATUS_CONNECTING_RETRY_MS);
		} catch (error) {
			if (generation !== this.generation || !this.started) return;
			const timedOut = error instanceof Error && error.name === "TimeoutError";
			this.fail(generation, timedOut ? {
				kind: "network",
				code: "status_request_timeout",
				message: "The PowerContext status request exceeded its deadline.",
				recovery: "Check the PowerContext Server and DSH host, then retry."
			} : {
				kind: "network",
				code: "host_unreachable",
				message: "The PowerContext status bridge could not be reached.",
				recovery: "Check that DSH is still running, then retry."
			});
		} finally {
			this.clearTimer(timeout);
			if (generation === this.generation) {
				this.inFlight = false;
				this.abort = void 0;
			}
		}
	}
	fail(generation, error) {
		if (generation !== this.generation || !this.started) return;
		this.retry += 1;
		this.publish({
			state: "failed",
			loading: false,
			error,
			generation
		});
		this.schedule(STATUS_RETRY_DELAYS_MS[Math.min(this.retry - 1, STATUS_RETRY_DELAYS_MS.length - 1)]);
	}
	schedule(delay) {
		this.cancelScheduled();
		if (!this.started || !this.visible) return;
		this.timer = this.setTimer(() => {
			this.timer = void 0;
			this.refresh();
		}, delay);
	}
	cancelScheduled() {
		if (this.timer !== void 0) {
			this.clearTimer(this.timer);
			this.timer = void 0;
		}
	}
	publish(patch) {
		const next = {
			...this.snapshot,
			...patch
		};
		next.state = displayState(next, this.now());
		const previous = this.snapshot;
		if (previous.state === next.state && previous.loading === next.loading && previous.data === next.data && previous.error === next.error && previous.last_success_at === next.last_success_at && previous.session_id === next.session_id && previous.generation === next.generation) return;
		this.snapshot = next;
		for (const listener of this.listeners) listener();
	}
};

//#endregion
//#region src/ui/styles.ts
/**
* Sidebar status and diagnostics styles. Every surface reads DSH's semantic
* theme tokens, so light/dark and collapsed/rail layouts share one rule set.
*/
const STATUS_STYLES = `
.pc-status { position: relative; display: flex; align-items: center; width: 100%; min-width: 0; }
.pc-trigger {
  display: flex; align-items: center; gap: 8px; width: calc(100% + 4px); min-width: 0;
  height: 42px; margin: 8px -2px 0; padding: 0 10px 0 8px;
  border: 0; border-radius: 12px; corner-shape: round;
  background: transparent; color: var(--dsw-alias-label-primary, CanvasText);
  font: inherit; text-align: start; cursor: pointer;
}
@media (hover: hover) { .pc-trigger:hover { background: var(--dsw-alias-interactive-bg-hover, rgba(127,127,127,.12)); } }
.pc-trigger[aria-expanded="true"] { background: var(--dsw-alias-interactive-bg-hover, rgba(127,127,127,.12)); }
.pc-trigger:focus-visible { outline: 2px solid var(--dsw-alias-state-focus, Highlight); outline-offset: 1px; }
.pc-trigger-rail { justify-content: center; gap: 0; width: 36px; height: 36px; margin: 0; padding: 0; border-radius: 50%; corner-shape: round; }
.pc-mark { position: relative; display: inline-flex; flex: none; align-items: center; justify-content: center; width: 20px; height: 20px; color: var(--dsw-alias-label-secondary, currentColor); }
.pc-trigger-rail .pc-mark { width: 36px; height: 36px; }
.pc-dot { display: inline-block; flex: none; width: 8px; height: 8px; border-radius: 50%; corner-shape: round; background: var(--dsw-alias-state-idle-primary, GrayText); box-shadow: 0 0 0 2px var(--dsw-alias-bg-l1, Canvas); }
.pc-trigger .pc-dot { position: absolute; right: 1px; bottom: 1px; }
.pc-trigger-wide .pc-dot { position: static; margin-inline-start: auto; }
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
.pc-icon-button:disabled { opacity: .5; cursor: default; }
.pc-panel-body { min-height: 0; overflow: auto; padding: 12px 14px 14px; overscroll-behavior: contain; }
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
.pc-panel-foot { flex: none; display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 8px 14px 10px; border-top: 1px solid var(--dsw-alias-border-l1, rgba(127,127,127,.2)); color: var(--dsw-alias-label-tertiary, GrayText); font-size: 10px; line-height: 14px; }
.pc-sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0; }
@keyframes pc-pulse { 0%,100% { opacity: 1; } 50% { opacity: .42; } }
@media (prefers-reduced-motion: reduce) {
  .pc-dot-connecting { animation: none; }
  .pc-trigger, .pc-panel, .pc-icon-button, .pc-text-button { transition: none; }
}
`;

//#endregion
//#region src/ui/client.tsx
const STYLE_ID = "powercontext-dsh-status-styles";
const inject = ["slots"];
const STAGE_ROWS = [
	{
		key: "scope",
		label: "Scope resolution"
	},
	{
		key: "prepare",
		label: "Recall"
	},
	{
		key: "capture",
		label: "Source capture"
	},
	{
		key: "flush",
		label: "Memory flush"
	},
	{
		key: "injection",
		label: "Context injection"
	}
];
function PowerContextMark({ size = 20 }) {
	return /* @__PURE__ */ (0, react_jsx_runtime.jsxs)("svg", {
		width: size,
		height: size,
		viewBox: "0 0 24 24",
		fill: "none",
		stroke: "currentColor",
		strokeWidth: "1.7",
		strokeLinecap: "round",
		strokeLinejoin: "round",
		"aria-hidden": "true",
		focusable: "false",
		children: [
			/* @__PURE__ */ (0, react_jsx_runtime.jsx)("path", { d: "M4 7.5 12 3l8 4.5-8 4.5-8-4.5Z" }),
			/* @__PURE__ */ (0, react_jsx_runtime.jsx)("path", {
				d: "m4 12 8 4.5 8-4.5",
				opacity: ".72"
			}),
			/* @__PURE__ */ (0, react_jsx_runtime.jsx)("path", {
				d: "m4 16.5 8 4.5 8-4.5",
				opacity: ".45"
			})
		]
	});
}
function Dot({ state }) {
	return /* @__PURE__ */ (0, react_jsx_runtime.jsx)("span", {
		className: `pc-dot pc-dot-${state}`,
		"aria-hidden": "true"
	});
}
function StatusLabel({ state, snapshot }) {
	if (state === "failed" && snapshot.error?.kind === "authentication") return /* @__PURE__ */ (0, react_jsx_runtime.jsx)(react_jsx_runtime.Fragment, { children: "Authentication required" });
	switch (state) {
		case "unconfigured": return /* @__PURE__ */ (0, react_jsx_runtime.jsx)(react_jsx_runtime.Fragment, { children: "Not configured" });
		case "connecting": return snapshot.error === null ? /* @__PURE__ */ (0, react_jsx_runtime.jsx)(react_jsx_runtime.Fragment, { children: "Connecting" }) : /* @__PURE__ */ (0, react_jsx_runtime.jsx)(react_jsx_runtime.Fragment, { children: "Retrying" });
		case "stale": return /* @__PURE__ */ (0, react_jsx_runtime.jsx)(react_jsx_runtime.Fragment, { children: "Stale, retrying" });
		case "ready": return /* @__PURE__ */ (0, react_jsx_runtime.jsx)(react_jsx_runtime.Fragment, { children: "Connected" });
		case "failed": return /* @__PURE__ */ (0, react_jsx_runtime.jsx)(react_jsx_runtime.Fragment, { children: "Action needed" });
	}
}
function triggerLabel(state, snapshot) {
	const label = state === "ready" ? "Connected" : state === "connecting" ? "Connecting" : state === "stale" ? "Status stale, retrying" : state === "failed" ? "Action needed" : "Not configured";
	const error = snapshot.error;
	return error === null ? label : `${label}: ${error.message}`;
}
function summaryDetail(state, snapshot) {
	if (state === "failed") return snapshot.error?.message ?? snapshot.data?.connection.message ?? "PowerContext is not ready.";
	if (state === "unconfigured") return snapshot.error?.message ?? "The status bridge is unavailable in this host. Existing tools still work.";
	if (state === "stale") return "The last observation expired; re-verifying with the Server.";
	if (state === "connecting") return "Contacting the PowerContext Server and checking readiness.";
	if (snapshot.data?.session === null) return "Readiness verified. There is no active conversation to observe.";
	return "Connectivity, readiness, and the required context capability are verified.";
}
function ago(milliseconds, now) {
	if (milliseconds === null || milliseconds === void 0) return "not observed";
	const seconds = Math.max(0, Math.round(milliseconds / 1e3));
	if (seconds < 5) return "just now";
	if (seconds < 60) return `${String(seconds)}s ago`;
	const minutes = Math.round(seconds / 60);
	if (minutes < 60) return `${String(minutes)}m ago`;
	return `${String(Math.round(minutes / 60))}h ago`;
}
function clock(iso) {
	if (iso === null) return "not yet";
	try {
		return new Date(iso).toLocaleTimeString([], {
			hour: "2-digit",
			minute: "2-digit",
			second: "2-digit"
		});
	} catch {
		return iso;
	}
}
function probeLabel(probe) {
	if (probe.state === "ok") return "Verified";
	if (probe.state === "failed") return "Failed";
	return "Not checked";
}
function stageLabel(stage) {
	switch (stage.state) {
		case "not_yet_observed": return "Not yet observed";
		case "running": return "Running";
		case "resolved": return "Resolved";
		case "ready": return "Ready";
		case "empty": return "Empty";
		case "accepted": return "Accepted";
		case "completed": return "Completed";
		case "incomplete": return "Incomplete";
		case "appended": return "Injected";
		case "skipped": return "Skipped";
		case "unavailable": return "Unavailable";
	}
}
function stageTone(stage) {
	if (stage.state === "unavailable") return "failed";
	if (stage.state === "running") return "connecting";
	if ([
		"resolved",
		"ready",
		"empty",
		"accepted",
		"completed",
		"appended"
	].includes(stage.state)) return "ready";
	return "unconfigured";
}
function StageRow({ stage, label, now }) {
	const tone = stageTone(stage);
	return /* @__PURE__ */ (0, react_jsx_runtime.jsxs)("div", {
		className: "pc-stage",
		"data-pc-stage": stage.operation,
		"data-pc-stage-state": stage.state,
		children: [/* @__PURE__ */ (0, react_jsx_runtime.jsx)("span", {
			className: `pc-dot pc-dot-${tone}`,
			"aria-hidden": "true"
		}), /* @__PURE__ */ (0, react_jsx_runtime.jsxs)("div", {
			className: "pc-stage-main",
			children: [
				/* @__PURE__ */ (0, react_jsx_runtime.jsxs)("div", {
					className: "pc-stage-head",
					children: [/* @__PURE__ */ (0, react_jsx_runtime.jsx)("span", {
						className: "pc-stage-name",
						children: label
					}), /* @__PURE__ */ (0, react_jsx_runtime.jsx)("span", {
						className: "pc-stage-state",
						children: stageLabel(stage)
					})]
				}),
				stage.code !== void 0 && /* @__PURE__ */ (0, react_jsx_runtime.jsx)("p", {
					className: "pc-stage-message pc-mono",
					children: stage.code
				}),
				stage.message !== void 0 && /* @__PURE__ */ (0, react_jsx_runtime.jsx)("p", {
					className: "pc-stage-message",
					children: stage.message
				}),
				/* @__PURE__ */ (0, react_jsx_runtime.jsx)("p", {
					className: "pc-stage-meta",
					children: ago(stage.age_ms ?? (stage.observed_at === null ? null : now - Date.parse(stage.observed_at)), now)
				})
			]
		})]
	});
}
function Section({ title, note, children }) {
	return /* @__PURE__ */ (0, react_jsx_runtime.jsxs)("section", {
		className: "pc-section",
		"aria-label": title,
		children: [/* @__PURE__ */ (0, react_jsx_runtime.jsxs)("div", {
			className: "pc-section-head",
			children: [/* @__PURE__ */ (0, react_jsx_runtime.jsx)("h3", {
				className: "pc-section-title",
				children: title
			}), note !== void 0 && /* @__PURE__ */ (0, react_jsx_runtime.jsx)("span", {
				className: "pc-section-note",
				children: note
			})]
		}), children]
	});
}
function ConnectionSection({ data }) {
	const readinessDependencies = data.server.readiness.dependencies;
	return /* @__PURE__ */ (0, react_jsx_runtime.jsx)(Section, {
		title: "Connection",
		note: "read-only checks",
		children: /* @__PURE__ */ (0, react_jsx_runtime.jsxs)("div", {
			className: "pc-kv",
			children: [
				/* @__PURE__ */ (0, react_jsx_runtime.jsx)("span", {
					className: "pc-key",
					children: "Deployment"
				}),
				/* @__PURE__ */ (0, react_jsx_runtime.jsxs)("span", {
					className: "pc-value",
					children: [data.deployment.mode === "desktop" ? "DSH Desktop" : "Local Web", /* @__PURE__ */ (0, react_jsx_runtime.jsxs)("span", {
						className: "pc-muted",
						children: [" · ", data.deployment.owner_local === "explicit" ? "explicit owner config" : "automatic desktop signal"]
					})]
				}),
				/* @__PURE__ */ (0, react_jsx_runtime.jsx)("span", {
					className: "pc-key",
					children: "Endpoint"
				}),
				/* @__PURE__ */ (0, react_jsx_runtime.jsx)("span", {
					className: "pc-value pc-mono",
					children: data.configuration.endpoint_origin || "unavailable"
				}),
				/* @__PURE__ */ (0, react_jsx_runtime.jsx)("span", {
					className: "pc-key",
					children: "Endpoint source"
				}),
				/* @__PURE__ */ (0, react_jsx_runtime.jsxs)("span", {
					className: "pc-value",
					children: [data.configuration.endpoint_source, data.configuration.path_prefix ? " (with path prefix)" : ""]
				}),
				/* @__PURE__ */ (0, react_jsx_runtime.jsx)("span", {
					className: "pc-key",
					children: "Authentication"
				}),
				/* @__PURE__ */ (0, react_jsx_runtime.jsxs)("span", {
					className: "pc-value",
					children: [data.configuration.authorization_configured ? `Configured (${data.configuration.authorization_source})` : "Not configured", /* @__PURE__ */ (0, react_jsx_runtime.jsx)("span", {
						className: "pc-muted",
						children: " · value never displayed"
					})]
				}),
				/* @__PURE__ */ (0, react_jsx_runtime.jsx)("span", {
					className: "pc-key",
					children: "Liveness"
				}),
				/* @__PURE__ */ (0, react_jsx_runtime.jsxs)("span", {
					className: "pc-value",
					children: [probeLabel(data.server.liveness), data.server.liveness.code ? ` · ${data.server.liveness.code}` : ""]
				}),
				/* @__PURE__ */ (0, react_jsx_runtime.jsx)("span", {
					className: "pc-key",
					children: "Readiness"
				}),
				/* @__PURE__ */ (0, react_jsx_runtime.jsxs)("span", {
					className: "pc-value",
					children: [
						probeLabel(data.server.readiness),
						data.server.readiness.code ? ` · ${data.server.readiness.code}` : "",
						readinessDependencies !== void 0 && Object.keys(readinessDependencies).length > 0 && /* @__PURE__ */ (0, react_jsx_runtime.jsxs)("span", {
							className: "pc-muted",
							children: [
								" (",
								Object.entries(readinessDependencies).map(([name, value]) => `${name}=${value}`).join(", "),
								")"
							]
						})
					]
				}),
				/* @__PURE__ */ (0, react_jsx_runtime.jsx)("span", {
					className: "pc-key",
					children: "Required capability"
				}),
				/* @__PURE__ */ (0, react_jsx_runtime.jsx)("span", {
					className: "pc-value",
					children: data.server.capabilities.state === "unknown" ? "Not checked (liveness or readiness failed)" : data.capabilities.prepared_context ? "PreparedContext schema verified" : "PreparedContext schema missing"
				}),
				/* @__PURE__ */ (0, react_jsx_runtime.jsx)("span", {
					className: "pc-key",
					children: "Scope source"
				}),
				/* @__PURE__ */ (0, react_jsx_runtime.jsx)("span", {
					className: "pc-value",
					children: data.configuration.scope_source
				})
			]
		})
	});
}
function SessionSection({ data }) {
	if (data.session === null) return /* @__PURE__ */ (0, react_jsx_runtime.jsx)(Section, {
		title: "Scope and conversation",
		children: /* @__PURE__ */ (0, react_jsx_runtime.jsx)("p", {
			className: "pc-empty",
			children: "No active conversation. This panel reads status only; it does not create a conversation or change Scope."
		})
	});
	return /* @__PURE__ */ (0, react_jsx_runtime.jsx)(Section, {
		title: "Scope and conversation",
		children: /* @__PURE__ */ (0, react_jsx_runtime.jsxs)("div", {
			className: "pc-kv",
			children: [
				/* @__PURE__ */ (0, react_jsx_runtime.jsx)("span", {
					className: "pc-key",
					children: "Conversation"
				}),
				/* @__PURE__ */ (0, react_jsx_runtime.jsx)("span", {
					className: "pc-value pc-mono",
					children: data.session.id
				}),
				data.session.cwd !== void 0 && /* @__PURE__ */ (0, react_jsx_runtime.jsxs)(react_jsx_runtime.Fragment, { children: [/* @__PURE__ */ (0, react_jsx_runtime.jsx)("span", {
					className: "pc-key",
					children: "Workspace"
				}), /* @__PURE__ */ (0, react_jsx_runtime.jsx)("span", {
					className: "pc-value pc-mono",
					children: data.session.cwd
				})] }),
				/* @__PURE__ */ (0, react_jsx_runtime.jsx)("span", {
					className: "pc-key",
					children: "Active Scope"
				}),
				/* @__PURE__ */ (0, react_jsx_runtime.jsx)("span", {
					className: "pc-value pc-mono",
					children: data.session.scope_state === "resolved" && data.session.scope_id !== null ? data.session.scope_id : "unresolved"
				}),
				data.session.scope_state === "unresolved" && data.session.scope_message !== void 0 && /* @__PURE__ */ (0, react_jsx_runtime.jsxs)(react_jsx_runtime.Fragment, { children: [/* @__PURE__ */ (0, react_jsx_runtime.jsx)("span", {
					className: "pc-key",
					children: "Scope detail"
				}), /* @__PURE__ */ (0, react_jsx_runtime.jsxs)("span", {
					className: "pc-value",
					children: [data.session.scope_message, data.session.scope_code !== void 0 && /* @__PURE__ */ (0, react_jsx_runtime.jsxs)("span", {
						className: "pc-muted",
						children: [" · ", data.session.scope_code]
					})]
				})] })
			]
		})
	});
}
function RuntimeSection({ data, now }) {
	const runtime = data.runtime;
	return /* @__PURE__ */ (0, react_jsx_runtime.jsxs)(Section, {
		title: "Automatic path",
		note: runtime.freshness === "stale" ? "stale observation" : void 0,
		children: [
			runtime.freshness === "not_yet_observed" ? /* @__PURE__ */ (0, react_jsx_runtime.jsx)("p", {
				className: "pc-empty",
				children: "No automatic-path attempt has been observed for this conversation. Send a prompt in DSH, then refresh."
			}) : /* @__PURE__ */ (0, react_jsx_runtime.jsx)("div", {
				className: "pc-stage-list",
				children: STAGE_ROWS.map((row) => {
					const stage = runtime.stages[row.key];
					return stage === void 0 ? null : /* @__PURE__ */ (0, react_jsx_runtime.jsx)(StageRow, {
						stage,
						label: row.label,
						now
					}, row.key);
				})
			}),
			runtime.freshness === "stale" && runtime.stale_reason !== void 0 && /* @__PURE__ */ (0, react_jsx_runtime.jsxs)("p", {
				className: "pc-coverage",
				children: [
					"Stale reason: ",
					runtime.stale_reason,
					". The sidebar will not present this as a fresh connection."
				]
			}),
			runtime.coverage !== "" && /* @__PURE__ */ (0, react_jsx_runtime.jsx)("p", {
				className: "pc-coverage",
				children: runtime.coverage
			})
		]
	});
}
function CapabilitySection({ data }) {
	const memory = data.capabilities.memory_extraction;
	const handoff = data.capabilities.handoff_generation;
	return /* @__PURE__ */ (0, react_jsx_runtime.jsx)(Section, {
		title: "Server capabilities",
		children: /* @__PURE__ */ (0, react_jsx_runtime.jsxs)("div", {
			className: "pc-kv",
			children: [
				/* @__PURE__ */ (0, react_jsx_runtime.jsx)("span", {
					className: "pc-key",
					children: "Memory extraction"
				}),
				/* @__PURE__ */ (0, react_jsx_runtime.jsx)("span", {
					className: "pc-value",
					children: memory === null ? "Unknown" : memory ? "Configured" : "Disabled"
				}),
				/* @__PURE__ */ (0, react_jsx_runtime.jsx)("span", {
					className: "pc-key",
					children: "Handoff generation"
				}),
				/* @__PURE__ */ (0, react_jsx_runtime.jsx)("span", {
					className: "pc-value",
					children: handoff === null ? "Unknown" : handoff ? "Configured" : "Disabled"
				}),
				/* @__PURE__ */ (0, react_jsx_runtime.jsx)("span", {
					className: "pc-key",
					children: "Context versions"
				}),
				/* @__PURE__ */ (0, react_jsx_runtime.jsx)("span", {
					className: "pc-value pc-mono",
					children: data.capabilities.context_versions.length === 0 ? "unknown" : data.capabilities.context_versions.join(", ")
				})
			]
		})
	});
}
function Notices({ notices }) {
	const messages = notices.map((notice) => {
		switch (notice) {
			case "no_active_session": return "No active conversation; none was created.";
			case "session_unavailable": return "The selected conversation is no longer available to this host.";
			case "session_service_unavailable": return "DSH did not expose its session registry to the status bridge.";
			case "scope_unresolved": return "The current Scope could not be resolved; Scope-dependent work may be unavailable.";
			default: return notice;
		}
	});
	if (messages.length === 0) return null;
	return /* @__PURE__ */ (0, react_jsx_runtime.jsxs)("div", {
		className: "pc-alert",
		role: "status",
		"data-pc-notices": notices.join(","),
		children: [/* @__PURE__ */ (0, react_jsx_runtime.jsx)("div", {
			className: "pc-alert-title",
			children: "Attention"
		}), /* @__PURE__ */ (0, react_jsx_runtime.jsx)("p", {
			className: "pc-alert-text",
			children: messages.join(" ")
		})]
	});
}
/**
* The sidebar footer action and its non-modal diagnostics panel. All status
* values come from the authenticated Host bridge; the component never reads a
* credential, calls a model, or invokes `/pc doctor`.
*/
function PowerContextStatus(props) {
	const { wide, useSessions, useStatus, setSession, refresh } = props;
	const sessionId = useSessions((state$1) => activeSessionId(state$1));
	const snapshot = useStatus((value) => value);
	const [open, setOpen] = (0, react.useState)(false);
	const [clockTick, setClockTick] = (0, react.useState)(() => Date.now());
	const [anchor, setAnchor] = (0, react.useState)({
		left: 12,
		bottom: 12
	});
	const triggerRef = (0, react.useRef)(null);
	const panelRef = (0, react.useRef)(null);
	const panelId = (0, react.useId)();
	const now = Math.max(clockTick, snapshot.last_success_at ?? 0);
	const state = displayState(snapshot, now);
	const data = snapshot.data;
	(0, react.useEffect)(() => {
		setSession(sessionId);
	}, [sessionId, setSession]);
	(0, react.useEffect)(() => {
		if (!open) return;
		const timer = window.setInterval(() => {
			setClockTick(Date.now());
		}, 1e4);
		return () => {
			window.clearInterval(timer);
		};
	}, [open]);
	(0, react.useEffect)(() => {
		if (!open) return;
		const timer = window.setTimeout(() => {
			const panel = panelRef.current;
			if (panel === null || !panel.contains(document.activeElement)) panel?.focus({ preventScroll: true });
		}, 50);
		return () => {
			window.clearTimeout(timer);
		};
	}, [open]);
	(0, react.useLayoutEffect)(() => {
		if (!open) return;
		const place = () => {
			const rect = triggerRef.current?.getBoundingClientRect();
			if (rect === void 0) return;
			const width = Math.min(392, window.innerWidth - 24);
			setAnchor({
				left: Math.max(12, Math.min(rect.left, window.innerWidth - width - 12)),
				bottom: Math.max(12, window.innerHeight - rect.top + 8)
			});
		};
		place();
		window.addEventListener("resize", place);
		window.addEventListener("scroll", place, true);
		const frame = window.requestAnimationFrame(() => {
			const panel = panelRef.current;
			if (panel === null) return;
			panel.focus({ preventScroll: true });
			panel.querySelector("button:not([disabled])")?.focus({ preventScroll: true });
		});
		return () => {
			window.cancelAnimationFrame(frame);
			window.removeEventListener("resize", place);
			window.removeEventListener("scroll", place, true);
		};
	}, [open]);
	(0, react.useEffect)(() => {
		if (!open) return;
		const close$1 = () => {
			setOpen(false);
			triggerRef.current?.focus();
		};
		const onPointerDown = (event) => {
			const target = event.target;
			if (target instanceof Node && panelRef.current?.contains(target)) return;
			if (target instanceof Node && triggerRef.current?.contains(target)) return;
			close$1();
		};
		const onKeyDown = (event) => {
			if (event.key !== "Escape") return;
			event.preventDefault();
			event.stopPropagation();
			close$1();
		};
		document.addEventListener("pointerdown", onPointerDown, true);
		document.addEventListener("keydown", onKeyDown, true);
		return () => {
			document.removeEventListener("pointerdown", onPointerDown, true);
			document.removeEventListener("keydown", onKeyDown, true);
		};
	}, [open]);
	const close = () => {
		setOpen(false);
		triggerRef.current?.focus();
	};
	const onPanelKeyDown = (event) => {
		if (event.key !== "Tab") return;
		const focusable = [...panelRef.current?.querySelectorAll("button:not([disabled]), [href], input, select, textarea, [tabindex]:not([tabindex=\"-1\"])") ?? []];
		if (focusable.length === 0) return;
		const first = focusable[0];
		const last = focusable[focusable.length - 1];
		if (event.shiftKey && document.activeElement === first) {
			event.preventDefault();
			last.focus();
		} else if (!event.shiftKey && document.activeElement === last) {
			event.preventDefault();
			first.focus();
		}
	};
	const label = triggerLabel(state, snapshot);
	return /* @__PURE__ */ (0, react_jsx_runtime.jsxs)("div", {
		className: "pc-status",
		children: [/* @__PURE__ */ (0, react_jsx_runtime.jsxs)("button", {
			ref: triggerRef,
			type: "button",
			className: `pc-trigger${wide ? " pc-trigger-wide" : " pc-trigger-rail"}`,
			"aria-label": `PowerContext status: ${label}`,
			"aria-haspopup": "dialog",
			"aria-expanded": open,
			"aria-controls": open ? panelId : void 0,
			title: label,
			"data-pc-status-trigger": state,
			onClick: () => {
				setClockTick(Date.now());
				setOpen((value) => !value);
			},
			onKeyDown: (event) => {
				if (event.key !== "Enter" && event.key !== " ") return;
				event.preventDefault();
				setClockTick(Date.now());
				setOpen(true);
			},
			children: [
				/* @__PURE__ */ (0, react_jsx_runtime.jsxs)("span", {
					className: "pc-mark",
					children: [/* @__PURE__ */ (0, react_jsx_runtime.jsx)(PowerContextMark, { size: wide ? 20 : 18 }), /* @__PURE__ */ (0, react_jsx_runtime.jsx)(Dot, { state })]
				}),
				/* @__PURE__ */ (0, react_jsx_runtime.jsx)("span", {
					className: "pc-trigger-label",
					children: "PowerContext"
				}),
				wide && /* @__PURE__ */ (0, react_jsx_runtime.jsx)("span", {
					className: "pc-trigger-state",
					children: /* @__PURE__ */ (0, react_jsx_runtime.jsx)(StatusLabel, {
						state,
						snapshot
					})
				})
			]
		}), open && /* @__PURE__ */ (0, react_jsx_runtime.jsxs)("div", {
			ref: panelRef,
			id: panelId,
			className: "pc-panel",
			role: "dialog",
			"aria-modal": "false",
			"aria-labelledby": `${panelId}-title`,
			tabIndex: -1,
			style: {
				left: anchor.left,
				bottom: anchor.bottom
			},
			onKeyDown: onPanelKeyDown,
			"data-pc-status-panel": state,
			children: [
				/* @__PURE__ */ (0, react_jsx_runtime.jsxs)("header", {
					className: "pc-panel-head",
					children: [/* @__PURE__ */ (0, react_jsx_runtime.jsxs)("div", {
						className: "pc-brand",
						children: [/* @__PURE__ */ (0, react_jsx_runtime.jsx)("span", {
							className: "pc-mark",
							children: /* @__PURE__ */ (0, react_jsx_runtime.jsx)(PowerContextMark, { size: 22 })
						}), /* @__PURE__ */ (0, react_jsx_runtime.jsxs)("div", { children: [/* @__PURE__ */ (0, react_jsx_runtime.jsx)("h2", {
							className: "pc-title",
							id: `${panelId}-title`,
							children: "PowerContext"
						}), /* @__PURE__ */ (0, react_jsx_runtime.jsx)("div", {
							className: "pc-subtitle",
							children: "Connection diagnostics"
						})] })]
					}), /* @__PURE__ */ (0, react_jsx_runtime.jsxs)("div", {
						className: "pc-actions",
						children: [/* @__PURE__ */ (0, react_jsx_runtime.jsx)("button", {
							type: "button",
							className: "pc-icon-button",
							"aria-label": "Refresh status",
							title: "Refresh status",
							disabled: snapshot.loading,
							onClick: () => {
								setClockTick(Date.now());
								refresh();
							},
							children: "↻"
						}), /* @__PURE__ */ (0, react_jsx_runtime.jsx)("button", {
							type: "button",
							className: "pc-icon-button",
							"aria-label": "Close diagnostics",
							title: "Close",
							onClick: close,
							children: "×"
						})]
					})]
				}),
				/* @__PURE__ */ (0, react_jsx_runtime.jsxs)("div", {
					className: "pc-panel-body",
					children: [
						/* @__PURE__ */ (0, react_jsx_runtime.jsxs)("div", {
							className: `pc-summary pc-summary-${state}`,
							role: "status",
							"aria-live": "polite",
							"aria-busy": snapshot.loading,
							children: [
								/* @__PURE__ */ (0, react_jsx_runtime.jsx)(Dot, { state }),
								/* @__PURE__ */ (0, react_jsx_runtime.jsxs)("div", {
									className: "pc-summary-copy",
									children: [/* @__PURE__ */ (0, react_jsx_runtime.jsx)("div", {
										className: "pc-summary-label",
										children: /* @__PURE__ */ (0, react_jsx_runtime.jsx)(StatusLabel, {
											state,
											snapshot
										})
									}), /* @__PURE__ */ (0, react_jsx_runtime.jsx)("div", {
										className: "pc-summary-detail",
										children: summaryDetail(state, snapshot)
									})]
								}),
								/* @__PURE__ */ (0, react_jsx_runtime.jsx)("span", {
									className: "pc-summary-time",
									children: snapshot.last_success_at === null ? "not checked" : clock(data?.observed_at ?? null)
								})
							]
						}),
						snapshot.error !== null && (state === "failed" ? /* @__PURE__ */ (0, react_jsx_runtime.jsxs)("div", {
							className: "pc-alert",
							role: "alert",
							children: [
								/* @__PURE__ */ (0, react_jsx_runtime.jsx)("div", {
									className: "pc-alert-title",
									children: "Status read failed"
								}),
								/* @__PURE__ */ (0, react_jsx_runtime.jsxs)("p", {
									className: "pc-alert-text",
									children: [
										/* @__PURE__ */ (0, react_jsx_runtime.jsx)("span", {
											className: "pc-mono",
											children: snapshot.error.code
										}),
										" · ",
										snapshot.error.message
									]
								}),
								snapshot.error.recovery !== void 0 && /* @__PURE__ */ (0, react_jsx_runtime.jsx)("p", {
									className: "pc-alert-text",
									children: snapshot.error.recovery
								}),
								/* @__PURE__ */ (0, react_jsx_runtime.jsx)("div", {
									className: "pc-alert-actions",
									children: /* @__PURE__ */ (0, react_jsx_runtime.jsx)("button", {
										type: "button",
										className: "pc-text-button",
										onClick: () => {
											setClockTick(Date.now());
											refresh();
										},
										children: "Retry"
									})
								})
							]
						}) : /* @__PURE__ */ (0, react_jsx_runtime.jsxs)("div", {
							className: "pc-empty",
							role: "status",
							"data-pc-status-unconfigured": snapshot.error.code,
							children: [snapshot.error.recovery, /* @__PURE__ */ (0, react_jsx_runtime.jsx)("div", {
								className: "pc-alert-actions",
								children: /* @__PURE__ */ (0, react_jsx_runtime.jsx)("button", {
									type: "button",
									className: "pc-text-button",
									onClick: () => {
										setClockTick(Date.now());
										refresh();
									},
									children: "Retry"
								})
							})]
						})),
						data !== null && /* @__PURE__ */ (0, react_jsx_runtime.jsxs)(react_jsx_runtime.Fragment, { children: [
							/* @__PURE__ */ (0, react_jsx_runtime.jsx)(Notices, { notices: data.notices }),
							/* @__PURE__ */ (0, react_jsx_runtime.jsx)(ConnectionSection, { data }),
							/* @__PURE__ */ (0, react_jsx_runtime.jsx)(SessionSection, { data }),
							/* @__PURE__ */ (0, react_jsx_runtime.jsx)(RuntimeSection, {
								data,
								now
							}),
							/* @__PURE__ */ (0, react_jsx_runtime.jsx)(CapabilitySection, { data })
						] }),
						data === null && snapshot.error === null && /* @__PURE__ */ (0, react_jsx_runtime.jsx)("p", {
							className: "pc-empty",
							children: "Reading the local status bridge. No model request is made."
						})
					]
				}),
				/* @__PURE__ */ (0, react_jsx_runtime.jsxs)("footer", {
					className: "pc-panel-foot",
					children: [/* @__PURE__ */ (0, react_jsx_runtime.jsx)("span", { children: "Local DSH owner only · read-only checks" }), /* @__PURE__ */ (0, react_jsx_runtime.jsx)("span", {
						className: "pc-mono",
						children: data?.plugin_version ?? "checking"
					})]
				})
			]
		})]
	});
}
function installStyles(ctx) {
	if (typeof document === "undefined") return;
	ctx.effect(() => {
		document.getElementById(STYLE_ID)?.remove();
		const style = document.createElement("style");
		style.id = STYLE_ID;
		style.textContent = STATUS_STYLES;
		document.head.append(style);
		return () => {
			style.remove();
		};
	}, "powercontext: sidebar status styles");
}
/**
* Register the optional sidebar entry. The sidebar slot owner controls when the
* component mounts; unloading this plugin disposes the slot cell, styles, and
* the controller's timers.
*/
function apply(ctx) {
	installStyles(ctx);
	const controller = new StatusController();
	ctx.effect(() => () => controller.dispose(), "powercontext: status controller disposal");
	ctx.slots.inject("sidebar.footer.action", () => ctx.slots.register({
		name: "sidebar.footer.action",
		id: "powercontext-status",
		order: 40,
		inject: () => ({
			hooks: { status: controller },
			setSession: (sessionId) => controller.setSession(sessionId),
			refresh: () => controller.refreshNow()
		})
	}, PowerContextStatus));
}

//#endregion
exports.PowerContextStatus = PowerContextStatus;
exports.apply = apply;
exports.inject = inject;
		})(module, exports, require);
		return module.exports;
	}
});
