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
import { createElement, insert, setProp } from "@opentui/solid";
import { createSignal, onCleanup } from "solid-js";
import { readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join, resolve } from "node:path";
import { execFile, spawn } from "node:child_process";
import { createHash } from "node:crypto";

//#region src/errors.ts
const PLUGIN_NAME = "powercontext-opencode";
const PLUGIN_VERSION = "0.0.1";
const PLUGIN_USER_AGENT = `${PLUGIN_NAME}/${PLUGIN_VERSION}`;
function safeRequestId(value) {
	return value && /^[a-zA-Z0-9._:-]{1,128}$/.test(value) ? value : void 0;
}
var ClientError = class extends Error {
	requestId;
	constructor(message, requestId) {
		super(message);
		this.name = new.target.name;
		this.requestId = safeRequestId(requestId);
	}
};
var TransportError = class extends ClientError {
	path;
	constructor(path, cause, requestId) {
		super(`request to ${path} failed`, requestId);
		this.path = path;
		this.cause = cause;
	}
};
var UnavailableError = class extends TransportError {};
var RequestNotSentError = class extends TransportError {};
var RequestTimeoutError = class extends TransportError {};
var UnknownOutcomeError = class extends UnavailableError {
	outcome = "unknown";
};
/** Headers were received, but the response body could not be read to completion. */
var ResponseReadError = class extends TransportError {
	statusCode;
	constructor(path, cause, statusCode, requestId) {
		super(path, cause, requestId);
		this.statusCode = statusCode;
	}
};
var InvalidResponseError = class extends ClientError {
	path;
	statusCode;
	issue;
	constructor(path, requestId, statusCode, issue) {
		super(`response from ${path} violated the API schema`, requestId);
		this.path = path;
		this.statusCode = statusCode;
		this.issue = issue;
	}
};
var UnknownOperationError = class extends ClientError {
	operationId;
	constructor(operationId) {
		super(`unknown PowerContext operation: ${operationId}`);
		this.operationId = operationId;
	}
};
var ServerResponseError = class extends ClientError {
	statusCode;
	path;
	code;
	serverMessage;
	constructor(options) {
		const suffix = typeof options.code === "string" ? ` (${options.code})` : "";
		super(`PowerContext Server returned HTTP ${options.statusCode}${suffix}`, options.requestId);
		this.statusCode = options.statusCode;
		this.path = options.path ?? "";
		this.code = options.code;
		this.serverMessage = options.message;
	}
};

