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
	try {
		return localStorage.getItem(LOCALE_KEY) === "en" ? "en" : "zh-CN";
	} catch {
		return "zh-CN";
	}
}
function saveLocale(locale) {
	try {
		localStorage.setItem(LOCALE_KEY, locale);
	} catch {}
}
function failureTextKey(code, status) {
	if (status === 401 || /authentication|unauthorized/.test(code ?? "")) return "authHelp";
	if (status === 403) return "forbiddenHelp";
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
//#region src/ui/client.tsx
const LocaleContext = (0, react.createContext)("zh-CN");
function useText() {
	const locale = (0, react.useContext)(LocaleContext);
	return (key) => texts[locale][key] ?? key;
}
const STYLE_ID = "powercontext-dsh-status-styles";
const inject = ["slots"];
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
