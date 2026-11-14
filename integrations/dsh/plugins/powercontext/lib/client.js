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
let __deepseek_ai_dsh_client_ui_primitives = require("@deepseek-ai/dsh-client-ui-primitives");

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
			package_version: packageVersion(server.package_version),
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
/** Only a bounded package version may cross the Host/browser allowlist. */
function packageVersion(value) {
	return typeof value === "string" && /^[0-9][0-9A-Za-z.+_-]{0,127}$/.test(value) ? value : null;
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
//#region src/ui/locale.ts
const LOCALE_KEY = "powercontext.dsh.locale";
let sessionLocale;
const LOCALE_CHANGED = "powercontext-dsh-locale-changed";
const texts = {
	"zh-CN": {
		"status": "状态",
		"diagnostics": "连接诊断",
		"dashboard": "Dashboard",
		"dashboardIntro": "在右侧面板查看现有 PowerContext 页面。",
		"dashboardOpen": "打开 Dashboard",
		"dashboardChecking": "正在检查 Dashboard…",
		"dashboardNoSession": "暂无活动会话。可在外部浏览器查看 Dashboard；打开已有会话后再从这里进入右侧面板。",
		"dashboardExternal": "在浏览器中打开",
		"dashboardBridgeUnavailable": "无法检查 Dashboard。请确认 DSH 登录和插件 Host 接口后重试。",
		"dashboardContainerUnavailable": "右侧 Browser 容器不可用。请确认当前会话和 DSH Browser 插件已启用。",
		"dashboardLoopbackMismatch": "DSH Web 和 Dashboard 的本地地址不匹配，右侧面板无法保持登录。请使用相同的协议和主机名（例如都用 127.0.0.1，端口可以不同）。",
		"dashboardDisabled": "Dashboard 未开启。请在 Server 配置中明确启用后重试；插件不会自动修改配置或重启服务。",
		"dashboardAuthentication": "Dashboard 认证未通过。请检查 Server token；右侧页面的登录仍需手动完成。",
		"dashboardServerUnavailable": "Server 或 Dashboard 路由不可达。检查 Server 启动日志；自定义认证 Provider 与 Dashboard 同时启用的组合不受支持，需由配置和日志确认。",
		"dashboardLoadUnavailable": "Dashboard 返回了异常页面或状态。请检查 Server 和代理配置。",
		"dashboardSessionUnavailable": "所选会话已不可用，请切换到有效会话后重试。",
		"dashboardScopeUnresolved": "当前会话的 Scope 尚未解析。请检查工作目录绑定后重试。",
		"language": "切换语言",
		"refresh": "刷新状态",
		"close": "关闭诊断",
		"retry": "重试",
		"ready": "已连接",
		"failed": "需要处理",
		"connecting": "连接中",
		"unconfigured": "未配置",
		"stale": "状态已过期，重试中",
		"auth": "需要认证",
		"connectingHelp": "正在检查 PowerContext Server。",
		"staleHelp": "正在重新检查连接。",
		"unconfiguredHelp": "请检查本机插件配置。",
		"authHelp": "请检查 Server 认证配置；若 DSH 登录已过期，请重新登录后重试。",
		"readinessHelp": "Server 尚未就绪，请检查服务日志与详情中的依赖状态。",
		"networkHelp": "无法连接 Server，请检查服务是否启动以及地址和网络。",
		"timeoutHelp": "Server 响应超时，请检查服务负载并稍后刷新。",
		"protocolHelp": "Server 响应格式不兼容，请检查 Server 与插件版本及代理配置。",
		"forbiddenHelp": "当前认证没有访问权限，请检查 Server 授权配置。",
		"failureHelp": "请确认 Server 正在运行并检查地址与配置，随后刷新。可运行 /pc doctor 查看诊断。",
		"bridgeHelp": "请使用本机 DSH 所有者会话，并检查插件是否已启用。仅在自己的本地 Web 主机配置 ownerLocal。",
		"schemaHelp": "请安装相互兼容的 PowerContext Server 和 DSH 插件版本。",
		"notChecked": "尚未检查",
		"server": "服务器",
		"address": "地址",
		"serverVersion": "PowerContext Server 版本",
		"unknown": "未知",
		"session": "会话与 Scope",
		"conversation": "当前会话",
		"workspace": "工作目录",
		"scope": "当前 Scope",
		"unresolved": "未解析",
		"noSession": "暂无活动会话。",
		"scopeHelp": "无法解析当前 Scope，请检查工作目录绑定或运行 /pc doctor。",
		"details": "技术详情",
		"connection": "连接检查",
		"deployment": "运行环境",
		"explicit": "显式本机配置",
		"automatic": "自动处理链路",
		"automaticOwner": "自动识别 Desktop",
		"reason": "诊断信息",
		"endpointSource": "地址来源",
		"pathPrefix": "（含路径前缀）",
		"authentication": "认证",
		"configured": "已配置",
		"disabled": "已禁用",
		"liveness": "存活检查",
		"readiness": "就绪检查",
		"capabilities": "能力检查",
		"dependencies": "就绪依赖",
		"scopeSource": "Scope 来源",
		"schema": "上下文协议",
		"memory": "记忆提取",
		"handoff": "交接生成",
		"ok": "已验证",
		"noObservation": "尚未观察到本会话的自动处理；发送消息后刷新。",
		"prepare": "召回",
		"capture": "Source 捕获",
		"flush": "记忆处理",
		"injection": "上下文注入",
		"not_yet_observed": "尚未观察",
		"running": "处理中",
		"resolved": "已解析",
		"empty": "无内容",
		"accepted": "已接收",
		"completed": "已完成",
		"incomplete": "未完成",
		"appended": "已注入",
		"skipped": "已跳过",
		"unavailable": "不可用",
		"operationHelp": "请运行 /pc doctor 检查配置与服务状态。",
		"coverage": "本地观察时间不代表记忆新鲜度。Source 接收与处理进度不证明记忆已生成；注入仅表示加入消息，不证明模型已使用。未确认的写入可能已生效，失败也不会回滚之前的工作。使用 /pc doctor 检查当前配置。",
		"no_active_session": "暂无活动会话。",
		"session_unavailable": "所选会话已不可用。",
		"session_service_unavailable": "主机未提供会话列表。",
		"scope_unresolved": "当前 Scope 未解析。"
	},
	"en": {
		"status": "status",
		"diagnostics": "Connection diagnostics",
		"dashboard": "Dashboard",
		"dashboardIntro": "View the existing PowerContext pages in the right panel.",
		"dashboardOpen": "Open Dashboard",
		"dashboardChecking": "Checking Dashboard…",
		"dashboardNoSession": "No active conversation. You can view Dashboard in a separate browser; open an existing conversation to use the right panel here.",
		"dashboardExternal": "Open in browser",
		"dashboardBridgeUnavailable": "Could not check Dashboard. Verify the DSH sign-in and plugin Host route, then retry.",
		"dashboardContainerUnavailable": "The right-panel Browser is unavailable. Check the current conversation and that the DSH Browser plugin is enabled.",
		"dashboardLoopbackMismatch": "The local addresses differ, so Dashboard sign-in cannot persist in the DSH Web panel. Use the same scheme and hostname for both (for example, 127.0.0.1). Ports may differ.",
		"dashboardDisabled": "Dashboard is off. Enable it explicitly in Server configuration, then retry; the plugin does not change configuration or restart Server.",
		"dashboardAuthentication": "Dashboard authentication failed. Check the Server token; sign-in in the right panel remains manual.",
		"dashboardServerUnavailable": "The Server or Dashboard route is unreachable. Check Server startup logs. Dashboard with a custom authentication provider is unsupported; confirm that cause from configuration and logs.",
		"dashboardLoadUnavailable": "Dashboard returned an unexpected page or status. Check Server and proxy configuration.",
		"dashboardSessionUnavailable": "The selected conversation is unavailable. Select a valid conversation and retry.",
		"dashboardScopeUnresolved": "The current conversation's Scope is unresolved. Check its workspace binding, then retry.",
		"language": "Change language",
		"refresh": "Refresh status",
		"close": "Close diagnostics",
		"retry": "Retry",
		"ready": "Connected",
		"failed": "Action needed",
		"connecting": "Connecting",
		"unconfigured": "Not configured",
		"stale": "Status stale, retrying",
		"auth": "Authentication required",
		"connectingHelp": "Checking the PowerContext Server.",
		"staleHelp": "Rechecking the connection.",
		"unconfiguredHelp": "Check the local plugin configuration.",
		"authHelp": "Check Server authentication; if the DSH session expired, sign in again and retry.",
		"readinessHelp": "The Server is not ready. Check its logs and the dependency states in details.",
		"networkHelp": "Cannot reach the Server. Check that it is running and verify its address and network.",
		"timeoutHelp": "The Server timed out. Check its load and refresh again shortly.",
		"protocolHelp": "The Server response is incompatible. Check Server/plugin versions and proxy configuration.",
		"forbiddenHelp": "This credential lacks access. Check Server authorization configuration.",
		"failureHelp": "Check that the Server is running and its address and configuration are correct, then refresh. Run /pc doctor for diagnostics.",
		"bridgeHelp": "Use the local DSH owner session and check that the plugin is enabled. Configure ownerLocal only on your own local Web host.",
		"schemaHelp": "Install compatible PowerContext Server and DSH plugin versions.",
		"notChecked": "Not checked",
		"server": "Server",
		"address": "Address",
		"serverVersion": "PowerContext Server version",
		"unknown": "Unknown",
		"session": "Conversation and Scope",
		"conversation": "Conversation",
		"workspace": "Workspace",
		"scope": "Active Scope",
		"unresolved": "Unresolved",
		"noSession": "No active conversation.",
		"scopeHelp": "Unable to resolve Scope. Check the workspace binding or run /pc doctor.",
		"details": "Technical details",
		"connection": "Connection checks",
		"deployment": "Deployment",
		"explicit": "explicit owner config",
		"automatic": "Automatic path",
		"automaticOwner": "automatic desktop signal",
		"reason": "Diagnostic information",
		"endpointSource": "Endpoint source",
		"pathPrefix": " (with path prefix)",
		"authentication": "Authentication",
		"configured": "Configured",
		"disabled": "Disabled",
		"liveness": "Liveness",
		"readiness": "Readiness",
		"capabilities": "Capabilities",
		"dependencies": "Readiness dependencies",
		"scopeSource": "Scope source",
		"schema": "Context schema",
		"memory": "Memory extraction",
		"handoff": "Handoff generation",
		"ok": "Verified",
		"noObservation": "No automatic attempt observed for this conversation. Send a prompt, then refresh.",
		"prepare": "Recall",
		"capture": "Source capture",
		"flush": "Memory flush",
		"injection": "Context injection",
		"not_yet_observed": "Not yet observed",
		"running": "Running",
		"resolved": "Resolved",
		"empty": "Empty",
		"accepted": "Accepted",
		"completed": "Completed",
		"incomplete": "Incomplete",
		"appended": "Injected",
		"skipped": "Skipped",
		"unavailable": "Unavailable",
		"operationHelp": "Run /pc doctor to check configuration and service status.",
		"coverage": "Local observation age is not Memory freshness. Source acceptance and flush progress do not prove Memory production. Injection means appended messages, not model consumption. Unconfirmed writes may have taken effect; failures do not roll back earlier work. Use /pc doctor to check current configuration.",
		"no_active_session": "No active conversation.",
		"session_unavailable": "The selected conversation is no longer available.",
		"session_service_unavailable": "The host did not expose its session registry.",
		"scope_unresolved": "The current Scope is unresolved."
	}
};
function readLocale() {
	if (sessionLocale) return sessionLocale;
	try {
		return localStorage.getItem(LOCALE_KEY) === "en" ? "en" : "zh-CN";
	} catch {
		return "zh-CN";
	}
}
function saveLocale(locale) {
	try {
		localStorage.setItem(LOCALE_KEY, locale);
		sessionLocale = void 0;
	} catch {
		sessionLocale = locale;
	}
	if (typeof window !== "undefined") window.dispatchEvent(new Event(LOCALE_CHANGED));
}
function subscribeLocale(onChange) {
	if (typeof window === "undefined") return () => {};
	const onStorage = (event) => {
		if (event.key === null || event.key === LOCALE_KEY) onChange();
	};
	window.addEventListener(LOCALE_CHANGED, onChange);
	window.addEventListener("storage", onStorage);
	return () => {
		window.removeEventListener(LOCALE_CHANGED, onChange);
		window.removeEventListener("storage", onStorage);
	};
}
function failureTextKey(code, status$1) {
	if (status$1 === 401 || /authentication|unauthorized/.test(code ?? "")) return "authHelp";
	if (status$1 === 403) return "forbiddenHelp";
	if (code === "unsupported_context_schema" || code === "version_mismatch") return "schemaHelp";
	if (code === "not_ready" || code === "degraded") return "readinessHelp";
	if (/timeout/.test(code ?? "")) return "timeoutHelp";
	if (/connection|unreachable|dns|tls/.test(code ?? "")) return "networkHelp";
	if (/invalid_response|invalid_json|protocol/.test(code ?? "")) return "protocolHelp";
	return "failureHelp";
}

//#endregion
//#region src/ui/styles.ts
/**
* Sidebar status and diagnostics styles. Every surface reads DSH's semantic
* theme tokens, so light/dark and collapsed/rail layouts share one rule set.
*/
const STATUS_STYLES = `
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
`;

//#endregion
//#region src/ui/brand.tsx
function PowerContextMark({ size = 20 }) {
	return /* @__PURE__ */ (0, react_jsx_runtime.jsxs)("span", {
		className: "pc-brand-icon",
		style: {
			width: size,
			height: size
		},
		"aria-hidden": "true",
		children: [/* @__PURE__ */ (0, react_jsx_runtime.jsx)("img", {
			className: "pc-brand-icon-color",
			src: "/plugins/powercontext-dsh/assets/powercontext.svg",
			width: size,
			height: size,
			alt: ""
		}), /* @__PURE__ */ (0, react_jsx_runtime.jsx)("img", {
			className: "pc-brand-icon-reverse",
			src: "/plugins/powercontext-dsh/assets/powercontext-reverse.svg",
			width: size,
			height: size,
			alt: ""
		})]
	});
}

//#endregion
//#region src/update-contract.ts
const UPDATE_ROUTE = "/powercontext/updates";
const UPDATE_ACTION_HEADER = "x-powercontext-update-action";
const UPDATE_PLAN_HEADER = "x-powercontext-update-plan";
const RELEASES_URL = "https://github.com/oceanbase/powercontext/releases";
const UPDATE_PHASES = [
	"idle",
	"checking",
	"current",
	"available",
	"manual",
	"server_first",
	"artifact_unavailable",
	"incompatible",
	"held",
	"check_failed",
	"installing",
	"restart_required",
	"updated",
	"restored",
	"install_failed",
	"busy",
	"unconfirmed"
];
const SERVER_UPDATE_STATES = [
	"unknown",
	"current",
	"available",
	"artifact_unavailable"
];
/** Releases use the same numeric version in Python and npm, with different RC spellings. */
function releaseVersion(value) {
	if (typeof value !== "string" || value.length > 64) return null;
	const parts = /^(0|[1-9]\d{0,8})\.(0|[1-9]\d{0,8})\.(0|[1-9]\d{0,8})(?:(?:(a|b|rc)|-(alpha|beta|rc)\.)(0|[1-9]\d{0,8}))?$/.exec(value);
	if (!parts) return null;
	const stage = parts[4] ?? {
		alpha: "a",
		beta: "b",
		rc: "rc"
	}[parts[5]];
	const core = parts.slice(1, 4).join(".");
	const npmStage = stage === "a" ? "alpha" : stage === "b" ? "beta" : stage;
	return {
		python: stage ? core + stage + parts[6] : core,
		npm: stage ? core + "-" + npmStage + "." + parts[6] : core,
		prerelease: Boolean(stage),
		order: [
			...parts.slice(1, 4).map(Number),
			stage === "a" ? 0 : stage === "b" ? 1 : stage === "rc" ? 2 : 3,
			Number(parts[6] ?? 0)
		]
	};
}
function parseUpdateSnapshot(value) {
	if (typeof value !== "object" || value === null) return null;
	const item = value;
	if (!UPDATE_PHASES.includes(item.phase) || !SERVER_UPDATE_STATES.includes(item.server_state)) return null;
	const version = (v) => typeof v === "string" && /^[\da-zA-Z.+-]{1,64}$/.test(v) ? v : null;
	return {
		phase: item.phase,
		plugin_version: version(item.plugin_version),
		plugin_target: releaseVersion(item.plugin_target)?.npm ?? null,
		server_target: releaseVersion(item.server_target)?.python ?? null,
		server_state: item.server_state,
		plan: typeof item.plan === "string" && /^[a-f0-9-]{36}$/.test(item.plan) ? item.plan : null,
		release_tag: typeof item.release_tag === "string" && /^powercontext-v[\da-z.]{1,64}$/.test(item.release_tag) ? item.release_tag : null
	};
}

//#endregion
//#region src/ui/update-panel.tsx
const DOCS = "https://powercontext.oceanbase.io";
const labels = {
	"zh-CN": {
		version: "版本：",
		check: "检查更新",
		update: "更新",
		updateTo: "更新插件至",
		notes: "发布说明",
		serverGuide: "Server 升级指南",
		pluginGuide: "插件更新指南",
		idle: "",
		checking: "正在检查发布版本…",
		current: "插件已是最新版本。",
		available: "安装后可能需要重新加载或重启 DSH。",
		manual: "此安装方式需要手动更新，请保留原安装渠道。",
		server_first: "请先确认连接的 Server 已升级到匹配版本。",
		artifact_unavailable: "所用渠道尚无对应的插件包，请稍后再查。",
		incompatible: "此版本不适用于当前 DSH，请查看发布说明。",
		held: "已锁定插件更新，请检查插件配置。",
		check_failed: "检查未完成，请检查网络或重新登录 DSH 后重试。",
		plan_changed: "更新检查已过期或安装状态已变化，请重新检查。",
		installing: "DSH 正在后台安装插件…",
		restart_required: "已安装，请重新加载或重启 DSH 以启用新版本。",
		updated: "已确认新插件版本生效。",
		restored: "安装未完成，已恢复原插件版本。",
		install_failed: "安装失败，请在 DSH 的插件设置中检查当前安装。",
		busy: "DSH 仍有会话在运行，请在会话结束后重试。",
		unconfirmed: "安装结果未确认，请先在 DSH 的插件设置中检查版本。",
		serverMissing: "发布包尚不可用"
	},
	en: {
		version: "Version: ",
		check: "Check for updates",
		update: "Update",
		updateTo: "Update plugin to",
		notes: "Release notes",
		serverGuide: "Server upgrade guide",
		pluginGuide: "Plugin update guide",
		idle: "",
		checking: "Checking releases…",
		current: "The plugin is up to date.",
		available: "A DSH reload or restart may be needed after installation.",
		manual: "Update manually using the original installation source.",
		server_first: "First confirm the connected Server is on the matching version.",
		artifact_unavailable: "The plugin package is unavailable on this channel. Check again later.",
		incompatible: "This release cannot run on this DSH version. See the release notes.",
		held: "Plugin updates are on hold. Check the plugin configuration.",
		check_failed: "The check did not finish. Check the network or sign in to DSH again.",
		plan_changed: "The check expired or the installation changed. Check again.",
		installing: "DSH is installing the plugin in the background…",
		restart_required: "Installed. Reload or restart DSH to use the new version.",
		updated: "The new plugin version is active.",
		restored: "Installation did not complete. The previous plugin was restored.",
		install_failed: "Installation failed. Check the current installation in DSH Plugins.",
		busy: "A DSH session is running. Try again when it finishes.",
		unconfirmed: "The install result is unknown. Check the version in DSH Plugins before retrying.",
		serverMissing: "Release package unavailable"
	}
};
async function requestUpdate(signal, action, plan) {
	try {
		const headers = { accept: "application/json" };
		if (action) headers[UPDATE_ACTION_HEADER] = action;
		if (plan) headers[UPDATE_PLAN_HEADER] = plan;
		const response = await fetch(UPDATE_ROUTE, {
			method: action ? "POST" : "GET",
			headers,
			signal,
			credentials: "same-origin",
			cache: "no-store",
			redirect: "error"
		});
		return {
			status: response.status,
			snapshot: response.ok ? parseUpdateSnapshot(await response.json()) : null
		};
	} catch {
		return { snapshot: null };
	}
}
/** Read the Host snapshot only while the status panel is open. */
function useVersionUpdates(open) {
	const [snapshot, setSnapshot] = (0, react.useState)(null);
	const [sending, setSending] = (0, react.useState)(false);
	const [notice, setNotice] = (0, react.useState)(null);
	const pending = (0, react.useRef)(null);
	const phase = snapshot?.phase ?? "idle";
	const working = phase === "checking" || phase === "installing";
	(0, react.useEffect)(() => () => pending.current?.abort(), []);
	(0, react.useEffect)(() => {
		if (open) return;
		pending.current?.abort();
		pending.current = null;
		setSending(false);
	}, [open]);
	(0, react.useEffect)(() => {
		if (!open || sending) return;
		const lifetime = new AbortController();
		let timer;
		let failures = 0;
		const poll = async () => {
			if (document.hidden) {
				timer = setTimeout(() => {
					poll();
				}, 15e3);
				return;
			}
			const { snapshot: state } = await requestUpdate(AbortSignal.any([lifetime.signal, AbortSignal.timeout(5e3)]));
			if (lifetime.signal.aborted) return;
			if (state) {
				setSnapshot(state);
				if (state.phase !== "idle") setNotice(null);
				failures = 0;
			} else if (working && ++failures >= 8) {
				setNotice(phase === "installing" ? "unconfirmed" : "check_failed");
				setSnapshot(null);
			}
			const fast = state?.phase === "checking" || state?.phase === "installing" || !state && working && failures < 8;
			timer = setTimeout(() => {
				poll();
			}, fast ? failures > 0 ? 2e3 : 1e3 : 15e3);
		};
		poll();
		return () => {
			lifetime.abort();
			clearTimeout(timer);
		};
	}, [
		open,
		sending,
		working,
		phase
	]);
	const submit = async () => {
		if (!open || pending.current || working || phase === "restart_required") return;
		const plan = phase === "available" && snapshot?.plan && snapshot.plugin_target ? snapshot.plan : void 0;
		const lifetime = new AbortController();
		pending.current = lifetime;
		setSending(true);
		setNotice(null);
		const result = await requestUpdate(AbortSignal.any([lifetime.signal, AbortSignal.timeout(6e3)]), plan ? "install" : "check", plan);
		if (lifetime.signal.aborted) return;
		pending.current = null;
		setSending(false);
		if (result.snapshot) setSnapshot(result.snapshot);
		else {
			setSnapshot(null);
			setNotice(result.status === 409 ? "plan_changed" : plan ? "unconfirmed" : "check_failed");
		}
	};
	return {
		snapshot,
		phase,
		notice,
		busy: sending || working,
		submit
	};
}
function VersionFooter({ locale, pluginVersion, updates }) {
	const text$1 = labels[locale];
	const description = (0, react.useId)();
	const { snapshot, phase, notice, busy, submit } = updates;
	const target = phase === "available" && snapshot?.plan ? snapshot.plugin_target : null;
	const actionLabel = target ? text$1.updateTo + " " + target : text$1.check;
	const message = !notice && phase === "server_first" && snapshot?.plugin_target ? text$1.server_first + " (" + snapshot.plugin_target + ")" : text$1[notice ?? phase];
	const version = snapshot?.plugin_version ?? pluginVersion ?? texts[locale].unknown;
	const release = snapshot?.release_tag ? RELEASES_URL + "/tag/" + snapshot.release_tag : RELEASES_URL;
	return /* @__PURE__ */ (0, react_jsx_runtime.jsxs)("footer", {
		className: "pc-panel-foot",
		"data-pc-update-state": notice ?? phase,
		children: [
			/* @__PURE__ */ (0, react_jsx_runtime.jsx)("a", {
				className: "pc-release-link",
				href: release,
				target: "_blank",
				rel: "noreferrer",
				children: text$1.notes
			}),
			/* @__PURE__ */ (0, react_jsx_runtime.jsxs)("div", {
				className: "pc-version-actions",
				children: [/* @__PURE__ */ (0, react_jsx_runtime.jsx)("button", {
					className: target ? "pc-update-button" : "pc-icon-button pc-update-check",
					type: "button",
					"aria-label": actionLabel,
					"aria-describedby": description,
					"aria-busy": busy,
					title: message ? actionLabel + " · " + message : actionLabel,
					disabled: busy || phase === "restart_required",
					onClick: () => {
						submit();
					},
					children: target ? text$1.update : /* @__PURE__ */ (0, react_jsx_runtime.jsx)("svg", {
						width: "15",
						height: "15",
						viewBox: "0 0 16 16",
						fill: "none",
						stroke: "currentColor",
						strokeWidth: "1.4",
						strokeLinecap: "round",
						strokeLinejoin: "round",
						"aria-hidden": "true",
						children: /* @__PURE__ */ (0, react_jsx_runtime.jsx)("path", { d: "M13.5 3v4h-4M13.2 6.5a5.4 5.4 0 1 0-.4 4.4" })
					})
				}), /* @__PURE__ */ (0, react_jsx_runtime.jsxs)("span", {
					className: "pc-running-version",
					title: text$1.version + version,
					children: [text$1.version, /* @__PURE__ */ (0, react_jsx_runtime.jsx)("span", {
						className: "pc-mono",
						"data-pc-plugin-version": true,
						children: version
					})]
				})]
			}),
			/* @__PURE__ */ (0, react_jsx_runtime.jsx)("span", {
				className: "pc-sr-only",
				id: description,
				role: "status",
				children: message
			})
		]
	});
}
function UpdateDetails({ locale, serverVersion, updates }) {
	const text$1 = labels[locale];
	const { snapshot, phase } = updates;
	const docs = DOCS + (locale === "zh-CN" ? "/zh" : "/en");
	const anchor = locale === "zh-CN" ? "#更新或替换安装" : "#update-or-replace-an-installation";
	const serverTarget = snapshot?.server_target;
	return /* @__PURE__ */ (0, react_jsx_runtime.jsxs)(react_jsx_runtime.Fragment, { children: [
		/* @__PURE__ */ (0, react_jsx_runtime.jsx)("span", {
			className: "pc-key",
			children: texts[locale].serverVersion
		}),
		/* @__PURE__ */ (0, react_jsx_runtime.jsx)("span", {
			className: "pc-value pc-mono",
			"data-pc-server-version": true,
			children: serverVersion ?? texts[locale].unknown
		}),
		/* @__PURE__ */ (0, react_jsx_runtime.jsxs)("div", {
			className: "pc-update-guide",
			children: [
				serverTarget && /* @__PURE__ */ (0, react_jsx_runtime.jsxs)("span", {
					className: "pc-mono",
					children: [serverTarget, " · "]
				}),
				/* @__PURE__ */ (0, react_jsx_runtime.jsx)("a", {
					className: "pc-release-link",
					href: docs + "/docs/get-started/install-and-run" + anchor,
					target: "_blank",
					rel: "noreferrer",
					children: text$1.serverGuide
				}),
				snapshot?.server_state === "artifact_unavailable" && /* @__PURE__ */ (0, react_jsx_runtime.jsxs)("span", { children: [" · ", text$1.serverMissing] })
			]
		}),
		phase === "manual" && /* @__PURE__ */ (0, react_jsx_runtime.jsx)("div", {
			className: "pc-update-guide",
			children: /* @__PURE__ */ (0, react_jsx_runtime.jsx)("a", {
				className: "pc-release-link",
				href: docs + "/docs/integrations/dsh",
				target: "_blank",
				rel: "noreferrer",
				children: text$1.pluginGuide
			})
		})
	] });
}

//#endregion
//#region src/dashboard-contract.ts
const DASHBOARD_ROUTE = "/powercontext/dashboard";
const DASHBOARD_SCHEMA = "powercontext.dsh-dashboard.v1";
const STATES = new Set([
	"available",
	"disabled",
	"authentication",
	"server_unavailable",
	"load_unavailable",
	"session_unavailable",
	"scope_unresolved"
]);
function parseDashboardPayload(value) {
	if (typeof value !== "object" || value === null || Array.isArray(value)) return null;
	const item = value;
	if (item.schema !== DASHBOARD_SCHEMA || !STATES.has(item.state)) return null;
	const sessionId = item.session_id === null || typeof item.session_id === "string" ? item.session_id : void 0;
	const scopeId = item.scope_id === null || typeof item.scope_id === "string" ? item.scope_id : void 0;
	if (sessionId === void 0 || scopeId === void 0) return null;
	const url = item.url;
	if (item.state === "available") {
		if (typeof url !== "string") return null;
		try {
			const parsed = new URL(url);
			if (!["http:", "https:"].includes(parsed.protocol) || parsed.username || parsed.password || !parsed.pathname.endsWith("/dashboard/home") || parsed.hash) return null;
			if ([...parsed.searchParams.keys()].some((key) => key !== "scope")) return null;
			const scopes = parsed.searchParams.getAll("scope");
			if (scopes.length > 1 || scopes.length === 1 && !/^[a-zA-Z0-9_-]{1,128}$/.test(scopes[0])) return null;
			if (scopes.length === 0 && scopeId !== null) return null;
			if (scopes.length === 1 && scopes[0] !== scopeId) return null;
		} catch {
			return null;
		}
	} else if (url !== void 0) return null;
	return {
		schema: DASHBOARD_SCHEMA,
		state: item.state,
		...typeof url === "string" ? { url } : {},
		session_id: sessionId,
		scope_id: scopeId
	};
}

//#endregion
//#region src/ui/dashboard.ts
const dashboardTextKey = {
	available: "dashboardOpen",
	disabled: "dashboardDisabled",
	authentication: "dashboardAuthentication",
	server_unavailable: "dashboardServerUnavailable",
	load_unavailable: "dashboardLoadUnavailable",
	session_unavailable: "dashboardSessionUnavailable",
	scope_unresolved: "dashboardScopeUnresolved",
	bridge_unavailable: "dashboardBridgeUnavailable"
};
function isLoopbackWebSite({ protocol, hostname }) {
	return (protocol === "http:" || protocol === "https:") && (hostname === "localhost" || hostname === "localhost." || hostname === "[::1]" || /^127(?:\.\d+){3}$/.test(hostname));
}
/** Web's iframe needs a same-site context for the Dashboard's Strict session cookie. */
function hasMismatchedLoopbackSite(dsh, dashboardUrl) {
	const dashboard = new URL(dashboardUrl);
	return isLoopbackWebSite(dsh) && isLoopbackWebSite(dashboard) && (dsh.protocol !== dashboard.protocol || dsh.hostname !== dashboard.hostname);
}
/** The browser reads a credential-free URL selected by the authenticated Host. */
async function readDashboard(sessionId, fetcher = fetch, signal) {
	const url = sessionId === void 0 ? DASHBOARD_ROUTE : `${DASHBOARD_ROUTE}?${new URLSearchParams({ session_id: sessionId })}`;
	try {
		const response = await fetcher(url, {
			method: "GET",
			headers: { Accept: "application/json" },
			credentials: "same-origin",
			signal
		});
		if (!response.ok) return { state: "bridge_unavailable" };
		return parseDashboardPayload(await response.json()) ?? { state: "bridge_unavailable" };
	} catch {
		return { state: "bridge_unavailable" };
	}
}

//#endregion
//#region src/ui/handoff/client.ts
var HandoffHttpError = class extends Error {
	status;
	constructor(code, status$1) {
		super(code);
		this.status = status$1;
	}
};
var HandoffHttp = class {
	fetcher;
	constructor(fetcher = (input, init) => fetch(input, init)) {
		this.fetcher = fetcher;
	}
	async json(path, body, signal) {
		const response = await this.fetcher("/powercontext/" + path, body === void 0 ? {
			cache: "no-store",
			signal
		} : {
			method: "POST",
			headers: { "content-type": "application/json" },
			body: JSON.stringify(body),
			cache: "no-store",
			signal
		});
		if (!response.ok) {
			const error = await response.json().catch(() => ({}));
			throw new HandoffHttpError(typeof error.code === "string" ? error.code : "integration_unavailable", response.status);
		}
		return response.json();
	}
	async resolveBoundary(sessionId, messageId) {
		const source = (await this.json(`boundaries?sessionId=${encodeURIComponent(sessionId)}&messageId=${encodeURIComponent(messageId)}`))?.source;
		if (!source || source.sessionId !== sessionId || source.messageId !== messageId || !Number.isSafeInteger(source.throughSeq) || source.throughSeq < 0 || typeof source.cwd !== "string" || !source.cwd || !Number.isFinite(Date.parse(source.workspaceObservedAt))) throw new HandoffHttpError("binding_mismatch", 409);
		return Object.freeze({ ...source });
	}
	async readReceipt(deliveryId, sessionId) {
		const result = await this.json(`deliveries/${encodeURIComponent(deliveryId)}/receipt?sessionId=${encodeURIComponent(sessionId)}`);
		if (!result || result.deliveryId !== deliveryId || ![
			"awaiting",
			"accepted",
			"needs_clarification",
			"declined"
		].includes(result.state) || ![
			"not_found",
			"verified",
			"unavailable",
			"forbidden",
			"invalid",
			"stale"
		].includes(result.readState) || typeof result.observedAt !== "string" || !Number.isFinite(Date.parse(result.observedAt))) throw new HandoffHttpError("receipt_invalid", 422);
		const attribution = result.attribution;
		if (attribution && (typeof attribution.receiver !== "string" || !attribution.receiver.trim() || attribution.kind !== "self_reported" && attribution.kind !== "authenticated" || attribution.kind === "authenticated" && (!attribution.principal || typeof attribution.principal.type !== "string" || typeof attribution.principal.id !== "string"))) throw new HandoffHttpError("receipt_invalid", 422);
		return result;
	}
	targets() {
		return this.json("targets");
	}
	async diagnoseTarget(targetId, signal) {
		const result = await this.json(`targets/${encodeURIComponent(targetId)}/diagnose`, void 0, signal);
		if (!result || result.targetId !== targetId || ![
			"present",
			"missing",
			"unknown",
			"unsupported"
		].includes(result.state) || result.state === "present" && (typeof result.version !== "string" || !result.version.trim())) throw new HandoffHttpError("integration_unavailable", 503);
		return result;
	}
	preflight(source, targetId, requestId, signal) {
		return this.json("preflights", {
			source,
			targetId,
			requestId
		}, signal);
	}
	async getPreflight(preflightId, sessionId, signal) {
		const result = await this.json(`preflights/${encodeURIComponent(preflightId)}?sessionId=${encodeURIComponent(sessionId)}`, void 0, signal);
		if (result?.preflightId !== preflightId || result.source?.sessionId !== sessionId) throw new HandoffHttpError("binding_mismatch", 409);
		return result;
	}
	decision(preflight, consent, signal) {
		return this.json(`preflights/${encodeURIComponent(preflight.preflightId)}/install`, {
			sessionId: preflight.source.sessionId,
			expectedRevision: preflight.revision,
			consent
		}, signal);
	}
	verify(preflight, confirmOriginal = false, signal) {
		return this.json(`preflights/${encodeURIComponent(preflight.preflightId)}/install`, {
			sessionId: preflight.source.sessionId,
			expectedRevision: preflight.revision,
			verify: true,
			...confirmOriginal ? { confirmOriginal: true } : {}
		}, signal);
	}
	cancelPreflight(preflight, signal) {
		return this.json(`preflights/${encodeURIComponent(preflight.preflightId)}/cancel`, { sessionId: preflight.source.sessionId }, signal);
	}
	prepare(preflight, requestId, workInput$1, supersedesDeliveryId, signal, locale) {
		return this.json("deliveries", {
			preflightId: preflight.preflightId,
			expectedRevision: preflight.revision,
			source: preflight.source,
			requestId,
			...locale === void 0 ? {} : { locale },
			...workInput$1 === void 0 ? {} : { workInput: workInput$1 },
			...supersedesDeliveryId ? { supersedesDeliveryId } : {}
		}, signal);
	}
	async history(sessionId, signal) {
		const result = await this.json(`deliveries?sessionId=${encodeURIComponent(sessionId)}`, void 0, signal);
		if (!Array.isArray(result?.deliveries) || result.deliveries.some((item) => item.source?.sessionId !== sessionId)) throw new HandoffHttpError("binding_mismatch", 409);
		return result;
	}
	async pin(deliveryId, sessionId, pinned, signal) {
		const result = await this.json(`deliveries/${encodeURIComponent(deliveryId)}/pin`, {
			sessionId,
			pinned
		}, signal);
		if (result?.deliveryId !== deliveryId || result.source?.sessionId !== sessionId || result.pinned !== pinned) throw new HandoffHttpError("binding_mismatch", 409);
		return result;
	}
	async delete(deliveryId, sessionId, signal) {
		const result = await this.json(`deliveries/${encodeURIComponent(deliveryId)}/delete`, { sessionId }, signal);
		if (result?.deleted !== true) throw new HandoffHttpError("integration_unavailable", 503);
		return result;
	}
	async get(deliveryId, sessionId, signal) {
		const result = await this.json(`deliveries/${encodeURIComponent(deliveryId)}?sessionId=${encodeURIComponent(sessionId)}`, void 0, signal);
		if (result?.delivery?.deliveryId !== deliveryId || result.delivery.source?.sessionId !== sessionId) throw new HandoffHttpError("binding_mismatch", 409);
		return result;
	}
	cancel(deliveryId, sessionId, signal) {
		return this.json(`deliveries/${encodeURIComponent(deliveryId)}/cancel`, { sessionId }, signal);
	}
	launch(deliveryId, sessionId, targetId, requestId, retryOf, signal, locale) {
		return this.json(`deliveries/${encodeURIComponent(deliveryId)}/launch`, {
			sessionId,
			targetId,
			requestId,
			...locale === void 0 ? {} : { locale },
			...retryOf ? { retryOf } : {}
		}, signal);
	}
	exportUrl(deliveryId, sessionId) {
		return `/powercontext/deliveries/${encodeURIComponent(deliveryId)}/export?sessionId=${encodeURIComponent(sessionId)}`;
	}
};
function workInput(objective, state, next, omissions, disposition, locale = "en") {
	return {
		schema: "powercontext.current-work-handoff.v1",
		trust: "untrusted_input",
		objective: objective.trim(),
		state: [{
			text: state.trim(),
			basis: "declared",
			evidence: []
		}],
		disposition,
		next_action: next.trim() ? {
			text: next.trim(),
			basis: "declared",
			evidence: []
		} : null,
		omissions: [...new Set([...omissions.split("\n").map((line) => line.trim()).filter(Boolean), locale === "zh-CN" ? "未取得历史工作区快照。" : "The historical workspace observation was not available."])]
	};
}

//#endregion
//#region src/ui/handoff/panel.tsx
const copy = {
	zh: {
		action: "交接给其他 Agent",
		title: "交接当前工作",
		close: "关闭",
		unavailable: "交接边界服务不可用。请等待集成完成。",
		target: "目标",
		select: "选择目标",
		check: "重新检查目标",
		back: "返回",
		diagnostics: "安装检查",
		checkingInstallation: "正在检查",
		checkFailed: "检查失败",
		checkUnavailable: "检查不可用",
		loading: "正在加载目标",
		notAvailable: "目标当前不可用",
		unqualifiedShort: "当前版本尚未通过交接验证",
		installationPresent: "已安装，版本",
		installationMissing: "未检测到安装",
		installationUnknown: "无法确定安装状态",
		installationUnsupported: "当前平台不支持检查",
		unqualified: "当前版本尚未通过交接验证。可以检查安装状态；交接准备和启动暂不可用。",
		preflight: "预检与安装",
		preparation: "准备",
		launch: "启动",
		receipt: "接收确认",
		original: "继续使用原来的回复",
		yes: "同意安装",
		no: "不安装并取消",
		verify: "已完成，重新检查",
		prepare: "整理交接",
		manual: "手动填写并检查交接内容",
		disposition: "工作状态",
		selectDisposition: "选择状态",
		continuable: "可以继续",
		blocked: "受阻",
		complete: "已完成",
		objective: "工作目标",
		state: "当前状态与已知问题",
		next: "下一步",
		omissions: "遗漏或未验证项（每行一项）",
		newVersion: "明确创建新的交接",
		submit: "使用填写内容准备",
		open: "打开目标",
		retry: "明确重试打开",
		cancel: "取消交接",
		refresh: "刷新状态",
		pinned: "已固定",
		pin: "固定",
		unpin: "取消固定",
		delete: "删除",
		confirmDelete: "确认删除",
		keep: "保留",
		noHistory: "此会话没有已保存的交接",
		export: "导出交接包",
		history: "交接记录",
		deliveryId: "交付 ID",
		recover: "查看交付",
		timeout: "五分钟内尚未确认，可手动刷新。",
		receiptState: "接收状态",
		receiptRead: "本次读取",
		noReceipt: "尚未读取",
		selfReported: "接收方自行报告",
		authenticated: "已认证的接收方",
		receiver: "接收方",
		principal: "认证身份",
		receiptUnavailable: "回执读取服务不可用",
		instruction: "打开仅表示系统接受请求。请在目标中检查并发送预填提示。",
		interrupted: "已请求停止，结果可能未知。请刷新预检或在交接历史中恢复；已完成的安装不会自动撤销。",
		processing: "正在处理…",
		fixedOmission: "交接会注明无法取得历史工作区快照。",
		reset: "重新选择目标",
		error: "操作未完成",
		boundary: "回复已变化，请重新选择最新回复。",
		empty: "请选择支持预检的目标。"
	},
	en: {
		action: "Hand off to another agent",
		title: "Hand off current work",
		close: "Close",
		unavailable: "The handoff boundary service is unavailable. Integration is required.",
		target: "Target",
		select: "Choose a target",
		check: "Check target again",
		back: "Back",
		diagnostics: "Installation checks",
		checkingInstallation: "Checking",
		checkFailed: "Check failed",
		checkUnavailable: "Check unavailable",
		loading: "Loading targets",
		notAvailable: "Target unavailable",
		unqualifiedShort: "Current version not yet validated for handoff",
		installationPresent: "Installed, version",
		installationMissing: "Installation not detected",
		installationUnknown: "Installation status unknown",
		installationUnsupported: "Installation check is not supported on this platform",
		unqualified: "This version has not yet been validated for handoff. You can check installation; preparation and launch are unavailable.",
		preflight: "Preflight and installation",
		preparation: "Preparation",
		launch: "Launch",
		receipt: "Receipt",
		original: "Continue with the original reply",
		yes: "Allow installation",
		no: "Decline and cancel",
		verify: "Done, check again",
		prepare: "Prepare handoff",
		manual: "Enter and review handoff details",
		disposition: "Work disposition",
		selectDisposition: "Choose a status",
		continuable: "Continuable",
		blocked: "Blocked",
		complete: "Complete",
		objective: "Objective",
		state: "Current state and known issues",
		next: "Next action",
		omissions: "Omissions or unverified items (one per line)",
		newVersion: "Explicitly create a new handoff",
		submit: "Prepare with these details",
		open: "Open target",
		retry: "Retry opening explicitly",
		cancel: "Cancel handoff",
		refresh: "Refresh status",
		pinned: "Pinned",
		pin: "Pin",
		unpin: "Unpin",
		delete: "Delete",
		confirmDelete: "Confirm deletion",
		keep: "Keep",
		noHistory: "No saved handoffs in this session",
		export: "Export handoff package",
		history: "Handoff history",
		deliveryId: "Delivery ID",
		recover: "View delivery",
		timeout: "No confirmation after five minutes. Refresh manually.",
		receiptState: "Receipt status",
		receiptRead: "Latest read",
		noReceipt: "Not read yet",
		selfReported: "Receiver self-report",
		authenticated: "Authenticated receiver",
		receiver: "Receiver",
		principal: "Principal",
		receiptUnavailable: "Receipt reader unavailable",
		instruction: "Opened means the system accepted the request. Review and send the prefilled prompt in the target.",
		interrupted: "Stop requested; the outcome may be unknown. Refresh preflight or recover from history. Completed installation is not undone.",
		processing: "Working…",
		fixedOmission: "The handoff will note that the historical workspace snapshot was unavailable.",
		reset: "Choose a target again",
		error: "The action did not complete",
		boundary: "The reply changed. Select the latest reply again.",
		empty: "Choose a target that supports preflight."
	}
};
const statusZh = {
	checking_agent: "正在检查目标",
	checking_integration: "正在检查集成",
	needs_install_consent: "等待安装确认",
	installing: "正在安装",
	verifying: "正在验证",
	ready: "已就绪",
	needs_user_action: "需要操作",
	failed: "失败",
	cancelled: "已取消",
	unknown: "结果未知",
	preparing: "正在准备",
	needs_input: "需要填写内容",
	prepared: "已准备",
	requested: "已请求",
	opened: "已打开",
	awaiting: "等待接收确认",
	stale: "历史确认，未重新查询",
	not_found: "未找到回执",
	verified: "已验证",
	unavailable: "服务不可用",
	forbidden: "无读取权限",
	invalid: "回执无效",
	accepted: "接收方已确认",
	needs_clarification: "接收方需要澄清",
	declined: "接收方已拒绝",
	install_agent: "请安装目标应用",
	install_integration: "请安装集成",
	repair_integration: "请修复集成",
	restart_target: "请重启目标应用",
	sign_in: "请登录目标应用",
	configure_target: "请配置目标应用",
	reselect_source: "请重新选择来源",
	run_preflight_again: "请重新检查",
	check_target: "请检查目标状态"
};
function status(value, locale) {
	if (!value) return "";
	return locale === "zh" ? statusZh[value] ?? value : value === "stale" ? "Historical confirmation, not rechecked" : value.replaceAll("_", " ");
}
const flowCopy = {
	zh: {
		checking: "检查目标环境",
		installing: "安装 PowerContext",
		preparing: "整理当前工作",
		opening: "打开新会话",
		waiting: "可在目标 Agent 补充要求，再发送预填草稿。",
		received: "接收方已读取并确认本次交接。",
		uncertain: "结果还未确认。先检查目标会话或在交接记录中查看，再决定是否重试。",
		source: "回复已变化，请选择最新回复或明确继续原回复。",
		missing: "还没有足够的可用上下文。可以换一条有任务背景的回复，或补充必要信息。",
		unavailable: "无法继续交接。请检查目标环境后再试。",
		details: "交付详情",
		identifier: "交付编号",
		refreshDelivery: "刷新交付状态",
		refreshReceipt: "刷新接收确认",
		optional: "补充下一步和未验证项"
	},
	en: {
		checking: "Checking target",
		installing: "Installing PowerContext",
		preparing: "Preparing handoff",
		opening: "Opening a new session",
		waiting: "Send the prefilled draft in the target agent.",
		received: "The receiver read and acknowledged this handoff.",
		uncertain: "The outcome is unknown. Check the target session or handoff history before trying again.",
		source: "The reply changed. Select the latest reply or confirm the original.",
		missing: "There is not enough usable context. Choose a reply with task context or add the missing details.",
		unavailable: "Handoff could not continue. Check the target environment and try again.",
		details: "Delivery details",
		identifier: "Delivery ID",
		refreshDelivery: "Refresh delivery",
		refreshReceipt: "Refresh receipt",
		optional: "Add next steps and gaps"
	}
};
function historyTime(value, locale) {
	const time = new Date(value);
	return Number.isFinite(time.getTime()) ? new Intl.DateTimeFormat(locale === "zh" ? "zh-CN" : "en", {
		year: "numeric",
		month: "short",
		day: "numeric",
		hour: "2-digit",
		minute: "2-digit"
	}).format(time) : value;
}
const pendingByReply = /* @__PURE__ */ new Map();
const nativeTargets = [
	{
		id: "codex-desktop",
		name: "Codex Desktop"
	},
	{
		id: "claude-desktop",
		name: "Claude Desktop (Code)"
	},
	{
		id: "claude-code-cli",
		name: "Claude Code CLI"
	}
];
function pendingOperations(sessionId, messageId) {
	const key = JSON.stringify([sessionId, messageId]);
	let pending = pendingByReply.get(key);
	if (!pending) {
		pending = {
			users: 0,
			preflights: /* @__PURE__ */ new Map(),
			preparations: /* @__PURE__ */ new Map(),
			launches: /* @__PURE__ */ new Map()
		};
		pendingByReply.set(key, pending);
	}
	return pending;
}
function releasePending(sessionId, messageId, pending) {
	const key = JSON.stringify([sessionId, messageId]);
	if (pending.users === 0 && pending.preflights.size === 0 && pending.preparations.size === 0 && pending.launches.size === 0 && pendingByReply.get(key) === pending) pendingByReply.delete(key);
}
function HandoffAction(props) {
	return /* @__PURE__ */ (0, react_jsx_runtime.jsx)(HandoffActionInstance, { ...props }, JSON.stringify([props.sessionId, props.messageId]));
}
function HandoffActionInstance(props) {
	const latest = props.useChat((snapshot) => {
		const turn = snapshot.timeline.turnOrder.at(-1);
		if (turn === void 0) return false;
		return snapshot.locations.getTurn(turn).some((key) => {
			const node = snapshot.nodes.get(key);
			return node?.kind === "turn-tail" && node.data.closing?.finalNode?.messageId === props.messageId;
		});
	});
	const [open, setOpen] = (0, react.useState)(false);
	const [visited, setVisited] = (0, react.useState)(false);
	const locale = (0, react.useSyncExternalStore)(subscribeLocale, readLocale) === "en" ? "en" : "zh";
	if (!latest && !visited) return null;
	return /* @__PURE__ */ (0, react_jsx_runtime.jsx)(HandoffPanel, {
		...props,
		locale,
		active: open,
		visited,
		latest,
		onOpen: () => {
			setVisited(true);
			setOpen(true);
		},
		onClose: () => setOpen(false)
	});
}
function HandoffPanel({ sessionId, messageId, ports, locale, active, visited, latest, onOpen, onClose }) {
	const t = copy[locale];
	const f = flowCopy[locale];
	const api = (0, react.useRef)(new HandoffHttp()).current;
	const pending = (0, react.useRef)(pendingOperations(sessionId, messageId)).current;
	(0, react.useEffect)(() => {
		pending.users++;
		return () => {
			pending.users--;
			releasePending(sessionId, messageId, pending);
		};
	}, [
		pending,
		sessionId,
		messageId
	]);
	const [replySource, setReplySource] = (0, react.useState)();
	const [confirmedOriginal, setConfirmedOriginal] = (0, react.useState)(false);
	const [recoveredFromHistory, setRecoveredFromHistory] = (0, react.useState)(false);
	const [targets, setTargets] = (0, react.useState)([]);
	const [view, setView] = (0, react.useState)("targets");
	const [target, setTarget] = (0, react.useState)("");
	const [diagnostics, setDiagnostics] = (0, react.useState)({});
	const [preflight, setPreflight] = (0, react.useState)();
	const [delivery, setDelivery] = (0, react.useState)();
	const source = delivery?.delivery.source ?? replySource;
	const [attempt, setAttempt] = (0, react.useState)();
	const [receipt, setReceipt] = (0, react.useState)();
	const [receiptRead, setReceiptRead] = (0, react.useState)();
	const [timedOut, setTimedOut] = (0, react.useState)(false);
	const [manual, setManual] = (0, react.useState)(false);
	const [disposition, setDisposition] = (0, react.useState)("");
	const [objective, setObjective] = (0, react.useState)("");
	const [state, setState] = (0, react.useState)("");
	const [next, setNext] = (0, react.useState)("");
	const [omissions, setOmissions] = (0, react.useState)("");
	const [history, setHistory] = (0, react.useState)();
	const [deleteId, setDeleteId] = (0, react.useState)();
	const [error, setError] = (0, react.useState)("");
	const [busy, setBusy] = (0, react.useState)(false);
	const [phase, setPhase] = (0, react.useState)("checking");
	const latestReply = (0, react.useRef)(latest);
	latestReply.current = latest;
	const mounted = (0, react.useRef)(true);
	(0, react.useEffect)(() => {
		mounted.current = true;
		return () => {
			mounted.current = false;
		};
	}, []);
	const lock = (0, react.useRef)(false);
	const activeRequest = (0, react.useRef)();
	const activeDeliveryId = (0, react.useRef)();
	const pendingPreparations = (0, react.useRef)(pending.preparations);
	const pendingLaunches = (0, react.useRef)(pending.launches);
	const trigger = (0, react.useRef)(null);
	const focusPanel = (0, react.useCallback)((node) => {
		node?.focus({ preventScroll: true });
	}, []);
	const run = async (fn) => {
		if (lock.current) return;
		const controller = new AbortController();
		activeRequest.current = controller;
		lock.current = true;
		setBusy(true);
		setError("");
		try {
			await fn(controller.signal);
		} catch (e) {
			setError(controller.signal.aborted ? "outcome_unknown" : e instanceof Error ? e.message : "integration_unavailable");
		} finally {
			activeRequest.current = void 0;
			lock.current = false;
			setBusy(false);
			releasePending(sessionId, messageId, pending);
		}
	};
	const stop = () => {
		if (activeRequest.current) activeRequest.current.abort();
		else if (delivery) run(async (signal) => acceptDelivery(await api.cancel(delivery.delivery.deliveryId, sessionId, signal), true));
		else if (preflight) run(async (signal) => setPreflight(await api.cancelPreflight(preflight, signal)));
	};
	(0, react.useEffect)(() => {
		let active$1 = true;
		if (!ports || !visited) return;
		Promise.all([ports.resolveBoundary(sessionId, messageId), api.targets()]).then(([boundary, result]) => {
			if (!active$1) return;
			if (boundary.sessionId !== sessionId || boundary.messageId !== messageId) {
				setError("binding_mismatch");
				return;
			}
			setReplySource(Object.freeze({ ...boundary }));
			setTargets(result.targets);
		}).catch(() => {
			if (active$1) setError("integration_unavailable");
		});
		return () => {
			active$1 = false;
		};
	}, [
		api,
		ports,
		sessionId,
		messageId,
		visited
	]);
	(0, react.useEffect)(() => {
		if (!active || view !== "diagnostics") return;
		const controller = new AbortController();
		const available = nativeTargets.filter((item) => targets.some((target$1) => target$1.targetId === item.id && target$1.diagnostics === "available"));
		setDiagnostics(Object.fromEntries(available.map((item) => [item.id, { state: "checking" }])));
		for (const item of available) api.diagnoseTarget(item.id, controller.signal).then((result) => {
			if (!controller.signal.aborted) setDiagnostics((current) => ({
				...current,
				[item.id]: result
			}));
		}, () => {
			if (!controller.signal.aborted) setDiagnostics((current) => ({
				...current,
				[item.id]: { state: "error" }
			}));
		});
		return () => {
			controller.abort();
		};
	}, [
		active,
		view,
		api,
		targets
	]);
	const readReceipt = async (id$1, isCurrent = () => true) => {
		if (!ports?.readReceipt) {
			if (isCurrent()) setReceiptRead("unavailable");
			return;
		}
		try {
			const value = await ports.readReceipt(id$1, sessionId);
			if (!isCurrent() || activeDeliveryId.current !== id$1) return void 0;
			setReceipt(value);
			setReceiptRead(value.readState);
			if (value.state !== "awaiting") setTimedOut(false);
			return value;
		} catch (error$1) {
			if (!isCurrent() || activeDeliveryId.current !== id$1) return void 0;
			setReceiptRead(error$1 instanceof HandoffHttpError && error$1.status === 403 ? "forbidden" : error$1 instanceof HandoffHttpError && error$1.message === "receipt_invalid" ? "invalid" : "unavailable");
			return;
		}
	};
	(0, react.useEffect)(() => {
		if (!active || view !== "flow" || !delivery || !attempt || !["opened", "unknown"].includes(attempt.state) || !ports?.readReceipt) return;
		let stopped = false;
		let timer;
		const start = Date.parse(attempt.createdAt);
		let delay = 2e3;
		let inFlight = false;
		const tick = async () => {
			if (stopped || inFlight || document.visibilityState === "hidden") return;
			if (!Number.isFinite(start) || Date.now() - start >= 3e5) {
				setTimedOut(true);
				return;
			}
			inFlight = true;
			try {
				const result = await readReceipt(delivery.delivery.deliveryId, () => !stopped);
				if (stopped) return;
				if (result && [
					"accepted",
					"needs_clarification",
					"declined"
				].includes(result.state)) return;
			} catch {
				if (!stopped) setReceiptRead("unavailable");
			} finally {
				inFlight = false;
			}
			if (!stopped && !document.hidden) {
				timer = setTimeout(tick, Math.min(delay, Math.max(0, 3e5 - (Date.now() - start))));
				delay = Math.min(delay * 2, 3e4);
			}
		};
		const visibility = () => {
			if (timer) clearTimeout(timer);
			if (document.visibilityState !== "hidden") tick();
		};
		document.addEventListener("visibilitychange", visibility);
		tick();
		return () => {
			stopped = true;
			if (timer) clearTimeout(timer);
			document.removeEventListener("visibilitychange", visibility);
		};
	}, [
		active,
		view,
		delivery?.delivery.deliveryId,
		attempt?.launchAttemptId,
		ports,
		sessionId
	]);
	const id = () => crypto.randomUUID();
	const acceptDelivery = (value, fromHistory = false, expected = source) => {
		const actual = value.delivery.source;
		const bound = value.preflight?.source;
		if (bound && (bound.sessionId !== actual.sessionId || bound.messageId !== actual.messageId || bound.throughSeq !== actual.throughSeq || bound.cwd !== actual.cwd || bound.workspaceObservedAt !== actual.workspaceObservedAt || value.preflight?.preflightId !== value.delivery.binding.preflightId || value.preflight?.targetId !== value.delivery.binding.targetId)) throw new Error("binding_mismatch");
		if (actual.sessionId !== sessionId || !fromHistory && (!expected || actual.messageId !== expected.messageId || actual.throughSeq !== expected.throughSeq || actual.cwd !== expected.cwd || actual.workspaceObservedAt !== expected.workspaceObservedAt)) throw new Error("binding_mismatch");
		const lastAttempt = value.launchAttempts.at(-1);
		const pendingLaunch = pendingLaunches.current.get(value.delivery.deliveryId);
		if (pendingLaunch && lastAttempt && lastAttempt.launchAttemptId !== pendingLaunch.baseline) pendingLaunches.current.delete(value.delivery.deliveryId);
		activeDeliveryId.current = value.delivery.deliveryId;
		if (fromHistory && delivery?.delivery.deliveryId !== value.delivery.deliveryId) {
			setRecoveredFromHistory(true);
			setConfirmedOriginal(false);
		}
		if (delivery?.delivery.deliveryId !== value.delivery.deliveryId) {
			setReceipt(void 0);
			setReceiptRead(void 0);
			setTimedOut(false);
			const draftKey = value.preflight && preparationKey(value.preflight.preflightId, value.delivery.deliveryId, "manual");
			setManual(!!draftKey && pendingPreparations.current.has(draftKey));
			setDisposition("");
			setObjective("");
			setState("");
			setNext("");
			setOmissions("");
		}
		setDelivery(value);
		setPreflight(value.preflight ?? void 0);
		setAttempt(value.launchAttempts.at(-1));
	};
	const preparationKey = (preflightId, predecessor, kind) => JSON.stringify([
		preflightId,
		predecessor,
		kind
	]);
	const manualKey = preflight && delivery ? preparationKey(preflight.preflightId, delivery.delivery.deliveryId, "manual") : void 0;
	const pendingManual = manualKey ? pendingPreparations.current.get(manualKey) : void 0;
	const pendingInput = pendingManual?.input;
	const requestLocale = () => readLocale();
	const canHandoff = (targetId) => targets.some((item) => item.targetId === targetId && item.availability === "requires_preflight");
	const boundTarget = targets.find((item) => item.targetId === (delivery?.delivery.binding.targetId ?? preflight?.targetId));
	const launchDelivery = async (value, signal) => {
		signal.throwIfAborted();
		const item = value.delivery;
		const baseline = value.launchAttempts.at(-1)?.launchAttemptId ?? null;
		const pending$1 = pendingLaunches.current.get(item.deliveryId) ?? {
			requestId: id(),
			baseline,
			locale: requestLocale()
		};
		pendingLaunches.current.set(item.deliveryId, pending$1);
		setPhase("opening");
		const result = await api.launch(item.deliveryId, sessionId, item.binding.targetId, pending$1.requestId, pending$1.baseline ?? void 0, signal, pending$1.locale);
		pendingLaunches.current.delete(item.deliveryId);
		setAttempt(result);
		setTimedOut(false);
	};
	const prepareDelivery = async (verified, signal, autoOpen, allowOriginal = false, expected = source) => {
		if (!mounted.current || !latestReply.current && !allowOriginal || verified.state !== "ready") return;
		signal.throwIfAborted();
		setPhase("preparing");
		const key = preparationKey(verified.preflightId, null, "automatic");
		const interrupted = pendingPreparations.current.get(key);
		const pending$1 = interrupted ?? {
			requestId: id(),
			preflight: verified,
			locale: requestLocale()
		};
		pendingPreparations.current.set(key, pending$1);
		const result = await api.prepare(pending$1.preflight, pending$1.requestId, void 0, void 0, signal, pending$1.locale);
		if (result.delivery.binding.preflightId !== verified.preflightId || result.delivery.binding.targetId !== verified.targetId) throw new Error("binding_mismatch");
		acceptDelivery(result, false, expected);
		pendingPreparations.current.delete(key);
		if (autoOpen && !interrupted && mounted.current && (latestReply.current || allowOriginal) && !signal.aborted && result.delivery.preparationState === "prepared" && !result.delivery.expired && result.launchAttempts.length === 0 && !pendingLaunches.current.has(result.delivery.deliveryId)) await launchDelivery(result, signal);
	};
	const clearFlow = () => {
		activeDeliveryId.current = void 0;
		setTarget("");
		setPreflight(void 0);
		setDelivery(void 0);
		setAttempt(void 0);
		setReceipt(void 0);
		setReceiptRead(void 0);
		setTimedOut(false);
		setConfirmedOriginal(false);
		setRecoveredFromHistory(false);
		setError("");
		setManual(false);
		setDisposition("");
		setObjective("");
		setState("");
		setNext("");
		setOmissions("");
	};
	const startPreflight = (targetId) => {
		if (!replySource || !latest || !canHandoff(targetId) || lock.current) return;
		const shownTarget = delivery?.delivery.binding.targetId ?? preflight?.targetId;
		const belongsToReply = source?.messageId === messageId && source.throughSeq === replySource.throughSeq && source.cwd === replySource.cwd && source.workspaceObservedAt === replySource.workspaceObservedAt;
		setView("flow");
		if (shownTarget === targetId && belongsToReply) return;
		clearFlow();
		setTarget(targetId);
		run(async (signal) => {
			setPhase("checking");
			const saved = pending.preflights.get(targetId);
			const requested = saved ?? {
				requestId: id(),
				source: replySource
			};
			pending.preflights.set(targetId, requested);
			const checked = await api.preflight(requested.source, targetId, requested.requestId, signal);
			if (checked.targetId !== targetId || checked.source.sessionId !== sessionId || checked.source.messageId !== messageId || checked.source.throughSeq !== replySource.throughSeq || checked.source.cwd !== replySource.cwd || checked.source.workspaceObservedAt !== replySource.workspaceObservedAt) throw new Error("binding_mismatch");
			setPreflight(checked);
			pending.preflights.delete(targetId);
			if (!saved) await prepareDelivery(checked, signal, true, false, replySource);
		});
	};
	const returnToTargets = () => {
		if (view === "flow") activeRequest.current?.abort();
		setDeleteId(void 0);
		setView("targets");
	};
	const closeMenu = () => {
		onClose();
		trigger.current?.focus({ preventScroll: true });
	};
	const errorMessage = error === "outcome_unknown" || error === "idempotency_conflict" ? f.uncertain : error === "stale_preflight" || error === "binding_mismatch" ? f.source : f.unavailable;
	const sourceAllowed = latest && source?.messageId === messageId || confirmedOriginal || recoveredFromHistory;
	const targetName = nativeTargets.find((item) => item.id === (delivery?.delivery.binding.targetId ?? preflight?.targetId ?? target))?.name;
	const busyLabel = view === "flow" ? f[phase] : t.processing;
	const preflightState = preflight?.state;
	const deliveryState = delivery?.delivery.preparationState;
	const receiptLabel = receipt ? status(receipt.state, locale) : status("awaiting", locale);
	const receiptConfirmed = receipt?.state === "accepted";
	const preparationStatus = delivery?.delivery.expired ? locale === "zh" ? "交接包已过期" : "The handoff package expired" : deliveryState === "needs_input" ? f.missing : deliveryState === "unknown" || delivery?.delivery.error?.code === "outcome_unknown" ? f.uncertain : deliveryState === "failed" ? f.unavailable : status(deliveryState, locale);
	return /* @__PURE__ */ (0, react_jsx_runtime.jsxs)(__deepseek_ai_dsh_client_ui_primitives.Menu, {
		open: active,
		onClose: closeMenu,
		side: "right",
		portal: true,
		compact: true,
		dense: true,
		autoFocus: view === "targets",
		anchorRef: trigger,
		listClassName: "pc-handoff-menu",
		anchor: /* @__PURE__ */ (0, react_jsx_runtime.jsx)(__deepseek_ai_dsh_client_ui_primitives.Tooltip, {
			label: t.action,
			side: "bottom",
			children: /* @__PURE__ */ (0, react_jsx_runtime.jsx)("button", {
				ref: trigger,
				type: "button",
				className: "pc-handoff-action",
				"aria-label": t.action,
				"aria-haspopup": "menu",
				"aria-expanded": active,
				onClick: () => {
					if (active) closeMenu();
					else {
						if (view === "diagnostics") setDiagnostics({});
						onOpen();
					}
				},
				children: /* @__PURE__ */ (0, react_jsx_runtime.jsx)(__deepseek_ai_dsh_client_ui_primitives.IconShareOutlineRegular, {})
			})
		}),
		children: [view === "targets" && /* @__PURE__ */ (0, react_jsx_runtime.jsxs)(react_jsx_runtime.Fragment, { children: [
			/* @__PURE__ */ (0, react_jsx_runtime.jsxs)("div", {
				className: "pc-handoff-brand",
				children: [/* @__PURE__ */ (0, react_jsx_runtime.jsx)(PowerContextMark, { size: 15 }), /* @__PURE__ */ (0, react_jsx_runtime.jsx)("span", { children: "PowerContext" })]
			}),
			!ports && /* @__PURE__ */ (0, react_jsx_runtime.jsx)("p", {
				role: "status",
				children: t.unavailable
			}),
			error && /* @__PURE__ */ (0, react_jsx_runtime.jsx)("p", {
				role: "alert",
				className: "pc-handoff-notice",
				children: errorMessage
			}),
			nativeTargets.map((item) => {
				const targetInfo = targets.find((candidate) => candidate.targetId === item.id);
				const available = !!replySource && latest && !busy && targetInfo?.availability === "requires_preflight";
				const reason = !replySource ? error ? t.notAvailable : t.loading : !latest ? t.boundary : busy ? t.processing : targetInfo?.diagnostics === "available" ? t.unqualifiedShort : t.notAvailable;
				return /* @__PURE__ */ (0, react_jsx_runtime.jsx)(__deepseek_ai_dsh_client_ui_primitives.MenuItemButton, {
					disabled: !available,
					disabledReason: reason,
					onSelect: () => startPreflight(item.id),
					children: item.name
				}, item.id);
			}),
			/* @__PURE__ */ (0, react_jsx_runtime.jsxs)("div", {
				className: "pc-handoff-menu-footer",
				children: [/* @__PURE__ */ (0, react_jsx_runtime.jsxs)("button", {
					type: "button",
					disabled: busy,
					onClick: () => {
						setDiagnostics({});
						setView("diagnostics");
					},
					children: [/* @__PURE__ */ (0, react_jsx_runtime.jsx)("span", {
						"aria-hidden": "true",
						children: /* @__PURE__ */ (0, react_jsx_runtime.jsx)(__deepseek_ai_dsh_client_ui_primitives.IconCheckCircleOutlineRegular, {})
					}), t.diagnostics]
				}), /* @__PURE__ */ (0, react_jsx_runtime.jsxs)("button", {
					type: "button",
					disabled: busy,
					onClick: () => {
						setView("history");
						run(async (signal) => setHistory((await api.history(sessionId, signal)).deliveries));
					},
					children: [/* @__PURE__ */ (0, react_jsx_runtime.jsx)("span", {
						"aria-hidden": "true",
						children: /* @__PURE__ */ (0, react_jsx_runtime.jsx)(__deepseek_ai_dsh_client_ui_primitives.IconClockOutlineRegular, {})
					}), t.history]
				})]
			})
		] }), view !== "targets" && /* @__PURE__ */ (0, react_jsx_runtime.jsxs)("div", {
			ref: focusPanel,
			tabIndex: -1,
			className: "pc-handoff-panel",
			onKeyDown: (event) => {
				if (event.key === "Tab") event.stopPropagation();
				else if (event.target instanceof HTMLElement && [
					"INPUT",
					"TEXTAREA",
					"SELECT"
				].includes(event.target.tagName) && [
					"ArrowDown",
					"ArrowUp",
					"Home",
					"End"
				].includes(event.key)) event.stopPropagation();
			},
			children: [
				/* @__PURE__ */ (0, react_jsx_runtime.jsxs)("div", {
					className: "pc-handoff-panel-header",
					children: [
						/* @__PURE__ */ (0, react_jsx_runtime.jsx)("button", {
							type: "button",
							className: "pc-handoff-back",
							"aria-label": t.back,
							title: t.back,
							disabled: busy && view !== "flow",
							onClick: returnToTargets,
							children: /* @__PURE__ */ (0, react_jsx_runtime.jsx)(__deepseek_ai_dsh_client_ui_primitives.IconChevronLeftOutlineRegular, {})
						}),
						/* @__PURE__ */ (0, react_jsx_runtime.jsx)("span", { children: view === "diagnostics" ? t.diagnostics : view === "history" ? t.history : targetName ?? t.target }),
						/* @__PURE__ */ (0, react_jsx_runtime.jsx)("span", {
							className: "pc-handoff-header-mark",
							"aria-hidden": "true",
							children: /* @__PURE__ */ (0, react_jsx_runtime.jsx)(PowerContextMark, { size: 14 })
						})
					]
				}),
				busy && /* @__PURE__ */ (0, react_jsx_runtime.jsxs)("div", {
					className: "pc-handoff-progress",
					role: "status",
					children: [/* @__PURE__ */ (0, react_jsx_runtime.jsx)("span", {
						className: "pc-handoff-spinner",
						"aria-hidden": "true"
					}), busyLabel]
				}),
				!ports && /* @__PURE__ */ (0, react_jsx_runtime.jsx)("p", {
					role: "status",
					children: t.unavailable
				}),
				error && /* @__PURE__ */ (0, react_jsx_runtime.jsx)("p", {
					role: "alert",
					className: "pc-handoff-notice",
					children: errorMessage
				}),
				view === "diagnostics" && nativeTargets.map((item) => {
					const info = targets.find((candidate) => candidate.targetId === item.id);
					const result = diagnostics[item.id];
					return /* @__PURE__ */ (0, react_jsx_runtime.jsx)("div", {
						className: "pc-handoff-diagnostic-row",
						children: /* @__PURE__ */ (0, react_jsx_runtime.jsxs)("div", {
							className: "pc-handoff-row-content",
							children: [/* @__PURE__ */ (0, react_jsx_runtime.jsx)("span", {
								className: "pc-handoff-row-title",
								children: item.name
							}), /* @__PURE__ */ (0, react_jsx_runtime.jsx)("span", {
								role: "status",
								className: "pc-handoff-row-meta pc-handoff-diagnostic-status",
								children: result?.state === "checking" || !result && info?.diagnostics === "available" ? /* @__PURE__ */ (0, react_jsx_runtime.jsxs)(react_jsx_runtime.Fragment, { children: [/* @__PURE__ */ (0, react_jsx_runtime.jsx)("span", {
									className: "pc-handoff-spinner",
									"aria-hidden": "true"
								}), /* @__PURE__ */ (0, react_jsx_runtime.jsx)("span", {
									className: "pc-sr-only",
									children: t.checkingInstallation
								})] }) : result?.state === "present" ? `${t.installationPresent}: ${result.version}` : result?.state === "missing" ? t.installationMissing : result?.state === "unsupported" ? t.installationUnsupported : result?.state === "unknown" ? t.installationUnknown : result?.state === "error" ? t.checkFailed : t.checkUnavailable
							})]
						})
					}, item.id);
				}),
				view === "flow" && source && /* @__PURE__ */ (0, react_jsx_runtime.jsxs)(react_jsx_runtime.Fragment, { children: [
					!sourceAllowed && /* @__PURE__ */ (0, react_jsx_runtime.jsx)("p", {
						role: "alert",
						className: "pc-handoff-notice",
						children: f.source
					}),
					boundTarget && boundTarget.availability !== "requires_preflight" && /* @__PURE__ */ (0, react_jsx_runtime.jsx)("p", {
						className: "pc-handoff-notice",
						children: t.unqualified
					}),
					!busy && !preflight && pending.preflights.has(target) && /* @__PURE__ */ (0, react_jsx_runtime.jsx)("div", {
						className: "pc-handoff-actions",
						children: /* @__PURE__ */ (0, react_jsx_runtime.jsx)("button", {
							type: "button",
							disabled: !canHandoff(target) || !latest,
							onClick: () => startPreflight(target),
							children: t.check
						})
					}),
					preflight && !delivery && !busy && /* @__PURE__ */ (0, react_jsx_runtime.jsxs)("section", {
						className: "pc-handoff-stage",
						children: [/* @__PURE__ */ (0, react_jsx_runtime.jsxs)("div", {
							className: "pc-handoff-stage-heading",
							children: [/* @__PURE__ */ (0, react_jsx_runtime.jsx)("span", {
								className: "pc-handoff-stage-icon",
								children: /* @__PURE__ */ (0, react_jsx_runtime.jsx)(__deepseek_ai_dsh_client_ui_primitives.IconCheckCircleOutlineRegular, {})
							}), /* @__PURE__ */ (0, react_jsx_runtime.jsxs)("div", { children: [/* @__PURE__ */ (0, react_jsx_runtime.jsx)("h3", { children: t.preflight }), /* @__PURE__ */ (0, react_jsx_runtime.jsx)("p", { children: preflightState === "unknown" ? f.uncertain : preflightState === "needs_install_consent" ? status(preflight.nextAction ?? "install_integration", locale) : status(preflight.nextAction || preflightState, locale) })] })]
						}), /* @__PURE__ */ (0, react_jsx_runtime.jsxs)("div", {
							className: "pc-handoff-actions",
							children: [
								preflightState === "needs_install_consent" && /* @__PURE__ */ (0, react_jsx_runtime.jsxs)(react_jsx_runtime.Fragment, { children: [/* @__PURE__ */ (0, react_jsx_runtime.jsx)("button", {
									className: "pc-handoff-emphasis",
									disabled: !latest,
									onClick: () => void run(async (signal) => {
										setPhase("installing");
										const checked = await api.decision(preflight, true, signal);
										setPreflight(checked);
										await prepareDelivery(checked, signal, true);
									}),
									children: t.yes
								}), /* @__PURE__ */ (0, react_jsx_runtime.jsx)("button", {
									onClick: () => void run(async (signal) => setPreflight(await api.decision(preflight, false, signal))),
									children: t.no
								})] }),
								preflightState === "needs_user_action" && preflight.nextAction === "reselect_source" && /* @__PURE__ */ (0, react_jsx_runtime.jsx)("button", {
									onClick: () => void run(async (signal) => {
										setPhase("checking");
										const checked = await api.verify(preflight, true, signal);
										setPreflight(checked);
										setConfirmedOriginal(checked.state === "ready");
										await prepareDelivery(checked, signal, true, true);
									}),
									children: t.original
								}),
								preflightState === "needs_user_action" && preflight.nextAction !== "reselect_source" && /* @__PURE__ */ (0, react_jsx_runtime.jsx)("button", {
									disabled: !sourceAllowed,
									onClick: () => void run(async (signal) => {
										setPhase("checking");
										const checked = await api.verify(preflight, false, signal);
										setPreflight(checked);
										await prepareDelivery(checked, signal, true, confirmedOriginal);
									}),
									children: t.verify
								}),
								error === "outcome_unknown" && /* @__PURE__ */ (0, react_jsx_runtime.jsx)("button", {
									onClick: () => void run(async (signal) => setPreflight(await api.getPreflight(preflight.preflightId, sessionId, signal))),
									children: t.refresh
								}),
								([
									"failed",
									"cancelled",
									"unknown"
								].includes(preflight.state) || !sourceAllowed) && /* @__PURE__ */ (0, react_jsx_runtime.jsx)("button", {
									onClick: () => {
										clearFlow();
										setView("targets");
									},
									children: t.reset
								}),
								preflightState === "ready" && /* @__PURE__ */ (0, react_jsx_runtime.jsx)("button", {
									className: "pc-handoff-emphasis",
									disabled: !canHandoff(preflight.targetId) || !sourceAllowed,
									onClick: () => void run((signal) => prepareDelivery(preflight, signal, false, confirmedOriginal)),
									children: t.prepare
								})
							]
						})]
					}),
					delivery && (!busy || phase !== "opening") && /* @__PURE__ */ (0, react_jsx_runtime.jsxs)("section", {
						className: "pc-handoff-stage",
						children: [
							/* @__PURE__ */ (0, react_jsx_runtime.jsxs)("div", {
								className: "pc-handoff-stage-heading",
								children: [/* @__PURE__ */ (0, react_jsx_runtime.jsx)("span", {
									className: "pc-handoff-stage-icon",
									children: /* @__PURE__ */ (0, react_jsx_runtime.jsx)(__deepseek_ai_dsh_client_ui_primitives.IconShareOutlineRegular, {})
								}), /* @__PURE__ */ (0, react_jsx_runtime.jsxs)("div", { children: [/* @__PURE__ */ (0, react_jsx_runtime.jsx)("h3", { children: attempt?.state === "opened" ? status("opened", locale) : t.preparation }), /* @__PURE__ */ (0, react_jsx_runtime.jsx)("p", { children: attempt?.state === "opened" ? receiptConfirmed ? f.received : f.waiting : attempt?.state === "unknown" ? f.uncertain : attempt?.state === "failed" ? f.unavailable : preparationStatus })] })]
							}),
							deliveryState === "needs_input" && !manual && /* @__PURE__ */ (0, react_jsx_runtime.jsx)("div", {
								className: "pc-handoff-actions",
								children: /* @__PURE__ */ (0, react_jsx_runtime.jsx)("button", {
									disabled: busy,
									onClick: () => setManual(true),
									children: t.manual
								})
							}),
							deliveryState === "needs_input" && manual && /* @__PURE__ */ (0, react_jsx_runtime.jsxs)("div", {
								className: "pc-handoff-fields",
								children: [
									/* @__PURE__ */ (0, react_jsx_runtime.jsxs)("label", { children: [t.disposition, /* @__PURE__ */ (0, react_jsx_runtime.jsxs)("select", {
										disabled: busy || !!pendingManual,
										value: pendingInput?.disposition ?? disposition,
										onChange: (e) => setDisposition(e.target.value),
										children: [
											/* @__PURE__ */ (0, react_jsx_runtime.jsx)("option", {
												value: "",
												children: t.selectDisposition
											}),
											/* @__PURE__ */ (0, react_jsx_runtime.jsx)("option", {
												value: "continuable",
												children: t.continuable
											}),
											/* @__PURE__ */ (0, react_jsx_runtime.jsx)("option", {
												value: "blocked",
												children: t.blocked
											}),
											/* @__PURE__ */ (0, react_jsx_runtime.jsx)("option", {
												value: "complete",
												children: t.complete
											})
										]
									})] }),
									/* @__PURE__ */ (0, react_jsx_runtime.jsxs)("label", { children: [t.objective, /* @__PURE__ */ (0, react_jsx_runtime.jsx)("textarea", {
										disabled: busy || !!pendingManual,
										value: pendingInput?.objective ?? objective,
										onChange: (e) => setObjective(e.target.value)
									})] }),
									/* @__PURE__ */ (0, react_jsx_runtime.jsxs)("label", { children: [t.state, /* @__PURE__ */ (0, react_jsx_runtime.jsx)("textarea", {
										disabled: busy || !!pendingManual,
										value: pendingInput?.state[0]?.text ?? state,
										onChange: (e) => setState(e.target.value)
									})] }),
									/* @__PURE__ */ (0, react_jsx_runtime.jsxs)("details", { children: [
										/* @__PURE__ */ (0, react_jsx_runtime.jsx)("summary", { children: f.optional }),
										/* @__PURE__ */ (0, react_jsx_runtime.jsxs)("label", { children: [t.next, /* @__PURE__ */ (0, react_jsx_runtime.jsx)("textarea", {
											disabled: busy || !!pendingManual,
											value: pendingInput ? pendingInput.next_action?.text ?? "" : next,
											onChange: (e) => setNext(e.target.value)
										})] }),
										/* @__PURE__ */ (0, react_jsx_runtime.jsxs)("label", { children: [t.omissions, /* @__PURE__ */ (0, react_jsx_runtime.jsx)("textarea", {
											disabled: busy || !!pendingManual,
											value: pendingInput ? pendingInput.omissions.join("\n") : omissions,
											onChange: (e) => setOmissions(e.target.value)
										})] }),
										/* @__PURE__ */ (0, react_jsx_runtime.jsx)("p", { children: t.fixedOmission })
									] }),
									/* @__PURE__ */ (0, react_jsx_runtime.jsx)("div", {
										className: "pc-handoff-actions",
										children: /* @__PURE__ */ (0, react_jsx_runtime.jsx)("button", {
											className: "pc-handoff-emphasis",
											disabled: busy || !canHandoff(delivery.delivery.binding.targetId) || !sourceAllowed || !preflight || !pendingInput && (!disposition || !objective.trim() || !state.trim()),
											onClick: () => void run(async (signal) => {
												if (preflight && manualKey && (pendingInput || disposition)) {
													setPhase("preparing");
													const selectedLocale = requestLocale();
													const pending$1 = pendingManual ?? {
														requestId: id(),
														preflight,
														locale: selectedLocale,
														input: workInput(objective, state, next, omissions, disposition, selectedLocale)
													};
													pendingPreparations.current.set(manualKey, pending$1);
													acceptDelivery(await api.prepare(pending$1.preflight, pending$1.requestId, pending$1.input, delivery.delivery.deliveryId, signal, pending$1.locale));
													pendingPreparations.current.delete(manualKey);
												}
											}),
											children: t.submit
										})
									})
								]
							}),
							/* @__PURE__ */ (0, react_jsx_runtime.jsxs)("div", {
								className: "pc-handoff-actions",
								children: [
									delivery.delivery.recoveryActions.includes("create_new_version") && deliveryState !== "needs_input" && preflight?.state === "ready" && /* @__PURE__ */ (0, react_jsx_runtime.jsx)("button", {
										disabled: busy || !canHandoff(delivery.delivery.binding.targetId) || !sourceAllowed,
										onClick: () => void run(async (signal) => {
											setPhase("preparing");
											const key = preparationKey(preflight.preflightId, delivery.delivery.deliveryId, "automatic");
											const pending$1 = pendingPreparations.current.get(key) ?? {
												requestId: id(),
												preflight,
												locale: requestLocale()
											};
											pendingPreparations.current.set(key, pending$1);
											acceptDelivery(await api.prepare(pending$1.preflight, pending$1.requestId, void 0, delivery.delivery.deliveryId, signal, pending$1.locale));
											pendingPreparations.current.delete(key);
											setManual(false);
										}),
										children: t.newVersion
									}),
									deliveryState === "prepared" && !delivery.delivery.expired && !["opened", "requested"].includes(attempt?.state ?? "") && /* @__PURE__ */ (0, react_jsx_runtime.jsx)("button", {
										className: "pc-handoff-emphasis",
										disabled: busy || !canHandoff(delivery.delivery.binding.targetId) || !sourceAllowed,
										onClick: () => void run((signal) => launchDelivery({
											...delivery,
											launchAttempts: attempt ? [attempt] : []
										}, signal)),
										children: attempt ? t.retry : t.open
									}),
									!attempt || attempt.state !== "opened" ? /* @__PURE__ */ (0, react_jsx_runtime.jsx)("button", {
										"aria-label": f.refreshDelivery,
										disabled: busy,
										onClick: () => void run(async (signal) => {
											acceptDelivery(await api.get(delivery.delivery.deliveryId, sessionId, signal), true);
										}),
										children: t.refresh
									}) : null
								]
							}),
							/* @__PURE__ */ (0, react_jsx_runtime.jsxs)("details", {
								className: "pc-handoff-details",
								children: [
									/* @__PURE__ */ (0, react_jsx_runtime.jsx)("summary", { children: f.details }),
									/* @__PURE__ */ (0, react_jsx_runtime.jsxs)("div", {
										className: "pc-handoff-row-meta pc-handoff-break",
										children: [
											f.identifier,
											": ",
											/* @__PURE__ */ (0, react_jsx_runtime.jsx)("code", { children: delivery.delivery.deliveryId })
										]
									}),
									delivery.delivery.preview?.objective && /* @__PURE__ */ (0, react_jsx_runtime.jsx)("p", { children: delivery.delivery.preview.objective }),
									deliveryState === "prepared" && !delivery.delivery.expired && /* @__PURE__ */ (0, react_jsx_runtime.jsx)("div", {
										className: "pc-handoff-actions",
										children: /* @__PURE__ */ (0, react_jsx_runtime.jsx)("a", {
											href: api.exportUrl(delivery.delivery.deliveryId, sessionId),
											download: true,
											children: t.export
										})
									})
								]
							})
						]
					}),
					delivery && attempt && ["opened", "unknown"].includes(attempt.state) && /* @__PURE__ */ (0, react_jsx_runtime.jsxs)("section", {
						className: "pc-handoff-stage pc-handoff-receipt",
						children: [/* @__PURE__ */ (0, react_jsx_runtime.jsxs)("div", {
							className: "pc-handoff-stage-heading",
							children: [/* @__PURE__ */ (0, react_jsx_runtime.jsx)("span", {
								className: "pc-handoff-stage-icon",
								children: receiptConfirmed ? /* @__PURE__ */ (0, react_jsx_runtime.jsx)(__deepseek_ai_dsh_client_ui_primitives.IconCheckCircleOutlineRegular, {}) : /* @__PURE__ */ (0, react_jsx_runtime.jsx)(__deepseek_ai_dsh_client_ui_primitives.IconClockOutlineRegular, {})
							}), /* @__PURE__ */ (0, react_jsx_runtime.jsxs)("div", { children: [
								/* @__PURE__ */ (0, react_jsx_runtime.jsx)("h3", { children: receiptLabel }),
								receiptRead && /* @__PURE__ */ (0, react_jsx_runtime.jsxs)("p", { children: [
									t.receiptRead,
									": ",
									status(receiptRead, locale)
								] }),
								receipt?.attribution && /* @__PURE__ */ (0, react_jsx_runtime.jsxs)("p", { children: [
									receipt.attribution.kind === "authenticated" ? t.authenticated : t.selfReported,
									" · ",
									t.receiver,
									": ",
									receipt.attribution.receiver,
									receipt.attribution.kind === "authenticated" && /* @__PURE__ */ (0, react_jsx_runtime.jsxs)(react_jsx_runtime.Fragment, { children: [
										" · ",
										t.principal,
										": ",
										receipt.attribution.principal.type,
										" · ",
										receipt.attribution.principal.id
									] })
								] }),
								timedOut && (!receipt || receipt.state === "awaiting") && /* @__PURE__ */ (0, react_jsx_runtime.jsx)("p", { children: t.timeout })
							] })]
						}), /* @__PURE__ */ (0, react_jsx_runtime.jsx)("div", {
							className: "pc-handoff-actions",
							children: /* @__PURE__ */ (0, react_jsx_runtime.jsx)("button", {
								"aria-label": f.refreshReceipt,
								onClick: () => void readReceipt(delivery.delivery.deliveryId),
								children: t.refresh
							})
						})]
					}),
					/* @__PURE__ */ (0, react_jsx_runtime.jsxs)("div", {
						className: "pc-handoff-actions pc-handoff-stop",
						children: [/* @__PURE__ */ (0, react_jsx_runtime.jsx)("button", {
							onClick: returnToTargets,
							children: locale === "zh" ? "更换目标" : "Change target"
						}), (busy || preflight && ![
							"failed",
							"cancelled",
							"unknown"
						].includes(preflight.state) && attempt?.state !== "opened") && /* @__PURE__ */ (0, react_jsx_runtime.jsx)("button", {
							onClick: stop,
							children: t.cancel
						})]
					})
				] }),
				view === "history" && /* @__PURE__ */ (0, react_jsx_runtime.jsxs)("div", {
					className: "pc-handoff-history-list",
					children: [history?.length === 0 && /* @__PURE__ */ (0, react_jsx_runtime.jsx)("p", {
						className: "pc-handoff-empty",
						children: t.noHistory
					}), history?.map((item) => /* @__PURE__ */ (0, react_jsx_runtime.jsxs)("div", {
						className: "pc-handoff-history-item",
						children: [
							/* @__PURE__ */ (0, react_jsx_runtime.jsx)("div", {
								className: "pc-handoff-row-title",
								title: item.preview?.objective ?? item.deliveryId,
								children: item.preview?.objective ?? t.deliveryId
							}),
							/* @__PURE__ */ (0, react_jsx_runtime.jsxs)("div", {
								className: "pc-handoff-row-meta",
								children: [
									nativeTargets.find((target$1) => target$1.id === item.binding.targetId)?.name,
									" · ",
									/* @__PURE__ */ (0, react_jsx_runtime.jsx)("time", {
										dateTime: item.createdAt,
										title: item.createdAt,
										children: historyTime(item.createdAt, locale)
									})
								]
							}),
							/* @__PURE__ */ (0, react_jsx_runtime.jsxs)("div", {
								className: "pc-handoff-row-meta",
								title: item.deliveryId,
								children: [
									status(item.preparationState, locale),
									" ",
									item.pinned ? `· ${t.pinned}` : "",
									" · ",
									/* @__PURE__ */ (0, react_jsx_runtime.jsx)("code", { children: item.deliveryId.slice(-8) })
								]
							}),
							/* @__PURE__ */ (0, react_jsx_runtime.jsxs)("div", {
								className: "pc-handoff-history-actions",
								children: [
									/* @__PURE__ */ (0, react_jsx_runtime.jsx)("button", {
										disabled: busy,
										onClick: () => void run(async (signal) => {
											acceptDelivery(await api.get(item.deliveryId, sessionId, signal), true);
											setView("flow");
										}),
										children: t.recover
									}),
									/* @__PURE__ */ (0, react_jsx_runtime.jsx)("button", {
										disabled: busy,
										onClick: () => void run(async (signal) => {
											const updated = await api.pin(item.deliveryId, sessionId, !item.pinned, signal);
											setHistory((current) => current?.map((row) => row.deliveryId === updated.deliveryId ? updated : row));
											if (delivery?.delivery.deliveryId === updated.deliveryId) setDelivery({
												...delivery,
												delivery: updated
											});
										}),
										children: item.pinned ? t.unpin : t.pin
									}),
									deleteId === item.deliveryId ? /* @__PURE__ */ (0, react_jsx_runtime.jsxs)(react_jsx_runtime.Fragment, { children: [/* @__PURE__ */ (0, react_jsx_runtime.jsx)("button", {
										disabled: busy,
										onClick: () => void run(async (signal) => {
											await api.delete(item.deliveryId, sessionId, signal);
											setHistory((current) => current?.filter((row) => row.deliveryId !== item.deliveryId));
											setDeleteId(void 0);
											pendingLaunches.current.delete(item.deliveryId);
											for (const key of pendingPreparations.current.keys()) {
												const [preflightId, predecessor] = JSON.parse(key);
												if (predecessor === item.deliveryId || predecessor === null && preflightId === item.binding.preflightId) pendingPreparations.current.delete(key);
											}
											if (delivery?.delivery.deliveryId === item.deliveryId) clearFlow();
										}),
										children: t.confirmDelete
									}), /* @__PURE__ */ (0, react_jsx_runtime.jsx)("button", {
										disabled: busy,
										onClick: () => setDeleteId(void 0),
										children: t.keep
									})] }) : /* @__PURE__ */ (0, react_jsx_runtime.jsx)("button", {
										disabled: busy,
										onClick: () => setDeleteId(item.deliveryId),
										children: t.delete
									})
								]
							})
						]
					}, item.deliveryId))]
				})
			]
		})]
	}, view);
}

//#endregion
//#region src/ui/handoff/styles.ts
const HANDOFF_STYLES = `
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
`;

//#endregion
//#region src/ui/client.tsx
const LocaleContext = (0, react.createContext)("zh-CN");
function useText() {
	const locale = (0, react.useContext)(LocaleContext);
	return (key) => texts[locale][key] ?? key;
}
const STYLE_ID = "powercontext-dsh-status-styles";
const inject = ["slots"];
function Dot({ state }) {
	return /* @__PURE__ */ (0, react_jsx_runtime.jsx)("span", {
		className: `pc-dot pc-dot-${state}`,
		"aria-hidden": "true"
	});
}
function StatusLabel({ state, snapshot }) {
	return /* @__PURE__ */ (0, react_jsx_runtime.jsx)(react_jsx_runtime.Fragment, { children: useText()(state === "failed" && snapshot.error?.kind === "authentication" ? "auth" : state) });
}
function Section({ title, children }) {
	return /* @__PURE__ */ (0, react_jsx_runtime.jsxs)("section", {
		className: "pc-section",
		"aria-label": title,
		children: [/* @__PURE__ */ (0, react_jsx_runtime.jsx)("h3", {
			className: "pc-section-title",
			children: title
		}), children]
	});
}
function SessionSection({ data }) {
	const t = useText();
	return /* @__PURE__ */ (0, react_jsx_runtime.jsx)(Section, {
		title: t("session"),
		children: data.session === null ? /* @__PURE__ */ (0, react_jsx_runtime.jsx)("p", {
			className: "pc-muted",
			children: t("noSession")
		}) : /* @__PURE__ */ (0, react_jsx_runtime.jsxs)("div", {
			className: "pc-kv",
			children: [
				data.session.cwd && /* @__PURE__ */ (0, react_jsx_runtime.jsxs)(react_jsx_runtime.Fragment, { children: [/* @__PURE__ */ (0, react_jsx_runtime.jsx)("span", {
					className: "pc-key",
					children: t("workspace")
				}), /* @__PURE__ */ (0, react_jsx_runtime.jsx)("span", {
					className: "pc-value pc-mono",
					children: data.session.cwd
				})] }),
				/* @__PURE__ */ (0, react_jsx_runtime.jsx)("span", {
					className: "pc-key",
					children: t("scope")
				}),
				/* @__PURE__ */ (0, react_jsx_runtime.jsx)("span", {
					className: "pc-value pc-mono",
					children: data.session.scope_id ?? t("unresolved")
				}),
				data.session.scope_state === "unresolved" && /* @__PURE__ */ (0, react_jsx_runtime.jsx)("p", {
					className: "pc-alert-text",
					children: t("scopeHelp")
				})
			]
		})
	});
}
function TechnicalDetails({ data, updates, serverVersion }) {
	const locale = (0, react.useContext)(LocaleContext);
	const t = useText();
	return /* @__PURE__ */ (0, react_jsx_runtime.jsxs)("details", {
		className: "pc-details",
		children: [
			/* @__PURE__ */ (0, react_jsx_runtime.jsx)("summary", { children: t("details") }),
			/* @__PURE__ */ (0, react_jsx_runtime.jsx)(Section, {
				title: t("connection"),
				children: /* @__PURE__ */ (0, react_jsx_runtime.jsxs)("div", {
					className: "pc-kv",
					children: [
						data.session && /* @__PURE__ */ (0, react_jsx_runtime.jsxs)(react_jsx_runtime.Fragment, { children: [/* @__PURE__ */ (0, react_jsx_runtime.jsx)("span", {
							className: "pc-key",
							children: t("conversation")
						}), /* @__PURE__ */ (0, react_jsx_runtime.jsx)("span", {
							className: "pc-value pc-mono",
							children: data.session.id
						})] }),
						data.connection.code && /* @__PURE__ */ (0, react_jsx_runtime.jsxs)(react_jsx_runtime.Fragment, { children: [/* @__PURE__ */ (0, react_jsx_runtime.jsx)("span", {
							className: "pc-key",
							children: t("reason")
						}), /* @__PURE__ */ (0, react_jsx_runtime.jsxs)("span", {
							className: "pc-value pc-mono",
							children: [
								data.connection.code,
								" ",
								data.connection.http_status
							]
						})] }),
						data.connection.message && /* @__PURE__ */ (0, react_jsx_runtime.jsx)("p", {
							className: "pc-value",
							children: data.connection.message
						}),
						data.connection.recovery && /* @__PURE__ */ (0, react_jsx_runtime.jsx)("p", {
							className: "pc-value",
							children: data.connection.recovery
						}),
						data.session?.scope_message && /* @__PURE__ */ (0, react_jsx_runtime.jsxs)("p", {
							className: "pc-value",
							children: [
								data.session.scope_code,
								" · ",
								data.session.scope_message
							]
						}),
						/* @__PURE__ */ (0, react_jsx_runtime.jsx)("span", {
							className: "pc-key",
							children: t("deployment")
						}),
						/* @__PURE__ */ (0, react_jsx_runtime.jsxs)("span", {
							className: "pc-value",
							children: [
								data.deployment.mode === "desktop" ? "DSH Desktop" : "Local Web",
								" · ",
								t(data.deployment.owner_local === "automatic" ? "automaticOwner" : "explicit")
							]
						}),
						/* @__PURE__ */ (0, react_jsx_runtime.jsx)("span", {
							className: "pc-key",
							children: t("endpointSource")
						}),
						/* @__PURE__ */ (0, react_jsx_runtime.jsxs)("span", {
							className: "pc-value",
							children: [data.configuration.endpoint_source, data.configuration.path_prefix ? t("pathPrefix") : ""]
						}),
						/* @__PURE__ */ (0, react_jsx_runtime.jsx)("span", {
							className: "pc-key",
							children: t("authentication")
						}),
						/* @__PURE__ */ (0, react_jsx_runtime.jsxs)("span", {
							className: "pc-value",
							children: [
								t(data.configuration.authorization_configured ? "configured" : "unconfigured"),
								" · ",
								data.configuration.authorization_source
							]
						}),
						[
							"liveness",
							"readiness",
							"capabilities"
						].map((key) => /* @__PURE__ */ (0, react_jsx_runtime.jsxs)("div", {
							className: "pc-detail-row",
							children: [/* @__PURE__ */ (0, react_jsx_runtime.jsx)("span", {
								className: "pc-key",
								children: t(key)
							}), /* @__PURE__ */ (0, react_jsx_runtime.jsxs)("span", {
								className: "pc-value",
								children: [
									t(data.server[key].state),
									" ",
									data.server[key].code
								]
							})]
						}, key)),
						/* @__PURE__ */ (0, react_jsx_runtime.jsx)(UpdateDetails, {
							locale,
							serverVersion,
							updates
						}),
						/* @__PURE__ */ (0, react_jsx_runtime.jsx)("span", {
							className: "pc-key",
							children: t("dependencies")
						}),
						/* @__PURE__ */ (0, react_jsx_runtime.jsx)("span", {
							className: "pc-value pc-mono",
							children: Object.entries(data.server.readiness.dependencies ?? {}).map(([key, value]) => `${key}=${value}`).join(", ") || t("unknown")
						}),
						/* @__PURE__ */ (0, react_jsx_runtime.jsx)("span", {
							className: "pc-key",
							children: t("scopeSource")
						}),
						/* @__PURE__ */ (0, react_jsx_runtime.jsx)("span", {
							className: "pc-value",
							children: data.configuration.scope_source
						}),
						/* @__PURE__ */ (0, react_jsx_runtime.jsx)("span", {
							className: "pc-key",
							children: t("schema")
						}),
						/* @__PURE__ */ (0, react_jsx_runtime.jsx)("span", {
							className: "pc-value pc-mono",
							children: data.capabilities.context_versions.join(", ") || t("unknown")
						}),
						/* @__PURE__ */ (0, react_jsx_runtime.jsx)("span", {
							className: "pc-key",
							children: t("memory")
						}),
						/* @__PURE__ */ (0, react_jsx_runtime.jsx)("span", {
							className: "pc-value",
							children: t(data.capabilities.memory_extraction === null ? "unknown" : data.capabilities.memory_extraction ? "configured" : "disabled")
						}),
						/* @__PURE__ */ (0, react_jsx_runtime.jsx)("span", {
							className: "pc-key",
							children: t("handoff")
						}),
						/* @__PURE__ */ (0, react_jsx_runtime.jsx)("span", {
							className: "pc-value",
							children: t(data.capabilities.handoff_generation === null ? "unknown" : data.capabilities.handoff_generation ? "configured" : "disabled")
						})
					]
				})
			}),
			/* @__PURE__ */ (0, react_jsx_runtime.jsxs)(Section, {
				title: t("automatic"),
				children: [
					data.runtime.freshness === "not_yet_observed" ? /* @__PURE__ */ (0, react_jsx_runtime.jsx)("p", {
						className: "pc-muted",
						children: t("noObservation")
					}) : /* @__PURE__ */ (0, react_jsx_runtime.jsx)("div", {
						className: "pc-stage-list",
						children: Object.entries(data.runtime.stages).map(([key, stage]) => /* @__PURE__ */ (0, react_jsx_runtime.jsxs)("div", {
							className: "pc-stage",
							"data-pc-stage": stage.operation,
							"data-pc-stage-state": stage.state,
							children: [/* @__PURE__ */ (0, react_jsx_runtime.jsx)(Dot, { state: stage.state === "unavailable" ? "failed" : stage.state === "running" ? "connecting" : [
								"resolved",
								"ready",
								"empty",
								"accepted",
								"completed",
								"appended"
							].includes(stage.state) ? "ready" : "unconfigured" }), /* @__PURE__ */ (0, react_jsx_runtime.jsxs)("div", { children: [
								/* @__PURE__ */ (0, react_jsx_runtime.jsxs)("div", {
									className: "pc-stage-head",
									children: [/* @__PURE__ */ (0, react_jsx_runtime.jsx)("span", { children: t(key) }), /* @__PURE__ */ (0, react_jsx_runtime.jsx)("span", { children: t(stage.state) })]
								}),
								stage.code && /* @__PURE__ */ (0, react_jsx_runtime.jsx)("p", {
									className: "pc-stage-message pc-mono",
									children: stage.code
								}),
								stage.message && /* @__PURE__ */ (0, react_jsx_runtime.jsx)("p", {
									className: "pc-stage-message",
									children: stage.message
								}),
								stage.recovery && /* @__PURE__ */ (0, react_jsx_runtime.jsx)("p", {
									className: "pc-stage-message",
									children: stage.recovery
								}),
								(stage.http_status || stage.request_id || stage.protocol_issue || stage.confirmation) && /* @__PURE__ */ (0, react_jsx_runtime.jsx)("p", {
									className: "pc-stage-meta pc-mono",
									children: [
										stage.http_status,
										stage.request_id,
										stage.protocol_issue,
										stage.confirmation
									].filter(Boolean).join(" · ")
								}),
								stage.observed_at && /* @__PURE__ */ (0, react_jsx_runtime.jsx)("time", {
									className: "pc-stage-meta",
									children: stage.observed_at
								})
							] })]
						}, key))
					}),
					data.runtime.freshness === "stale" && /* @__PURE__ */ (0, react_jsx_runtime.jsxs)("p", {
						className: "pc-coverage",
						children: [
							t("stale"),
							" · ",
							data.runtime.stale_reason
						]
					}),
					/* @__PURE__ */ (0, react_jsx_runtime.jsx)("p", {
						className: "pc-coverage",
						children: t("coverage")
					})
				]
			}),
			data.notices.length > 0 && /* @__PURE__ */ (0, react_jsx_runtime.jsx)("p", {
				className: "pc-coverage",
				"data-pc-notices": data.notices.join(","),
				children: data.notices.map((code) => t(code)).join(" ")
			})
		]
	});
}
/**
* The sidebar footer action and its non-modal diagnostics panel. All status
* values come from the authenticated Host bridge; the component never reads a
* credential, calls a model, or invokes `/pc doctor`.
*/
function PowerContextStatus(props) {
	const [locale, setLocale] = (0, react.useState)(readLocale);
	return /* @__PURE__ */ (0, react_jsx_runtime.jsx)(LocaleContext.Provider, {
		value: locale,
		children: /* @__PURE__ */ (0, react_jsx_runtime.jsx)(StatusPanel, {
			...props,
			locale,
			changeLocale: (value) => {
				saveLocale(value);
				setLocale(value);
			}
		})
	});
}
function StatusPanel(props) {
	const t = useText();
	const [languageOpen, setLanguageOpen] = (0, react.useState)(false);
	const languageRef = (0, react.useRef)(null);
	const { wide, useSessions, useStatus, setSession, refresh, openDashboard } = props;
	const sessionId = useSessions((state$1) => activeSessionId(state$1));
	const snapshot = useStatus((value) => value);
	const [open, setOpen] = (0, react.useState)(false);
	const updates = useVersionUpdates(open);
	const [dashboardBusy, setDashboardBusy] = (0, react.useState)(false);
	const [dashboardNotice, setDashboardNotice] = (0, react.useState)(null);
	const [externalDashboard, setExternalDashboard] = (0, react.useState)(null);
	const dashboardRequest = (0, react.useRef)(null);
	const currentSession = (0, react.useRef)(sessionId);
	currentSession.current = sessionId;
	const [clockTick, setClockTick] = (0, react.useState)(() => Date.now());
	const [anchor, setAnchor] = (0, react.useState)({
		left: 12,
		bottom: 12
	});
	const triggerRef = (0, react.useRef)(null);
	const panelRef = (0, react.useRef)(null);
	const panelId = (0, react.useId)();
	const state = displayState(snapshot, Math.max(clockTick, snapshot.last_success_at ?? 0));
	const data = snapshot.data;
	const serverVersion = snapshot.error !== null || state === "stale" || data?.server.liveness.state === "failed" ? null : data?.server.package_version;
	(0, react.useEffect)(() => {
		setSession(sessionId);
	}, [sessionId, setSession]);
	(0, react.useEffect)(() => {
		dashboardRequest.current?.abort();
		dashboardRequest.current = null;
		setDashboardBusy(false);
		setDashboardNotice(null);
		setExternalDashboard(null);
	}, [sessionId]);
	(0, react.useEffect)(() => () => {
		dashboardRequest.current?.abort();
	}, []);
	const showDashboard = async () => {
		dashboardRequest.current?.abort();
		const request = new AbortController();
		dashboardRequest.current = request;
		const selected = sessionId;
		setDashboardBusy(true);
		setDashboardNotice(null);
		setExternalDashboard(null);
		const result = await readDashboard(selected, fetch, request.signal);
		if (request.signal.aborted || currentSession.current !== selected) return;
		dashboardRequest.current = null;
		setDashboardBusy(false);
		if (result.state !== "available" || result.url === void 0) {
			setDashboardNotice(dashboardTextKey[result.state]);
			return;
		}
		if (selected === void 0) {
			setDashboardNotice("dashboardNoSession");
			setExternalDashboard(result.url);
			return;
		}
		if (hasMismatchedLoopbackSite(window.location, result.url)) {
			setDashboardNotice("dashboardLoopbackMismatch");
			setExternalDashboard(result.url);
			return;
		}
		if (!openDashboard(result.url)) {
			setDashboardNotice("dashboardContainerUnavailable");
			setExternalDashboard(result.url);
			return;
		}
		setOpen(false);
	};
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
			if (languageOpen) {
				setLanguageOpen(false);
				languageRef.current?.focus();
				return;
			}
			close$1();
		};
		document.addEventListener("pointerdown", onPointerDown, true);
		document.addEventListener("keydown", onKeyDown, true);
		return () => {
			document.removeEventListener("pointerdown", onPointerDown, true);
			document.removeEventListener("keydown", onKeyDown, true);
		};
	}, [open, languageOpen]);
	const close = () => {
		setOpen(false);
		triggerRef.current?.focus();
	};
	const onPanelKeyDown = (event) => {
		if (event.key !== "Tab") return;
		const focusable = [...panelRef.current?.querySelectorAll("button:not([disabled]), summary, [href], input, select, textarea, [tabindex]:not([tabindex=\"-1\"])") ?? []].filter((element) => element.getClientRects().length > 0);
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
	const label = t(state);
	return /* @__PURE__ */ (0, react_jsx_runtime.jsxs)("div", {
		className: "pc-status",
		lang: props.locale,
		children: [/* @__PURE__ */ (0, react_jsx_runtime.jsxs)("button", {
			ref: triggerRef,
			type: "button",
			className: `pc-trigger${wide ? " pc-trigger-wide" : " pc-trigger-rail"}`,
			"aria-label": `PowerContext ${t("status")}: ${label}`,
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
					children: [/* @__PURE__ */ (0, react_jsx_runtime.jsx)(PowerContextMark, { size: 16 }), /* @__PURE__ */ (0, react_jsx_runtime.jsx)(Dot, { state })]
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
							children: t("diagnostics")
						})] })]
					}), /* @__PURE__ */ (0, react_jsx_runtime.jsxs)("div", {
						className: "pc-actions",
						children: [
							/* @__PURE__ */ (0, react_jsx_runtime.jsx)("button", {
								ref: languageRef,
								type: "button",
								className: "pc-icon-button",
								"aria-label": t("language"),
								title: t("language"),
								"aria-expanded": languageOpen,
								"aria-controls": `${panelId}-language`,
								onClick: () => setLanguageOpen((value) => !value),
								children: /* @__PURE__ */ (0, react_jsx_runtime.jsxs)("svg", {
									width: "18",
									height: "18",
									viewBox: "0 0 24 24",
									fill: "none",
									stroke: "currentColor",
									strokeWidth: "1.6",
									"aria-hidden": "true",
									children: [
										/* @__PURE__ */ (0, react_jsx_runtime.jsx)("circle", {
											cx: "12",
											cy: "12",
											r: "9"
										}),
										/* @__PURE__ */ (0, react_jsx_runtime.jsx)("ellipse", {
											cx: "12",
											cy: "12",
											rx: "4",
											ry: "9"
										}),
										/* @__PURE__ */ (0, react_jsx_runtime.jsx)("path", { d: "M3 12h18" })
									]
								})
							}),
							/* @__PURE__ */ (0, react_jsx_runtime.jsx)("button", {
								type: "button",
								className: "pc-icon-button",
								"aria-label": t("refresh"),
								title: t("refresh"),
								disabled: snapshot.loading,
								onClick: () => {
									setClockTick(Date.now());
									refresh();
								},
								children: "↻"
							}),
							/* @__PURE__ */ (0, react_jsx_runtime.jsx)("button", {
								type: "button",
								className: "pc-icon-button",
								"data-pc-close": true,
								"aria-label": t("close"),
								title: t("close"),
								onClick: close,
								children: "×"
							})
						]
					})]
				}),
				languageOpen && /* @__PURE__ */ (0, react_jsx_runtime.jsxs)("fieldset", {
					id: `${panelId}-language`,
					className: "pc-language",
					children: [/* @__PURE__ */ (0, react_jsx_runtime.jsx)("legend", { children: t("language") }), ["zh-CN", "en"].map((value) => /* @__PURE__ */ (0, react_jsx_runtime.jsxs)("label", { children: [/* @__PURE__ */ (0, react_jsx_runtime.jsx)("input", {
						type: "radio",
						name: `${panelId}-locale`,
						value,
						checked: props.locale === value,
						onChange: () => props.changeLocale(value)
					}), value === "zh-CN" ? "简体中文" : "English"] }, value))]
				}),
				/* @__PURE__ */ (0, react_jsx_runtime.jsxs)("div", {
					className: "pc-panel-body",
					children: [
						/* @__PURE__ */ (0, react_jsx_runtime.jsxs)("section", {
							className: "pc-dashboard-entry",
							"aria-label": t("dashboard"),
							children: [
								/* @__PURE__ */ (0, react_jsx_runtime.jsxs)("div", {
									className: "pc-dashboard-intro",
									children: [/* @__PURE__ */ (0, react_jsx_runtime.jsx)("strong", { children: t("dashboard") }), /* @__PURE__ */ (0, react_jsx_runtime.jsx)("span", { children: t("dashboardIntro") })]
								}),
								/* @__PURE__ */ (0, react_jsx_runtime.jsx)("button", {
									type: "button",
									className: "pc-dashboard-button",
									disabled: dashboardBusy,
									onClick: () => {
										showDashboard();
									},
									children: dashboardBusy ? t("dashboardChecking") : t("dashboardOpen")
								}),
								dashboardNotice !== null && /* @__PURE__ */ (0, react_jsx_runtime.jsx)("p", {
									className: "pc-dashboard-notice",
									role: "status",
									children: t(dashboardNotice)
								}),
								externalDashboard !== null && /* @__PURE__ */ (0, react_jsx_runtime.jsx)("a", {
									className: "pc-text-button",
									href: externalDashboard,
									target: "_blank",
									rel: "noopener noreferrer",
									children: t("dashboardExternal")
								})
							]
						}),
						/* @__PURE__ */ (0, react_jsx_runtime.jsxs)("div", {
							className: `pc-summary pc-summary-${state}`,
							role: "status",
							"aria-live": "polite",
							"aria-busy": snapshot.loading,
							children: [
								/* @__PURE__ */ (0, react_jsx_runtime.jsx)(Dot, { state }),
								/* @__PURE__ */ (0, react_jsx_runtime.jsxs)("div", {
									className: "pc-summary-copy",
									children: [
										/* @__PURE__ */ (0, react_jsx_runtime.jsx)("div", {
											className: "pc-summary-label",
											children: /* @__PURE__ */ (0, react_jsx_runtime.jsx)(StatusLabel, {
												state,
												snapshot
											})
										}),
										/* @__PURE__ */ (0, react_jsx_runtime.jsx)("div", {
											className: "pc-summary-detail",
											children: state === "failed" ? t(failureTextKey(snapshot.error?.code ?? data?.connection.code, snapshot.error?.http_status ?? data?.connection.http_status)) : state === "ready" ? null : t(state + "Help")
										}),
										state === "failed" && data?.connection.code && /* @__PURE__ */ (0, react_jsx_runtime.jsx)("div", {
											className: "pc-stage-message pc-mono",
											children: data.connection.code
										})
									]
								}),
								/* @__PURE__ */ (0, react_jsx_runtime.jsx)("span", {
									className: "pc-summary-time",
									children: snapshot.last_success_at === null ? t("notChecked") : new Date(data?.observed_at ?? snapshot.last_success_at).toLocaleTimeString(props.locale, {
										hour: "2-digit",
										minute: "2-digit"
									})
								})
							]
						}),
						snapshot.error !== null && /* @__PURE__ */ (0, react_jsx_runtime.jsxs)("div", {
							className: "pc-alert",
							role: "alert",
							children: [
								/* @__PURE__ */ (0, react_jsx_runtime.jsx)("p", {
									className: "pc-alert-text",
									children: t(snapshot.error.kind === "authentication" ? "authHelp" : snapshot.error.kind === "forbidden" || snapshot.error.kind === "bridge" ? "bridgeHelp" : "failureHelp")
								}),
								/* @__PURE__ */ (0, react_jsx_runtime.jsx)("span", {
									className: "pc-mono",
									children: snapshot.error.code
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
										children: t("retry")
									})
								})
							]
						}),
						data !== null && /* @__PURE__ */ (0, react_jsx_runtime.jsxs)(react_jsx_runtime.Fragment, { children: [
							/* @__PURE__ */ (0, react_jsx_runtime.jsx)(Section, {
								title: t("server"),
								children: /* @__PURE__ */ (0, react_jsx_runtime.jsxs)("div", {
									className: "pc-kv",
									children: [/* @__PURE__ */ (0, react_jsx_runtime.jsx)("span", {
										className: "pc-key",
										children: t("address")
									}), /* @__PURE__ */ (0, react_jsx_runtime.jsx)("span", {
										className: "pc-value pc-mono",
										children: data.configuration.endpoint_origin || t("unknown")
									})]
								})
							}),
							/* @__PURE__ */ (0, react_jsx_runtime.jsx)(SessionSection, { data }),
							/* @__PURE__ */ (0, react_jsx_runtime.jsx)(TechnicalDetails, {
								data,
								updates,
								serverVersion
							})
						] }),
						data === null && snapshot.error === null && /* @__PURE__ */ (0, react_jsx_runtime.jsx)("p", {
							className: "pc-empty",
							children: t("connectingHelp")
						})
					]
				}),
				/* @__PURE__ */ (0, react_jsx_runtime.jsx)(VersionFooter, {
					locale: props.locale,
					pluginVersion: data?.plugin_version,
					updates
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
		style.textContent = STATUS_STYLES + HANDOFF_STYLES;
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
	const handoffPorts = new HandoffHttp();
	const handoffSlots = ctx.slots;
	handoffSlots.inject("conversation.chat.assistant-actions", () => handoffSlots.register({
		name: "conversation.chat.assistant-actions",
		id: "powercontext-handoff",
		order: 40,
		inject: () => ({ ports: handoffPorts })
	}, HandoffAction));
	const controller = new StatusController();
	ctx.effect(() => () => controller.dispose(), "powercontext: status controller disposal");
	ctx.slots.inject("sidebar.footer.action", () => ctx.slots.register({
		name: "sidebar.footer.action",
		id: "powercontext-status",
		order: 40,
		inject: () => ({
			hooks: { status: controller },
			setSession: (sessionId) => controller.setSession(sessionId),
			refresh: () => controller.refreshNow(),
			openDashboard: (url) => {
				const right = ctx.get("sidebarRight");
				const tabs = ctx.get("sidebarRightTabs");
				if (right === void 0 || tabs?.get("browser") === void 0) return false;
				try {
					right.openTab("browser", { params: { url } });
					return true;
				} catch {
					return false;
				}
			}
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