//#endregion
//#region src/operations.generated.ts
const OPERATIONS = {
	create_subject_source: {
		"method": "POST",
		"path": "/v1/scopes/{scope_id}/subject-sources",
		"scopeMode": "none"
	},
	get_profile_policy: {
		"method": "GET",
		"path": "/v1/scopes/{scope_id}/profile-policy",
		"scopeMode": "none"
	},
	put_profile_policy: {
		"method": "PUT",
		"path": "/v1/scopes/{scope_id}/profile-policy",
		"scopeMode": "none"
	},
	flush_profile: {
		"method": "POST",
		"path": "/v1/profile/flush",
		"scopeMode": "none"
	},
	get_liveness: {
		"method": "GET",
		"path": "/health/live",
		"scopeMode": "none"
	},
	get_readiness: {
		"method": "GET",
		"path": "/health/ready",
		"scopeMode": "none"
	},
	get_capabilities: {
		"method": "GET",
		"path": "/v1/capabilities",
		"scopeMode": "none"
	},
	list_scopes: {
		"method": "GET",
		"path": "/v1/scopes",
		"scopeMode": "none"
	},
	create_scope: {
		"method": "POST",
		"path": "/v1/scopes",
		"scopeMode": "none"
	},
	publish_artifact: {
		"method": "POST",
		"path": "/v1/artifact-publications",
		"scopeMode": "none"
	},
	get_scope: {
		"method": "GET",
		"path": "/v1/scopes/{scope_id}",
		"scopeMode": "none"
	},
	update_scope: {
		"method": "PUT",
		"path": "/v1/scopes/{scope_id}",
		"scopeMode": "none"
	},
	get_default_scope: {
		"method": "GET",
		"path": "/v1/scopes/default",
		"scopeMode": "none"
	},
	set_default_scope: {
		"method": "PUT",
		"path": "/v1/scopes/default",
		"scopeMode": "none"
	},
	resolve_scope_selection: {
		"method": "POST",
		"path": "/v1/scopes/selection/resolve",
		"scopeMode": "none"
	},
	resolve_scope_binding: {
		"method": "POST",
		"path": "/v1/scope-bindings/resolve",
		"scopeMode": "none"
	},
	set_scope_binding: {
		"method": "PUT",
		"path": "/v1/scope-bindings",
		"scopeMode": "none"
	},
	clear_scope_binding: {
		"method": "POST",
		"path": "/v1/scope-bindings/clear",
		"scopeMode": "none"
	},
	capture_content_source: {
		"method": "POST",
		"path": "/v1/sources/content",
		"scopeMode": "current"
	},
	register_source_definition: {
		"method": "POST",
		"path": "/v1/source-definitions/register",
		"scopeMode": "none"
	},
	get_connector_checkpoint: {
		"method": "POST",
		"path": "/v1/connector-checkpoints/get",
		"scopeMode": "none"
	},
	submit_source_observation: {
		"method": "POST",
		"path": "/v1/source-observations",
		"scopeMode": "none"
	},
	commit_connector_checkpoint: {
		"method": "POST",
		"path": "/v1/connector-checkpoints/commit",
		"scopeMode": "none"
	},
	prepare_context: {
		"method": "POST",
		"path": "/v1/context/prepare",
		"scopeMode": "current"
	},
	create_work_contract: {
		"method": "POST",
		"path": "/v1/work/contracts/create",
		"scopeMode": "current"
	},
	handoff_current_work: {
		"method": "POST",
		"path": "/v1/work/handoffs/prepare-current",
		"scopeMode": "current"
	},
	acknowledge_handoff: {
		"method": "POST",
		"path": "/v1/work/handoffs/acknowledge",
		"scopeMode": "current"
	},
	record_task_outcome: {
		"method": "POST",
		"path": "/v1/work/outcomes/record",
		"scopeMode": "current"
	},
	activate_handoff: {
		"method": "POST",
		"path": "/v1/handoff/activate",
		"scopeMode": "current"
	},
	prepare_handoff: {
		"method": "POST",
		"path": "/v1/handoff/prepare",
		"scopeMode": "current"
	},
	finalize_handoff: {
		"method": "POST",
		"path": "/v1/handoff/finalize",
		"scopeMode": "current"
	},
	commit_handoff: {
		"method": "POST",
		"path": "/v1/handoff/commit",
		"scopeMode": "current"
	},
	continue_handoff: {
		"method": "POST",
		"path": "/v1/handoff/continue",
		"scopeMode": "current"
	},
	flush_topic_memory: {
		"method": "POST",
		"path": "/v1/topic-memory/flush",
		"scopeMode": "current"
	},
	search_topic_memory: {
		"method": "POST",
		"path": "/v1/topic-memory/search",
		"scopeMode": "current"
	},
	get_topic_memory: {
		"method": "POST",
		"path": "/v1/topic-memory/get",
		"scopeMode": "current"
	},
	flush_memory: {
		"method": "POST",
		"path": "/v1/memory/flush",
		"scopeMode": "current"
	},
	remember_memory: {
		"method": "POST",
		"path": "/v1/memory/remember",
		"scopeMode": "current"
	},
	search_memory: {
		"method": "POST",
		"path": "/v1/memory/search",
		"scopeMode": "current"
	},
	list_memory_entries: {
		"method": "POST",
		"path": "/v1/memory/entries/list",
		"scopeMode": "current"
	},
	get_memory_entry: {
		"method": "POST",
		"path": "/v1/memory/entries/get",
		"scopeMode": "current"
	},
	revise_memory_entry: {
		"method": "POST",
		"path": "/v1/memory/entries/revise",
		"scopeMode": "current"
	},
	retire_memory_entry: {
		"method": "POST",
		"path": "/v1/memory/entries/retire",
		"scopeMode": "current"
	},
	list_memory_changes: {
		"method": "POST",
		"path": "/v1/memory/changes",
		"scopeMode": "current"
	},
	list_dream_runs: {
		"method": "GET",
		"path": "/v1/scopes/{scope_id}/dream",
		"scopeMode": "none"
	},
	create_dream_run: {
		"method": "POST",
		"path": "/v1/scopes/{scope_id}/dream",
		"scopeMode": "none"
	},
	get_dream_run: {
		"method": "GET",
		"path": "/v1/scopes/{scope_id}/dream/{run_id}",
		"scopeMode": "none"
	},
	propose_experience: {
		"method": "POST",
		"path": "/v1/experience/propose",
		"scopeMode": "current"
	},
	generate_experience: {
		"method": "POST",
		"path": "/v1/experience/generate",
		"scopeMode": "current"
	},
	get_experience: {
		"method": "POST",
		"path": "/v1/experience/get",
		"scopeMode": "current"
	},
	propose_skill: {
		"method": "POST",
		"path": "/v1/skill/propose",
		"scopeMode": "current"
	},
	generate_skill: {
		"method": "POST",
		"path": "/v1/skill/generate",
		"scopeMode": "current"
	},
	get_skill: {
		"method": "POST",
		"path": "/v1/skill/get",
		"scopeMode": "current"
	},
	list_managed_skills: {
		"method": "POST",
		"path": "/v1/skill/library",
		"scopeMode": "current"
	},
	update_skill_lifecycle: {
		"method": "POST",
		"path": "/v1/skill/lifecycle",
		"scopeMode": "current"
	},
	get_skill_package_manifest: {
		"method": "POST",
		"path": "/v1/skill/package/manifest",
		"scopeMode": "current"
	},
	download_skill_package: {
		"method": "POST",
		"path": "/v1/skill/package/download",
		"scopeMode": "current"
	},
	propose_skill_package: {
		"method": "POST",
		"path": "/v1/skill/package/propose",
		"scopeMode": "current"
	},
	record_skill_usage: {
		"method": "POST",
		"path": "/v1/skill/usage",
		"scopeMode": "current"
	},
	list_remote_skill_targets: {
		"method": "POST",
		"path": "/v1/skill/remote/targets",
		"scopeMode": "current"
	},
	create_remote_skill_target: {
		"method": "POST",
		"path": "/v1/skill/remote/target/create",
		"scopeMode": "current"
	},
	enroll_remote_skill_target: {
		"method": "POST",
		"path": "/v1/skill/remote/target/enroll",
		"scopeMode": "none"
	},
	rename_remote_skill_target: {
		"method": "POST",
		"path": "/v1/skill/remote/target/rename",
		"scopeMode": "current"
	},
	revoke_remote_skill_target: {
		"method": "POST",
		"path": "/v1/skill/remote/target/revoke",
		"scopeMode": "current"
	},
	publish_remote_skill: {
		"method": "POST",
		"path": "/v1/skill/remote/publication/publish",
		"scopeMode": "current"
	},
	unpublish_remote_skill: {
		"method": "POST",
		"path": "/v1/skill/remote/publication/unpublish",
		"scopeMode": "current"
	},
	reconcile_remote_skills: {
		"method": "POST",
		"path": "/v1/skill/remote/reconcile",
		"scopeMode": "none"
	},
	download_remote_skill_package: {
		"method": "POST",
		"path": "/v1/skill/remote/package/download",
		"scopeMode": "none"
	},
	record_remote_skill_receipt: {
		"method": "POST",
		"path": "/v1/skill/remote/receipt",
		"scopeMode": "none"
	},
	scan_external_skills: {
		"method": "POST",
		"path": "/v1/external-skills/scan",
		"scopeMode": "current"
	},
	list_external_skills: {
		"method": "POST",
		"path": "/v1/external-skills/list",
		"scopeMode": "current"
	},
	resolve_external_skill: {
		"method": "POST",
		"path": "/v1/external-skills/resolve",
		"scopeMode": "current"
	},
	import_external_skill: {
		"method": "POST",
		"path": "/v1/external-skills/import",
		"scopeMode": "current"
	},
	list_artifact_candidates: {
		"method": "POST",
		"path": "/v1/artifact-candidates/list",
		"scopeMode": "current"
	},
	get_artifact_candidate: {
		"method": "POST",
		"path": "/v1/artifact-candidates/get",
		"scopeMode": "current"
	},
	approve_artifact_candidate: {
		"method": "POST",
		"path": "/v1/artifact-candidates/approve",
		"scopeMode": "current"
	},
	reject_artifact_candidate: {
		"method": "POST",
		"path": "/v1/artifact-candidates/reject",
		"scopeMode": "current"
	},
	revise_artifact_candidate: {
		"method": "POST",
		"path": "/v1/artifact-candidates/revise",
		"scopeMode": "current"
	},
	get_stats: {
		"method": "POST",
		"path": "/v1/stats",
		"scopeMode": "selection"
	},
	get_handoff_report: {
		"method": "POST",
		"path": "/v1/handoff-reports/get",
		"scopeMode": "selection"
	},
	list_sources: {
		"method": "GET",
		"path": "/v1/scopes/{scope_id}/sources",
		"scopeMode": "none"
	},
	create_source: {
		"method": "POST",
		"path": "/v1/scopes/{scope_id}/sources",
		"scopeMode": "none"
	},
	get_source: {
		"method": "GET",
		"path": "/v1/scopes/{scope_id}/sources/{source_type}/{source_id}",
		"scopeMode": "none"
	},
	create_artifact: {
		"method": "POST",
		"path": "/v1/scopes/{scope_id}/artifacts",
		"scopeMode": "none"
	},
	list_artifacts: {
		"method": "GET",
		"path": "/v1/scopes/{scope_id}/artifacts/{family}",
		"scopeMode": "none"
	},
	get_artifact: {
		"method": "GET",
		"path": "/v1/scopes/{scope_id}/artifacts/{family}/{artifact_id}",
		"scopeMode": "none"
	},
	replace_artifact: {
		"method": "PUT",
		"path": "/v1/scopes/{scope_id}/artifacts/{family}/{artifact_id}",
		"scopeMode": "none"
	},
	get_artifact_tags: {
		"method": "GET",
		"path": "/v1/scopes/{scope_id}/artifacts/{family}/{artifact_id}/tags",
		"scopeMode": "none"
	},
	replace_artifact_tags: {
		"method": "PUT",
		"path": "/v1/scopes/{scope_id}/artifacts/{family}/{artifact_id}/tags",
		"scopeMode": "none"
	},
	get_memory_entry_tags: {
		"method": "GET",
		"path": "/v1/scopes/{scope_id}/artifacts/memory/{artifact_id}/entries/{entry_id}/tags",
		"scopeMode": "none"
	},
	replace_memory_entry_tags: {
		"method": "PUT",
		"path": "/v1/scopes/{scope_id}/artifacts/memory/{artifact_id}/entries/{entry_id}/tags",
		"scopeMode": "none"
	},
	query_artifact_tags: {
		"method": "POST",
		"path": "/v1/scopes/{scope_id}/artifact-tags/query",
		"scopeMode": "none"
	},
	get_artifact_revision: {
		"method": "GET",
		"path": "/v1/scopes/{scope_id}/artifacts/{family}/{artifact_id}/revisions/{revision}",
		"scopeMode": "none"
	},
	list_artifact_revisions: {
		"method": "GET",
		"path": "/v1/scopes/{scope_id}/artifacts/{family}/{artifact_id}/revisions",
		"scopeMode": "none"
	},
	get_prompt_configuration: {
		"method": "GET",
		"path": "/v1/scopes/{scope_id}/prompts/{prompt_key}",
		"scopeMode": "none"
	},
	generate_prompt_demonstrations: {
		"method": "POST",
		"path": "/v1/scopes/{scope_id}/prompts/{prompt_key}/demonstrations",
		"scopeMode": "none"
	},
	get_access_principal: {
		"method": "GET",
		"path": "/v1/access/me",
		"scopeMode": "none"
	},
	check_access: {
		"method": "POST",
		"path": "/v1/access/check",
		"scopeMode": "none"
	},
	list_access_resources: {
		"method": "POST",
		"path": "/v1/access/resources/list",
		"scopeMode": "none"
	},
	list_access_roles: {
		"method": "POST",
		"path": "/v1/access/roles/list",
		"scopeMode": "none"
	},
	list_access_bindings: {
		"method": "POST",
		"path": "/v1/access/bindings/list",
		"scopeMode": "none"
	},
	create_access_binding: {
		"method": "POST",
		"path": "/v1/access/bindings/create",
		"scopeMode": "none"
	},
	revoke_access_binding: {
		"method": "POST",
		"path": "/v1/access/bindings/revoke",
		"scopeMode": "none"
	},
	replace_access_binding: {
		"method": "POST",
		"path": "/v1/access/bindings/replace",
		"scopeMode": "none"
	},
	list_access_audit: {
		"method": "POST",
		"path": "/v1/access/audit/list",
		"scopeMode": "none"
	}
};
const OPERATION_IDS = Object.keys(OPERATIONS);

//#endregion
//#region src/transport.ts
function optionalText(value) {
	return typeof value === "string" ? value.trim() || void 0 : void 0;
}
function optionalBoolean(value, name) {
	if (value === void 0) return void 0;
	if (typeof value !== "boolean") throw new Error(`${name} must be a boolean`);
	return value;
}
function environmentBoolean(env, name) {
	if (env[name] === void 0) return void 0;
	const value = env[name].trim().toLowerCase();
	if ([
		"true",
		"1",
		"yes",
		"on"
	].includes(value)) return true;
	if ([
		"false",
		"0",
		"no",
		"off"
	].includes(value)) return false;
	throw new Error(`${name} must be a boolean (true/false, 1/0, yes/no, on/off)`);
}
function readSavedClient(host, env) {
	const home = optionalText(env.HOME) ?? homedir();
	const configuredPath = optionalText(env.POWERCONTEXT_CLIENT_CONFIG_FILE);
	const path = configuredPath?.startsWith("~/") ? join(home, configuredPath.slice(2)) : configuredPath ?? join(home, ".config", "powercontext", "clients.json");
	let contents;
	try {
		contents = readFileSync(path, "utf8");
	} catch (error) {
		if (error.code === "ENOENT") return {};
		throw new Error("Unable to read PowerContext client configuration", { cause: error });
	}
	let document;
	try {
		document = JSON.parse(contents);
	} catch {
		throw new Error("PowerContext client configuration must be valid JSON");
	}
	if (!document || typeof document !== "object" || Array.isArray(document) || document.version !== 1) throw new Error("PowerContext client configuration must have version 1");
	const hosts = document.hosts;
	if (!hosts || typeof hosts !== "object" || Array.isArray(hosts)) throw new Error("PowerContext client configuration hosts must be an object");
	const value = hosts[host];
	if (value === void 0) return {};
	if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("PowerContext saved host configuration must be an object");
	const entry = value;
	if (entry.server_url !== void 0 && !optionalText(entry.server_url)) throw new Error("PowerContext saved server_url must be a non-empty string");
	return {
		server_url: optionalText(entry.server_url),
		allow_insecure_http: optionalBoolean(entry.allow_insecure_http, "allow_insecure_http")
	};
}
function normalizeServerUrl(value, allowInsecureHttp = false, name = "PowerContext server URL") {
	optionalBoolean(allowInsecureHttp, "allowInsecureHttp");
	let url;
	try {
		url = new URL(value);
	} catch {
		throw new Error(`${name} must be a valid HTTP(S) URL`);
	}
	if (!["http:", "https:"].includes(url.protocol)) throw new Error(`${name} must use HTTP or HTTPS`);
	if (url.username || url.password || url.search || url.hash) throw new Error(`${name} must not contain credentials, a query, or a fragment`);
	const host = url.hostname.toLowerCase().replace(/^\[/, "").replace(/\]$/, "");
	const octets = host.split(".");
	const loopback = host === "localhost" || host === "::1" || octets.length === 4 && octets[0] === "127" && octets.every((octet) => /^\d{1,3}$/.test(octet) && Number(octet) <= 255);
	if (url.protocol === "http:" && !loopback && !allowInsecureHttp) throw new Error(`${name} must use HTTPS outside loopback; explicitly enable allow_insecure_http to permit plaintext HTTP`);
	return url.toString().replace(/\/+$/, "").replace(/\/mcp$/, "").replace(/\/+$/, "");
}
function resolveTransport(host, env, nativeUrl, nativeConsent, defaultUrl) {
	const prefix = `POWERCONTEXT_${host.toUpperCase()}`;
	const saved = readSavedClient(host, env);
	const environmentUrl = optionalText(env[`${prefix}_BASE_URL`]) ?? optionalText(env[`${prefix}_SERVER_URL`]) ?? optionalText(env[`${prefix}_ENDPOINT`]) ?? optionalText(env.POWERCONTEXT_CLIENT_SERVER_URL);
	const pluginUrl = optionalText(nativeUrl);
	const selectedUrl = environmentUrl ?? pluginUrl ?? saved.server_url ?? defaultUrl;
	const normalized = selectedUrl === void 0 ? void 0 : normalizeServerUrl(selectedUrl, true, `${prefix}_BASE_URL`);
	const savedUrl = saved.server_url === void 0 ? void 0 : normalizeServerUrl(saved.server_url, true);
	const hostConsent = environmentBoolean(env, `${prefix}_ALLOW_INSECURE_HTTP`);
	const commonConsent = environmentBoolean(env, "POWERCONTEXT_CLIENT_ALLOW_INSECURE_HTTP");
	const pluginConsent = optionalBoolean(nativeConsent, "allowInsecureHttp");
	const nativeEndpoint = pluginUrl === void 0 ? void 0 : normalizeServerUrl(pluginUrl, true);
	const allowInsecureHttp = hostConsent ?? commonConsent ?? (pluginConsent === false ? false : normalized !== void 0 && normalized === nativeEndpoint ? pluginConsent : void 0) ?? (normalized !== void 0 && normalized === savedUrl ? saved.allow_insecure_http : void 0) ?? false;
	return {
		baseUrl: normalized === void 0 ? void 0 : normalizeServerUrl(normalized, allowInsecureHttp, `${prefix}_BASE_URL`),
		allowInsecureHttp,
		source: environmentUrl ? "environment" : pluginUrl ? "plugin" : saved.server_url ? "saved" : "default"
	};
}

//#endregion
//#region src/management.ts
function callManagement(config, signal) {
	return new Promise((resolve$1) => {
		const child = execFile("powercontext-hook", ["--doctor"], {
			signal,
			timeout: Math.max(config.requestTimeoutMs * 3, 1e4) + 1e3,
			maxBuffer: 1048576
		}, (error, stdout) => {
			try {
				const report = JSON.parse(stdout);
				if (typeof report.ok !== "boolean" || !report.checks) throw new Error("Invalid diagnostic result");
				resolve$1(report);
			} catch {
				resolve$1({
					ok: false,
					status: "failed",
					checks: { client: {
						status: "failed",
						detail: error ? "PowerContext Python diagnostics are unavailable." : "Invalid diagnostic result"
					} }
				});
			}
		});
		child.stdin?.on("error", () => {});
		child.stdin?.end(JSON.stringify({
			connection: {
				base_url: config.baseUrl,
				authorization: config.authorization ?? null,
				allow_insecure_http: config.allowInsecureHttp ?? false,
				request_timeout: config.requestTimeoutMs / 1e3
			},
			deadline: (Date.now() + Math.max(config.requestTimeoutMs * 3, 1e4)) / 1e3
		}) + "\n");
	});
}

//#endregion
//#region src/writes.generated.ts
const WRITE_OPERATIONS = [
	"acknowledge_handoff",
	"activate_handoff",
	"approve_artifact_candidate",
	"capture_content_source",
	"clear_scope_binding",
	"commit_connector_checkpoint",
	"commit_handoff",
	"continue_handoff",
	"create_access_binding",
	"create_artifact",
	"create_dream_run",
	"create_remote_skill_target",
	"create_scope",
	"create_source",
	"create_subject_source",
	"create_work_contract",
	"enroll_remote_skill_target",
	"finalize_handoff",
	"flush_memory",
	"flush_profile",
	"flush_topic_memory",
	"generate_experience",
	"generate_prompt_demonstrations",
	"generate_skill",
	"handoff_current_work",
	"import_external_skill",
	"prepare_handoff",
	"propose_experience",
	"propose_skill",
	"propose_skill_package",
	"publish_artifact",
	"publish_remote_skill",
	"put_profile_policy",
	"reconcile_remote_skills",
	"record_remote_skill_receipt",
	"record_skill_usage",
	"record_task_outcome",
	"register_source_definition",
	"reject_artifact_candidate",
	"remember_memory",
	"rename_remote_skill_target",
	"replace_access_binding",
	"replace_artifact",
	"replace_artifact_tags",
	"replace_memory_entry_tags",
	"retire_memory_entry",
	"revise_artifact_candidate",
	"revise_memory_entry",
	"revoke_access_binding",
	"revoke_remote_skill_target",
	"scan_external_skills",
	"set_default_scope",
	"set_scope_binding",
	"submit_source_observation",
	"unpublish_remote_skill",
	"update_scope",
	"update_skill_lifecycle"
];

//#endregion
//#region src/worker.ts
const WRITES = new Set(WRITE_OPERATIONS);
const MAX_BYTES = 1048576;
const MAX_PENDING = 16;
function workerCommand() {
	return {
		command: "powercontext-hook",
		args: []
	};
}
var WorkerClient = class {
	options;
	child;
	pending = /* @__PURE__ */ new Map();
	buffer = Buffer.alloc(0);
	sequence = 0;
	worker;
	constructor(options, worker = workerCommand()) {
		this.options = options;
		this.worker = worker;
	}
	async request(operation, payload, signal, timeoutMs = this.options.requestTimeoutMs, readinessResponse = false) {
		const path = OPERATIONS[operation].path;
		if (signal?.aborted) throw new RequestTimeoutError(path);
		if (this.pending.size >= MAX_PENDING) throw new UnavailableError(path);
		const totalTimeoutMs = this.child ? timeoutMs : Math.max(timeoutMs, this.options.startupTimeoutMs ?? 4e3);
		const id = String(++this.sequence);
		const frame = JSON.stringify({
			protocol: 1,
			id,
			operation,
			arguments: payload ?? {},
			readiness_response: readinessResponse,
			observe_headers: true,
			connection: {
				base_url: this.options.baseUrl,
				authorization: this.options.authorization ?? null,
				allow_insecure_http: this.options.allowInsecureHttp ?? false,
				request_timeout: timeoutMs / 1e3
			},
			deadline: (Date.now() + totalTimeoutMs) / 1e3
		}) + "\n";
		if (Buffer.byteLength(frame) > MAX_BYTES) throw new InvalidResponseError(path);
		return new Promise((resolveRequest, reject) => {
			const abort = () => this.fail(new RequestTimeoutError(path, new DOMException("Operation stopped", signal?.aborted && signal.reason?.name !== "TimeoutError" ? "AbortError" : "TimeoutError")));
			const timer = setTimeout(abort, totalTimeoutMs);
			const cleanup = () => {
				clearTimeout(timer);
				signal?.removeEventListener("abort", abort);
			};
			this.pending.set(id, {
				operation,
				resolve: resolveRequest,
				reject,
				cleanup
			});
			signal?.addEventListener("abort", abort, { once: true });
			try {
				const child = this.start();
				this.reference(child, true);
				child.stdin.write(frame, (error) => {
					if (error) this.fail(new UnavailableError(path));
				});
			} catch {
				this.fail(new UnavailableError(path));
			}
		});
	}
	close() {
		this.fail(new UnavailableError("/worker"));
	}
	reference(child, active) {
		const method = active ? "ref" : "unref";
		child[method]();
		for (const stream of [
			child.stdin,
			child.stdout,
			child.stderr
		]) stream[method]?.();
	}
	start() {
		if (this.child) return this.child;
		const { command, args } = this.worker;
		const child = spawn(command, args, {
			stdio: [
				"pipe",
				"pipe",
				"pipe"
			],
			shell: false,
			detached: process.platform !== "win32"
		});
		this.child = child;
		child.stdout.on("data", (data) => {
			if (this.child !== child) return;
			this.buffer = Buffer.concat([this.buffer, data]);
			let end;
			while ((end = this.buffer.indexOf("\n")) !== -1) {
				if (end > MAX_BYTES) {
					this.fail(new InvalidResponseError("/worker"));
					return;
				}
				const line = this.buffer.subarray(0, end);
				this.buffer = this.buffer.subarray(end + 1);
				this.receive(line);
			}
			if (this.buffer.length > MAX_BYTES) this.fail(new InvalidResponseError("/worker"));
		});
		child.stderr.resume();
		child.on("error", () => {
			if (this.child === child) this.fail(child.pid ? new UnavailableError("/worker") : new RequestNotSentError("/worker"));
		});
		child.stdin.on("error", () => {
			if (this.child === child) this.fail(new UnavailableError("/worker"));
		});
		child.on("exit", () => {
			if (this.child === child) this.fail(new UnavailableError("/worker"));
		});
		return child;
	}
	receive(line) {
		try {
			const result = JSON.parse(line.toString("utf8"));
			if (result.protocol === 1 && this.pending.has(result.id) && result.event === "response_headers" && Number.isInteger(result.status_code)) {
				this.pending.get(result.id).observed = {
					statusCode: result.status_code,
					requestId: result.request_id
				};
				return;
			}
			if (result.protocol !== 1 || !this.pending.has(result.id) || ![
				"ok",
				"empty",
				"failed",
				"unknown"
			].includes(result.outcome)) throw new Error("Invalid worker response");
			if (["ok", "empty"].includes(result.outcome) && (!("value" in result) || ![
				"json",
				"text",
				"bytes"
			].includes(result.kind ?? "") || result.kind !== "json" && typeof result.value !== "string")) throw new Error("Invalid worker value");
			const pending = this.pending.get(result.id);
			this.pending.delete(result.id);
			pending.cleanup();
			if (this.pending.size === 0 && this.child) this.reference(this.child, false);
			const path = OPERATIONS[pending.operation].path;
			if (result.outcome === "ok" || result.outcome === "empty") {
				const metadata = {
					status: result.status_code ?? 200,
					requestId: result.request_id,
					etag: result.etag
				};
				if (result.kind === "bytes" && typeof result.value === "string") pending.resolve({
					kind: "bytes",
					value: Buffer.from(result.value, "base64"),
					...metadata
				});
				else if (result.kind === "text" && typeof result.value === "string") pending.resolve({
					kind: "text",
					value: result.value,
					...metadata
				});
				else pending.resolve({
					kind: "json",
					value: result.value,
					...metadata
				});
			} else if (result.body_error && result.status_code) {
				const failure = result.body_error === "response_too_large" ? new InvalidResponseError(path, result.request_id, result.status_code, "response_too_large") : new ResponseReadError(path, new DOMException("Response body failed", result.body_error === "request_timeout" ? "TimeoutError" : "NetworkError"), result.status_code, result.request_id);
				pending.reject(Object.assign(failure, { outcome: result.outcome }));
			} else if (result.outcome === "unknown" && result.status_code) pending.reject(Object.assign(new ServerResponseError({
				statusCode: result.status_code,
				code: result.code,
				path,
				requestId: result.request_id
			}), { outcome: "unknown" }));
			else if (result.outcome === "unknown") pending.reject(new UnknownOutcomeError(path, result.error === "deadline" ? new DOMException("Deadline exceeded", "TimeoutError") : void 0));
			else if (result.error === "server" && result.status_code) pending.reject(new ServerResponseError({
				statusCode: result.status_code,
				code: result.code,
				path,
				requestId: result.request_id
			}));
			else if (result.error === "invalid_response" || result.error === "invalid_request") pending.reject(new InvalidResponseError(path, result.request_id, result.status_code));
			else if (result.error === "deadline") pending.reject(new RequestTimeoutError(path, new DOMException("Deadline exceeded", "TimeoutError")));
			else pending.reject(new UnavailableError(path));
		} catch {
			this.fail(new InvalidResponseError("/worker"));
		}
	}
	fail(error) {
		const child = this.child;
		this.child = void 0;
		this.buffer = Buffer.alloc(0);
		if (child?.pid) try {
			if (process.platform === "win32") child.kill("SIGKILL");
			else process.kill(-child.pid, "SIGKILL");
		} catch {}
		for (const pending of this.pending.values()) {
			pending.cleanup();
			const path = OPERATIONS[pending.operation].path;
			const failure = pending.observed ? new ResponseReadError(path, error.cause, pending.observed.statusCode, pending.observed.requestId) : WRITES.has(pending.operation) && !(error instanceof RequestNotSentError) ? new UnknownOutcomeError(path, error.cause) : error;
			if (!(error instanceof RequestNotSentError) && WRITES.has(pending.operation) && ![401, 403].includes(pending.observed?.statusCode ?? 0)) Object.assign(failure, { outcome: "unknown" });
			pending.reject(failure);
		}
		this.pending.clear();
	}
};

//#endregion
//#region src/client.ts
function combineSignals(signals) {
	const present = signals.filter(Boolean);
	if (typeof AbortSignal.any === "function") return AbortSignal.any(present);
	const controller = new AbortController();
	for (const signal of present) {
		if (signal.aborted) {
			controller.abort(signal.reason);
			break;
		}
		signal.addEventListener("abort", () => controller.abort(signal.reason), { once: true });
	}
	return controller.signal;
}
var PowerContextClient = class {
	options;
	worker;
	constructor(options) {
		this.options = {
			...options,
			baseUrl: normalizeServerUrl(options.baseUrl, options.allowInsecureHttp)
		};
		this.worker = new WorkerClient(this.options, options.worker);
	}
	async request(id, payload, signal, options = {}) {
		if (!Object.hasOwn(OPERATIONS, id)) throw new UnknownOperationError(id);
		return this.worker.request(id, payload, signal, typeof options === "number" ? options : this.options.requestTimeoutMs, typeof options === "object" && options.readinessResponse === true);
	}
	doctor(signal) {
		return callManagement(this.options, signal);
	}
	close() {
		this.worker.close();
	}
};

//#endregion
//#region src/secrets.ts
const SECRET_PATTERNS = [
	/-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z0-9 ]*PRIVATE KEY-----|$)/giu,
	/(?<![\w-])["']?\b(?:api[_ -]?key|access[_ -]?key|client[_ -]?secret|secret(?:[_ -]?key)?|password|passwd|passphrase|token|authorization|cookie)\b["']?\s*[:=]\s*(?:"[^"\r\n]*"|'[^'\r\n]*'|`[^`\r\n]*`|[^\s,;}\]]+)/giu,
	/(?<![\w-])bearer\s+[A-Za-z0-9._~+/=-]{8,}(?![\w-])/giu,
	/(?<![\w-])(?:sk-[A-Za-z0-9][A-Za-z0-9_-]{7,}|github_pat_[A-Za-z0-9_]{8,}|gh[pousr]_[A-Za-z0-9_]{8,}|xox[baprs]-[A-Za-z0-9-]{8,})(?![\w-])/giu
];
function scrubSecrets(text) {
	return SECRET_PATTERNS.reduce((value, pattern) => value.replace(pattern, "[REDACTED]"), text);
}
function containsSecret(text) {
	return scrubSecrets(text) !== text;
}

//#endregion
//#region src/invoke.ts
const WRITE_OPERATIONS$1 = new Set(WRITE_OPERATIONS);
function operationMutates(id) {
	return WRITE_OPERATIONS$1.has(id);
}
function hasSecret(value) {
	if (typeof value === "string") return containsSecret(value);
	if (Array.isArray(value)) return value.some(hasSecret);
	return Boolean(value && typeof value === "object" && Object.values(value).some(hasSecret));
}
function errorResult(error) {
	if (error instanceof ServerResponseError) {
		if (error.statusCode === 401) return {
			ok: false,
			code: "authentication_failed",
			message: "PowerContext authentication failed.",
			status: 401
		};
		if (error.statusCode === 409) return {
			ok: false,
			code: typeof error.code === "string" ? error.code : "conflict",
			message: error.serverMessage ?? "Citation conflict; refresh and retry once.",
			status: 409,
			request_id: error.requestId
		};
		return {
			ok: false,
			code: typeof error.code === "string" ? error.code : error.statusCode === 404 ? "not_found" : "invalid_request",
			message: error.serverMessage ?? `PowerContext returned HTTP ${error.statusCode}.`,
			status: error.statusCode,
			request_id: error.requestId
		};
	}
	if (error instanceof UnknownOperationError) return {
		ok: false,
		code: "unknown_operation",
		message: error.message
	};
	if (error instanceof InvalidResponseError) return {
		ok: false,
		code: "invalid_response",
		message: error.message
	};
	return {
		ok: false,
		code: "unavailable",
		message: "PowerContext is unavailable; continue the task."
	};
}
async function invokeOperation(client, operationId, payload, scopeId, signal) {
	const mode = OPERATIONS[operationId].scopeMode;
	const body = mode === "selection" ? {
		...payload,
		selection: {
			mode: "exact",
			scope_ids: [scopeId]
		}
	} : mode === "current" ? {
		...payload,
		scope_id: scopeId
	} : payload;
	if (operationMutates(operationId) && hasSecret(body)) return {
		ok: false,
		code: "secret_rejected",
		message: "Refused to send secret-like content to PowerContext."
	};
	try {
		const result = await client.request(operationId, body, signal);
		return {
			ok: true,
			status: result.status,
			request_id: result.requestId,
			data: result.value
		};
	} catch (error) {
		return errorResult(error);
	}
}

//#endregion
//#region src/commands.ts
const PC_COMMAND_USAGE = "doctor | search <query> | remember <text> | flush | review | stats | capabilities | skills scan";
function formatResult(result) {
	return JSON.stringify(result, null, 2);
}
function asResult(result) {
	return {
		kind: result.ok ? "success" : "error",
		text: formatResult(result)
	};
}
async function call(runtime, scopeId, operationId, payload, signal) {
	return asResult(await invokeOperation(runtime.client, operationId, payload, scopeId, signal));
}
async function handleReview(tokens, runtime, scopeId, signal) {
	const action = tokens[1];
	if (!action) return call(runtime, scopeId, "list_artifact_candidates", { status: "pending" }, signal);
	if (action === "approve") {
		const candidateId = tokens[2];
		const version = Number(tokens[3]);
		if (!candidateId || !Number.isInteger(version)) return {
			kind: "error",
			text: "Usage: /pc review approve <candidate_id> <expected_version>"
		};
		return call(runtime, scopeId, "approve_artifact_candidate", {
			candidate_id: candidateId,
			expected_version: version
		}, signal);
	}
	if (action === "reject") {
		const candidateId = tokens[2];
		const version = Number(tokens[3]);
		const reason = tokens.slice(4).join(" ");
		if (!candidateId || !Number.isInteger(version) || !reason) return {
			kind: "error",
			text: "Usage: /pc review reject <candidate_id> <expected_version> <reason>"
		};
		return call(runtime, scopeId, "reject_artifact_candidate", {
			candidate_id: candidateId,
			expected_version: version,
			reason
		}, signal);
	}
	return {
		kind: "error",
		text: "Usage: /pc review [approve|reject] ..."
	};
}
async function handlePcCommand(rawInput, runtime, scopeId, signal) {
	const tokens = rawInput.trim().split(/\s+/).filter(Boolean);
	const command = tokens[0];
	if (!command) return {
		kind: "success",
		text: `scope=${scopeId}\nbaseUrl=${runtime.config.baseUrl}\nUse /pc doctor to check Server readiness.`
	};
	if (command === "doctor") {
		const report = await runtime.client.doctor(signal);
		return {
			kind: report.ok ? "success" : "error",
			text: JSON.stringify(report, null, 2)
		};
	}
	if (!scopeId) return {
		kind: "error",
		text: "A Scope is required for this command."
	};
	if (command === "search") {
		const query = tokens.slice(1).join(" ");
		if (!query) return {
			kind: "error",
			text: "Usage: /pc search <query>"
		};
		return call(runtime, scopeId, "search_memory", {
			query,
			limit: 8,
			mode: "auto"
		}, signal);
	}
	if (command === "remember") {
		const text = tokens.slice(1).join(" ");
		if (!text) return {
			kind: "error",
			text: "Usage: /pc remember <text>"
		};
		return call(runtime, scopeId, "remember_memory", {
			kind: "agent-note",
			text
		}, signal);
	}
	if (command === "flush") return call(runtime, scopeId, "flush_memory", {}, signal);
	if (command === "review") return handleReview(tokens, runtime, scopeId, signal);
	if (command === "stats") return call(runtime, scopeId, "get_stats", {}, signal);
	if (command === "capabilities") return call(runtime, scopeId, "get_capabilities", {}, signal);
	if (command === "skills") {
		if (tokens[1] === "scan") return call(runtime, scopeId, "scan_external_skills", {}, signal);
		return {
			kind: "error",
			text: "Usage: /pc skills scan"
		};
	}
	return {
		kind: "error",
		text: `Unknown /pc subcommand. Try ${PC_COMMAND_USAGE}.`
	};
}

//#endregion
//#region src/config.ts
const DEFAULTS = {
	baseUrl: "http://127.0.0.1:8000",
	allowInsecureHttp: false,
	scopeId: void 0,
	authorization: void 0,
	capturePrompts: true,
	requestTimeoutMs: 1e3,
	httpBudgetMs: 4e3,
	maxBytes: 8e3,
	flushOnCapture: false,
	flushMaxCalls: 4
};
function envString(env, name) {
	return env[name]?.trim() || void 0;
}
function contextAssembly(raw) {
	if (raw === void 0) return void 0;
	let value;
	try {
		value = JSON.parse(raw);
	} catch {
		throw new Error("PowerContext context assembly must be a JSON object");
	}
	if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("PowerContext context assembly must be a JSON object");
	return value;
}
function envBoolean(env, name) {
	const value = envString(env, name)?.toLowerCase();
	if (!value) return void 0;
	if ([
		"1",
		"true",
		"yes",
		"on"
	].includes(value)) return true;
	if ([
		"0",
		"false",
		"no",
		"off"
	].includes(value)) return false;
	throw new Error(`${name} must be a boolean`);
}
function envInteger(env, name, fallback, minimum, maximum) {
	const raw = envString(env, name);
	if (!raw) return fallback;
	const value = Number(raw);
	if (!Number.isInteger(value) || value < minimum || value > maximum) throw new Error(`${name} must be an integer between ${minimum} and ${maximum}`);
	return value;
}
function resolveConfig(env = process.env) {
	const transport = resolveTransport("opencode", env, void 0, void 0, DEFAULTS.baseUrl);
	const requestTimeoutMs = envInteger(env, "POWERCONTEXT_OPENCODE_REQUEST_TIMEOUT_MS", DEFAULTS.requestTimeoutMs, 50, 3e4);
	const httpBudgetMs = envInteger(env, "POWERCONTEXT_OPENCODE_HTTP_BUDGET_MS", DEFAULTS.httpBudgetMs, 100, 6e4);
	if (requestTimeoutMs > httpBudgetMs) throw new Error("POWERCONTEXT_OPENCODE_REQUEST_TIMEOUT_MS must not exceed POWERCONTEXT_OPENCODE_HTTP_BUDGET_MS");
	return {
		contextAssembly: contextAssembly(envString(env, "POWERCONTEXT_OPENCODE_CONTEXT_ASSEMBLY")),
		baseUrl: transport.baseUrl,
		allowInsecureHttp: transport.allowInsecureHttp,
		scopeId: envString(env, "POWERCONTEXT_OPENCODE_SCOPE_ID"),
		authorization: envString(env, "POWERCONTEXT_OPENCODE_AUTHORIZATION"),
		capturePrompts: envBoolean(env, "POWERCONTEXT_OPENCODE_CAPTURE_PROMPTS") ?? DEFAULTS.capturePrompts,
		requestTimeoutMs,
		httpBudgetMs,
		maxBytes: envInteger(env, "POWERCONTEXT_OPENCODE_MAX_BYTES", DEFAULTS.maxBytes, 512, 32768),
		flushOnCapture: envBoolean(env, "POWERCONTEXT_OPENCODE_FLUSH_ON_CAPTURE") ?? DEFAULTS.flushOnCapture,
		flushMaxCalls: envInteger(env, "POWERCONTEXT_OPENCODE_FLUSH_MAX_CALLS", DEFAULTS.flushMaxCalls, 1, 16)
	};
}

//#endregion
//#region src/scope.ts
function sessionBindingKey(sessionID) {
	return {
		integration: "opencode",
		kind: "session",
		external_id: sessionID
	};
}
function workspaceBindingKey(cwd) {
	return {
		integration: "opencode",
		kind: "workspace",
		external_id: createHash("sha256").update(resolve(cwd)).digest("hex")
	};
}
async function resolveScopeId(client, input, signal) {
	const sessionID = input.sessionID?.trim();
	const cwd = input.cwd?.trim();
	const bindingKeys = [];
	if (sessionID) bindingKeys.push(sessionBindingKey(sessionID));
	if (cwd) bindingKeys.push(workspaceBindingKey(cwd));
	const value = (await client.request("resolve_scope_binding", {
		explicit_scope_id: input.configuredScopeId,
		binding_keys: bindingKeys
	}, signal)).value;
	const scopeId = value && typeof value === "object" ? value.scope_id : void 0;
	if (typeof scopeId !== "string" || !scopeId.trim()) throw new Error("PowerContext returned an invalid Scope");
	const resolved = scopeId.trim();
	if (input.persistSession && !input.configuredScopeId && sessionID) await client.request("set_scope_binding", {
		key: sessionBindingKey(sessionID),
		scope_id: resolved
	}, signal);
	return resolved;
}

//#endregion
//#region src/tui.tsx
const COMMAND_NAME = "powercontext.pc";
const STATUS_REFRESH_MS = 3e4;
const STATUS_TIMEOUT_MS = 3e3;
function sessionDirectory(api, sessionID) {
	if (sessionID) {
		const directory = api.state.session.get(sessionID)?.directory?.trim();
		if (directory) return directory;
	}
	return api.state.path.directory?.trim() || void 0;
}
function currentSessionID(api) {
	const route = api.route.current;
	if (route.name === "session" && "params" in route && route.params && typeof route.params.sessionID === "string") return route.params.sessionID;
}
function currentDirectory(api) {
	return sessionDirectory(api, currentSessionID(api));
}
function compactTokens(value) {
	const absolute = Math.abs(value);
	const format = (scaled, suffix) => {
		const digits = scaled < 10 ? 1 : 0;
		return `${scaled.toFixed(digits).replace(/\.0$/, "")}${suffix}`;
	};
	if (absolute >= 1e6) return format(value / 1e6, "m");
	if (absolute >= 1e3) return format(value / 1e3, "k");
	return String(value);
}
function tokenTotals(value) {
	if (!value || typeof value !== "object") return void 0;
	const recall = value.recall;
	if (!recall || typeof recall !== "object") return void 0;
	const totals = recall.totals;
	if (!totals || typeof totals !== "object") return void 0;
	const candidate = totals;
	const preparations = candidate.preparations;
	const ready = candidate.ready_preparations;
	const comparable = candidate.comparable_preparations;
	const baseline = candidate.baseline_tokens;
	const recalled = candidate.recalled_tokens;
	const reduction = candidate.token_reduction;
	if (!Number.isInteger(preparations) || Number(preparations) < 0 || !Number.isInteger(ready) || Number(ready) < 0 || !Number.isInteger(comparable) || Number(comparable) < 0 || !Number.isInteger(baseline) || Number(baseline) < 0 || !Number.isInteger(recalled) || Number(recalled) < 0 || !Number.isInteger(reduction)) return void 0;
	return {
		preparations: Number(preparations),
		ready_preparations: Number(ready),
		comparable_preparations: Number(comparable),
		baseline_tokens: Number(baseline),
		recalled_tokens: Number(recalled),
		token_reduction: Number(reduction)
	};
}
function reductionOf(value) {
	return tokenTotals(value)?.token_reduction;
}
function savingsPhrase(reduction, suffix) {
	if (reduction === void 0) return `no data ${suffix}`;
	const amount = compactTokens(Math.abs(reduction));
	return `${reduction >= 0 ? "saved" : "cost"} ${amount} ${suffix}`;
}
function savingsColor(reduction, api) {
	if (reduction === void 0 || reduction === 0) return api.theme.current.textMuted;
	return reduction > 0 ? api.theme.current.success : api.theme.current.error;
}
function formatPowerContextStatus(today, month) {
	return `PC online · ${savingsPhrase(reductionOf(today), "today")} · ${savingsPhrase(reductionOf(month), "in 30d")}`;
}
const FAILURE_LABELS = {
	authentication_failed: "PC auth failed",
	version_mismatch: "PC version mismatch",
	server_unavailable: "PC offline · run powercontext doctor",
	invalid_response: "PC invalid response"
};
function failureLabel(outcome) {
	return FAILURE_LABELS[outcome];
}
var StatusTimeoutError = class extends Error {
	constructor() {
		super("PowerContext status timed out");
		this.name = "StatusTimeoutError";
	}
};
function httpOutcome(status) {
	if (status === 401) return "authentication_failed";
	if (status === 404) return "version_mismatch";
	if (status === 503) return "server_unavailable";
	return "invalid_response";
}
function scopeFailureOutcome(error) {
	if (error instanceof StatusTimeoutError) return "server_unavailable";
	if (error instanceof ServerResponseError) return httpOutcome(error.statusCode);
	if (error instanceof UnavailableError) return "server_unavailable";
	return "invalid_response";
}
function statsFailureOutcome(result) {
	if (result.code === "authentication_failed") return "authentication_failed";
	if (result.code === "unavailable") return "server_unavailable";
	if (typeof result.status === "number") return httpOutcome(result.status);
	return "invalid_response";
}
function textNode(text, color, onMouseUp) {
	const node = createElement("text");
	setProp(node, "fg", color);
	if (onMouseUp) setProp(node, "onMouseUp", onMouseUp);
	insert(node, text);
	return node;
}
function withTimeout(promise, timeoutMs) {
	return new Promise((resolve$1, reject) => {
		const timer = setTimeout(() => reject(new StatusTimeoutError()), timeoutMs);
		promise.then((value) => {
			clearTimeout(timer);
			resolve$1(value);
		}, (error) => {
			clearTimeout(timer);
			reject(error);
		});
	});
}
async function loadStatuslineStatus(runtime, sessionID, cwd, signal) {
	if (!cwd && !runtime.config.scopeId) return {
		connected: false,
		label: "PC unavailable"
	};
	let scopeId;
	try {
		scopeId = await withTimeout(resolveScopeId(runtime.client, {
			cwd,
			sessionID,
			configuredScopeId: runtime.config.scopeId
		}, signal), STATUS_TIMEOUT_MS);
	} catch (error) {
		return {
			connected: false,
			label: failureLabel(scopeFailureOutcome(error))
		};
	}
	try {
		const [today, month] = await Promise.all([withTimeout(invokeOperation(runtime.client, "get_stats", { period: "today" }, scopeId, signal), STATUS_TIMEOUT_MS), withTimeout(invokeOperation(runtime.client, "get_stats", { period: "30d" }, scopeId, signal), STATUS_TIMEOUT_MS)]);
		if (!today.ok) return {
			connected: false,
			label: failureLabel(statsFailureOutcome(today))
		};
		if (!month.ok) return {
			connected: false,
			label: failureLabel(statsFailureOutcome(month))
		};
		const todayTotals = tokenTotals(today.data);
		const monthTotals = tokenTotals(month.data);
		if (!todayTotals || !monthTotals) return {
			connected: false,
			label: failureLabel("invalid_response")
		};
		return {
			connected: true,
			label: formatPowerContextStatus(today.data, month.data),
			todayReduction: todayTotals.token_reduction,
			monthReduction: monthTotals.token_reduction
		};
	} catch (error) {
		return {
			connected: false,
			label: failureLabel(scopeFailureOutcome(error))
		};
	}
}
function tokenSavingsView(api, runtime, sessionID) {
	const [state, setState] = createSignal({
		connected: false,
		label: "PC offline"
	});
	const controller = new AbortController();
	let disposed = false;
	const root = createElement("box");
	setProp(root, "flexDirection", "row");
	setProp(root, "gap", 1);
	setProp(root, "alignItems", "center");
	const loadStatus = () => loadStatuslineStatus(runtime, sessionID, sessionDirectory(api, sessionID), combineSignals([api.lifecycle.signal, controller.signal]));
	const refresh = () => {
		withTimeout(loadStatus(), STATUS_TIMEOUT_MS * 2 + 1e3).then((value) => {
			if (!disposed) setState(value);
		}, () => {
			if (!disposed) setState({
				connected: false,
				label: failureLabel("server_unavailable")
			});
		});
	};
	insert(root, () => {
		const current = state();
		const connectionColor = current.connected ? api.theme.current.success : api.theme.current.error;
		if (!current.connected) return [textNode("●", connectionColor, () => void refresh()), textNode(current.label, api.theme.current.textMuted)];
		return [
			textNode("●", connectionColor, () => void refresh()),
			textNode("PC online · ", api.theme.current.textMuted),
			textNode(savingsPhrase(current.todayReduction, "today"), savingsColor(current.todayReduction, api)),
			textNode(" · ", api.theme.current.textMuted),
			textNode(savingsPhrase(current.monthReduction, "in 30d"), savingsColor(current.monthReduction, api))
		];
	});
	refresh();
	const timer = setInterval(() => void refresh(), STATUS_REFRESH_MS);
	onCleanup(() => {
		disposed = true;
		clearInterval(timer);
		controller.abort();
	});
	return root;
}
function showResult(api, result) {
	const DialogAlert = api.ui.DialogAlert;
	api.ui.dialog.setSize("large");
	api.ui.dialog.replace(() => DialogAlert({
		title: result.kind === "success" ? "PowerContext" : "PowerContext error",
		message: result.text,
		onConfirm: () => api.ui.dialog.clear()
	}));
}
async function runCommand(api, runtime, rawInput) {
	api.ui.dialog.clear();
	try {
		if (rawInput.trim().split(/\s+/)[0] === "doctor") {
			showResult(api, await handlePcCommand(rawInput, runtime, void 0, api.lifecycle.signal));
			return;
		}
		const cwd = currentDirectory(api);
		if (!cwd && !runtime.config.scopeId) {
			showResult(api, {
				kind: "error",
				text: "PowerContext could not resolve the current OpenCode project directory."
			});
			return;
		}
		showResult(api, await handlePcCommand(rawInput, runtime, await resolveScopeId(runtime.client, {
			cwd,
			sessionID: currentSessionID(api),
			configuredScopeId: runtime.config.scopeId
		}, api.lifecycle.signal), api.lifecycle.signal));
	} catch {
		showResult(api, {
			kind: "error",
			text: "PowerContext is unavailable; continue normal work."
		});
	}
}
function showCommandPrompt(api, runtime) {
	const DialogPrompt = api.ui.DialogPrompt;
	api.ui.dialog.setSize("large");
	api.ui.dialog.replace(() => DialogPrompt({
		title: "PowerContext /pc",
		placeholder: PC_COMMAND_USAGE,
		onConfirm: (value) => void runCommand(api, runtime, value),
		onCancel: () => api.ui.dialog.clear()
	}));
}
const PowerContextTuiPlugin = async (api) => {
	let config;
	try {
		config = resolveConfig();
	} catch (error) {
		api.ui.toast({
			variant: "error",
			title: "PowerContext",
			message: `configuration rejected: ${String(error)}`
		});
		return;
	}
	const runtime = {
		config,
		client: new PowerContextClient({
			baseUrl: config.baseUrl,
			allowInsecureHttp: config.allowInsecureHttp,
			authorization: config.authorization,
			requestTimeoutMs: config.requestTimeoutMs
		})
	};
	api.keymap.registerLayer({ commands: [{
		name: COMMAND_NAME,
		title: "PowerContext command",
		category: "PowerContext",
		namespace: "palette",
		slashName: "pc",
		slashAliases: ["powercontext"],
		run: () => showCommandPrompt(api, runtime)
	}] });
	api.slots.register({
		order: 50,
		slots: { session_prompt_right(_context, props) {
			return tokenSavingsView(api, runtime, props.session_id);
		} }
	});
};
const plugin = {
	id: `${PLUGIN_NAME}-tui`,
	tui: PowerContextTuiPlugin
};
var tui_default = plugin;

//#endregion
export { PowerContextTuiPlugin, tui_default as default, failureLabel, formatPowerContextStatus, loadStatuslineStatus, withTimeout };
