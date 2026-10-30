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
let node_crypto = require("node:crypto");
let node_readline = require("node:readline");

//#region src/errors.ts
const REQUEST_ID_HEADER = "X-PowerContext-Request-ID";
const MAX_RESPONSE_BYTES = 1048576;
const MAX_CONTEXT_BYTES = 32768;
const MAX_SOURCE_LENGTH = 2e5;
const PLUGIN_NAME = "powercontext-dsh";
const PLUGIN_VERSION = "0.0.2";
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
	/** The machine-readable `error.details` object, when the response body carried one. */
	serverDetails;
	constructor(options) {
		const suffix = typeof options.code === "string" ? ` (${options.code})` : "";
		super(`PowerContext Server returned HTTP ${options.statusCode}${suffix}`, options.requestId);
		this.statusCode = options.statusCode;
		this.path = options.path ?? "";
		this.code = options.code;
		this.serverMessage = options.message;
		this.serverDetails = options.details;
	}
};

//#endregion
//#region src/operations.generated.ts
const OPERATIONS = {
	get_recall_manifest: {
		method: "POST",
		path: "/v1/recall/manifest",
		location: "body",
		scopeMode: "current",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	list_atomic_memories: {
		method: "POST",
		path: "/v1/atomic-memory/list",
		location: "body",
		scopeMode: "current",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	search_atomic_memory: {
		method: "POST",
		path: "/v1/atomic-memory/search",
		location: "body",
		scopeMode: "current",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	merge_atomic_memories: {
		method: "POST",
		path: "/v1/atomic-memory/merges",
		location: "body",
		scopeMode: "current",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	change_atomic_memory_lifecycle: {
		method: "POST",
		path: "/v1/atomic-memory/lifecycle",
		location: "body",
		scopeMode: "current",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	preview_atomic_memory_restoration: {
		method: "POST",
		path: "/v1/atomic-memory/restoration-previews",
		location: "body",
		scopeMode: "current",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	restore_atomic_memory: {
		method: "POST",
		path: "/v1/atomic-memory/restorations",
		location: "body",
		scopeMode: "current",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	get_atomic_memory_state: {
		method: "GET",
		path: "/v1/scopes/{scope_id}/artifacts/atomic-memory/{artifact_id}/state",
		location: null,
		scopeMode: "current",
		pathParameters: ["scope_id", "artifact_id"],
		queryParams: [],
		headerParams: ["If-None-Match"],
		successStatuses: [200, 304],
		emptyStatuses: [304]
	},
	create_subject_source: {
		method: "POST",
		path: "/v1/scopes/{scope_id}/subject-sources",
		location: "body",
		scopeMode: "none",
		pathParameters: ["scope_id"],
		queryParams: [],
		headerParams: [],
		successStatuses: [201],
		emptyStatuses: []
	},
	get_profile_policy: {
		method: "GET",
		path: "/v1/scopes/{scope_id}/profile-policy",
		location: null,
		scopeMode: "none",
		pathParameters: ["scope_id"],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	put_profile_policy: {
		method: "PUT",
		path: "/v1/scopes/{scope_id}/profile-policy",
		location: "body",
		scopeMode: "none",
		pathParameters: ["scope_id"],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	flush_profile: {
		method: "POST",
		path: "/v1/profile/flush",
		location: "body",
		scopeMode: "none",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	get_liveness: {
		method: "GET",
		path: "/health/live",
		location: null,
		scopeMode: "none",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	get_readiness: {
		method: "GET",
		path: "/health/ready",
		location: null,
		scopeMode: "none",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	get_server_info: {
		method: "GET",
		path: "/v1/server-info",
		location: null,
		scopeMode: "none",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	get_capabilities: {
		method: "GET",
		path: "/v1/capabilities",
		location: null,
		scopeMode: "none",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	list_scopes: {
		method: "GET",
		path: "/v1/scopes",
		location: "query",
		scopeMode: "none",
		pathParameters: [],
		queryParams: [
			"query",
			"query_field",
			"parent_scope_id",
			"external_reference_kind",
			"binding_integration",
			"binding_kind",
			"limit",
			"cursor"
		],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	create_scope: {
		method: "POST",
		path: "/v1/scopes",
		location: "body",
		scopeMode: "none",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [201],
		emptyStatuses: []
	},
	publish_artifact: {
		method: "POST",
		path: "/v1/artifact-publications",
		location: "body",
		scopeMode: "none",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [201],
		emptyStatuses: []
	},
	get_scope: {
		method: "GET",
		path: "/v1/scopes/{scope_id}",
		location: null,
		scopeMode: "none",
		pathParameters: ["scope_id"],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	update_scope: {
		method: "PUT",
		path: "/v1/scopes/{scope_id}",
		location: "body",
		scopeMode: "none",
		pathParameters: ["scope_id"],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	get_default_scope: {
		method: "GET",
		path: "/v1/scopes/default",
		location: null,
		scopeMode: "none",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	set_default_scope: {
		method: "PUT",
		path: "/v1/scopes/default",
		location: "body",
		scopeMode: "none",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	resolve_scope_selection: {
		method: "POST",
		path: "/v1/scopes/selection/resolve",
		location: "body",
		scopeMode: "none",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	resolve_scope_binding: {
		method: "POST",
		path: "/v1/scope-bindings/resolve",
		location: "body",
		scopeMode: "none",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	set_scope_binding: {
		method: "PUT",
		path: "/v1/scope-bindings",
		location: "body",
		scopeMode: "none",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	clear_scope_binding: {
		method: "POST",
		path: "/v1/scope-bindings/clear",
		location: "body",
		scopeMode: "none",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	capture_content_source: {
		method: "POST",
		path: "/v1/sources/content",
		location: "body",
		scopeMode: "current",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [202],
		emptyStatuses: []
	},
	capture_working_note: {
		method: "POST",
		path: "/v1/sources/working-notes",
		location: "body",
		scopeMode: "current",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [202],
		emptyStatuses: []
	},
	register_source_definition: {
		method: "POST",
		path: "/v1/source-definitions/register",
		location: "body",
		scopeMode: "none",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	get_connector_checkpoint: {
		method: "POST",
		path: "/v1/connector-checkpoints/get",
		location: "body",
		scopeMode: "none",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	submit_source_observation: {
		method: "POST",
		path: "/v1/source-observations",
		location: "body",
		scopeMode: "none",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [202],
		emptyStatuses: []
	},
	commit_connector_checkpoint: {
		method: "POST",
		path: "/v1/connector-checkpoints/commit",
		location: "body",
		scopeMode: "none",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	query_code: {
		method: "POST",
		path: "/v1/scopes/{scope_id}/code/query",
		location: "body",
		scopeMode: "current",
		pathParameters: ["scope_id"],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	prepare_context: {
		method: "POST",
		path: "/v1/context/prepare",
		location: "body",
		scopeMode: "current",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	prepare_bootstrap_context: {
		method: "POST",
		path: "/v1/context/bootstrap",
		location: "body",
		scopeMode: "current",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	record_bootstrap_delivery: {
		method: "POST",
		path: "/v1/context/bootstrap/receipts",
		location: "body",
		scopeMode: "current",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	create_work_contract: {
		method: "POST",
		path: "/v1/work/contracts/create",
		location: "body",
		scopeMode: "current",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [202],
		emptyStatuses: []
	},
	handoff_current_work: {
		method: "POST",
		path: "/v1/work/handoffs/prepare-current",
		location: "body",
		scopeMode: "current",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	acknowledge_handoff: {
		method: "POST",
		path: "/v1/work/handoffs/acknowledge",
		location: "body",
		scopeMode: "current",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	record_task_outcome: {
		method: "POST",
		path: "/v1/work/outcomes/record",
		location: "body",
		scopeMode: "current",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [202],
		emptyStatuses: []
	},
	activate_handoff: {
		method: "POST",
		path: "/v1/handoff/activate",
		location: "body",
		scopeMode: "current",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	prepare_handoff: {
		method: "POST",
		path: "/v1/handoff/prepare",
		location: "body",
		scopeMode: "current",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	finalize_handoff: {
		method: "POST",
		path: "/v1/handoff/finalize",
		location: "body",
		scopeMode: "current",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	commit_handoff: {
		method: "POST",
		path: "/v1/handoff/commit",
		location: "body",
		scopeMode: "current",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	continue_handoff: {
		method: "POST",
		path: "/v1/handoff/continue",
		location: "body",
		scopeMode: "current",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	prepare_handoff_hint: {
		method: "POST",
		path: "/v1/handoff/hint",
		location: "body",
		scopeMode: "current",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	flush_topic_memory: {
		method: "POST",
		path: "/v1/topic-memory/flush",
		location: "body",
		scopeMode: "current",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	search_artifacts: {
		method: "POST",
		path: "/v1/scopes/{scope_id}/artifacts/{family}/search",
		location: "body",
		scopeMode: "none",
		pathParameters: ["scope_id", "family"],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	get_topic_memory_processing_status: {
		method: "POST",
		path: "/v1/topic-memory/processing/status",
		location: "body",
		scopeMode: "current",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	get_topic_memory_processing_block: {
		method: "POST",
		path: "/v1/topic-memory/processing/blocks/get",
		location: "body",
		scopeMode: "current",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	retry_topic_memory_processing: {
		method: "POST",
		path: "/v1/topic-memory/processing/retry",
		location: "body",
		scopeMode: "current",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	search_topic_memory: {
		method: "POST",
		path: "/v1/topic-memory/search",
		location: "body",
		scopeMode: "current",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	get_topic_memory: {
		method: "POST",
		path: "/v1/topic-memory/get",
		location: "body",
		scopeMode: "current",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	flush_memory: {
		method: "POST",
		path: "/v1/memory/flush",
		location: "body",
		scopeMode: "current",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	remember_memory: {
		method: "POST",
		path: "/v1/memory/remember",
		location: "body",
		scopeMode: "current",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	search_memory: {
		method: "POST",
		path: "/v1/memory/search",
		location: "body",
		scopeMode: "current",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	get_memory_capacity: {
		method: "POST",
		path: "/v1/memory/capacity",
		location: "body",
		scopeMode: "none",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	list_memory_entries: {
		method: "POST",
		path: "/v1/memory/entries/list",
		location: "body",
		scopeMode: "current",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	get_memory_entry: {
		method: "POST",
		path: "/v1/memory/entries/get",
		location: "body",
		scopeMode: "current",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	revise_memory_entry: {
		method: "POST",
		path: "/v1/memory/entries/revise",
		location: "body",
		scopeMode: "none",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	retire_memory_entry: {
		method: "POST",
		path: "/v1/memory/entries/retire",
		location: "body",
		scopeMode: "none",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	list_memory_changes: {
		method: "POST",
		path: "/v1/memory/changes",
		location: "body",
		scopeMode: "none",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	list_dream_runs: {
		method: "GET",
		path: "/v1/scopes/{scope_id}/dream",
		location: "query",
		scopeMode: "none",
		pathParameters: ["scope_id"],
		queryParams: [
			"status",
			"operation",
			"cursor",
			"limit"
		],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	create_dream_run: {
		method: "POST",
		path: "/v1/scopes/{scope_id}/dream",
		location: "body",
		scopeMode: "none",
		pathParameters: ["scope_id"],
		queryParams: [],
		headerParams: [],
		successStatuses: [202, 200],
		emptyStatuses: []
	},
	get_dream_run: {
		method: "GET",
		path: "/v1/scopes/{scope_id}/dream/{run_id}",
		location: null,
		scopeMode: "none",
		pathParameters: ["scope_id", "run_id"],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	propose_experience: {
		method: "POST",
		path: "/v1/experience/propose",
		location: "body",
		scopeMode: "current",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [201],
		emptyStatuses: []
	},
	generate_experience: {
		method: "POST",
		path: "/v1/experience/generate",
		location: "body",
		scopeMode: "current",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	get_experience: {
		method: "POST",
		path: "/v1/experience/get",
		location: "body",
		scopeMode: "current",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	propose_skill: {
		method: "POST",
		path: "/v1/skill/propose",
		location: "body",
		scopeMode: "current",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [201],
		emptyStatuses: []
	},
	generate_skill: {
		method: "POST",
		path: "/v1/skill/generate",
		location: "body",
		scopeMode: "current",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	get_skill: {
		method: "POST",
		path: "/v1/skill/get",
		location: "body",
		scopeMode: "current",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	list_managed_skills: {
		method: "POST",
		path: "/v1/skill/library",
		location: "body",
		scopeMode: "current",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	update_skill_lifecycle: {
		method: "POST",
		path: "/v1/skill/lifecycle",
		location: "body",
		scopeMode: "current",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	get_skill_package_manifest: {
		method: "POST",
		path: "/v1/skill/package/manifest",
		location: "body",
		scopeMode: "current",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	download_skill_package: {
		method: "POST",
		path: "/v1/skill/package/download",
		location: "body",
		scopeMode: "current",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	propose_skill_package: {
		method: "POST",
		path: "/v1/skill/package/propose",
		location: "body",
		scopeMode: "current",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [201],
		emptyStatuses: []
	},
	record_skill_usage: {
		method: "POST",
		path: "/v1/skill/usage",
		location: "body",
		scopeMode: "current",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [201],
		emptyStatuses: []
	},
	list_remote_skill_targets: {
		method: "POST",
		path: "/v1/skill/remote/targets",
		location: "body",
		scopeMode: "current",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	create_remote_skill_target: {
		method: "POST",
		path: "/v1/skill/remote/target/create",
		location: "body",
		scopeMode: "current",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [201],
		emptyStatuses: []
	},
	enroll_remote_skill_target: {
		method: "POST",
		path: "/v1/skill/remote/target/enroll",
		location: "body",
		scopeMode: "none",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	rename_remote_skill_target: {
		method: "POST",
		path: "/v1/skill/remote/target/rename",
		location: "body",
		scopeMode: "current",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	revoke_remote_skill_target: {
		method: "POST",
		path: "/v1/skill/remote/target/revoke",
		location: "body",
		scopeMode: "current",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	publish_remote_skill: {
		method: "POST",
		path: "/v1/skill/remote/publication/publish",
		location: "body",
		scopeMode: "current",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	unpublish_remote_skill: {
		method: "POST",
		path: "/v1/skill/remote/publication/unpublish",
		location: "body",
		scopeMode: "current",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	reconcile_remote_skills: {
		method: "POST",
		path: "/v1/skill/remote/reconcile",
		location: "body",
		scopeMode: "none",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	download_remote_skill_package: {
		method: "POST",
		path: "/v1/skill/remote/package/download",
		location: "body",
		scopeMode: "none",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	record_remote_skill_receipt: {
		method: "POST",
		path: "/v1/skill/remote/receipt",
		location: "body",
		scopeMode: "none",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	scan_external_skills: {
		method: "POST",
		path: "/v1/external-skills/scan",
		location: "body",
		scopeMode: "current",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	list_external_skills: {
		method: "POST",
		path: "/v1/external-skills/list",
		location: "body",
		scopeMode: "current",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	resolve_external_skill: {
		method: "POST",
		path: "/v1/external-skills/resolve",
		location: "body",
		scopeMode: "current",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	import_external_skill: {
		method: "POST",
		path: "/v1/external-skills/import",
		location: "body",
		scopeMode: "current",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	list_candidates: {
		method: "POST",
		path: "/v1/candidates/list",
		location: "body",
		scopeMode: "current",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	get_candidate: {
		method: "POST",
		path: "/v1/candidates/get",
		location: "body",
		scopeMode: "current",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	approve_candidate: {
		method: "POST",
		path: "/v1/candidates/approve",
		location: "body",
		scopeMode: "current",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	reject_candidate: {
		method: "POST",
		path: "/v1/candidates/reject",
		location: "body",
		scopeMode: "current",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	revise_candidate: {
		method: "POST",
		path: "/v1/candidates/revise",
		location: "body",
		scopeMode: "current",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	get_candidate_history: {
		method: "POST",
		path: "/v1/candidates/history",
		location: "body",
		scopeMode: "current",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	get_stats: {
		method: "POST",
		path: "/v1/stats",
		location: "body",
		scopeMode: "selection",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	get_handoff_report: {
		method: "POST",
		path: "/v1/handoff-reports/get",
		location: "body",
		scopeMode: "selection",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	list_sources: {
		method: "GET",
		path: "/v1/scopes/{scope_id}/sources",
		location: "query",
		scopeMode: "none",
		pathParameters: ["scope_id"],
		queryParams: ["limit", "cursor"],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	create_source: {
		method: "POST",
		path: "/v1/scopes/{scope_id}/sources",
		location: "body",
		scopeMode: "none",
		pathParameters: ["scope_id"],
		queryParams: [],
		headerParams: [],
		successStatuses: [201],
		emptyStatuses: []
	},
	get_source: {
		method: "GET",
		path: "/v1/scopes/{scope_id}/sources/{source_type}/{source_id}",
		location: null,
		scopeMode: "none",
		pathParameters: [
			"scope_id",
			"source_type",
			"source_id"
		],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	create_artifact: {
		method: "POST",
		path: "/v1/scopes/{scope_id}/artifacts",
		location: "body",
		scopeMode: "none",
		pathParameters: ["scope_id"],
		queryParams: [],
		headerParams: [],
		successStatuses: [201],
		emptyStatuses: []
	},
	list_artifacts: {
		method: "GET",
		path: "/v1/scopes/{scope_id}/artifacts/{family}",
		location: "query",
		scopeMode: "none",
		pathParameters: ["scope_id", "family"],
		queryParams: [
			"tag",
			"tag_match",
			"limit",
			"cursor"
		],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	resolve_artifact_handle: {
		method: "GET",
		path: "/v1/scopes/{scope_id}/artifact-handles/{family}/{key}",
		location: null,
		scopeMode: "none",
		pathParameters: [
			"scope_id",
			"family",
			"key"
		],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	bind_artifact_handle: {
		method: "PUT",
		path: "/v1/scopes/{scope_id}/artifact-handles/{family}/{key}",
		location: "body",
		scopeMode: "none",
		pathParameters: [
			"scope_id",
			"family",
			"key"
		],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	get_artifact: {
		method: "GET",
		path: "/v1/scopes/{scope_id}/artifacts/{family}/{artifact_id}",
		location: null,
		scopeMode: "current",
		pathParameters: [
			"scope_id",
			"family",
			"artifact_id"
		],
		queryParams: [],
		headerParams: ["If-None-Match"],
		successStatuses: [200, 304],
		emptyStatuses: [304]
	},
	replace_artifact: {
		method: "PUT",
		path: "/v1/scopes/{scope_id}/artifacts/{family}/{artifact_id}",
		location: "body",
		scopeMode: "current",
		pathParameters: [
			"scope_id",
			"family",
			"artifact_id"
		],
		queryParams: [],
		headerParams: ["If-Match"],
		successStatuses: [200],
		emptyStatuses: []
	},
	get_artifact_tags: {
		method: "GET",
		path: "/v1/scopes/{scope_id}/artifacts/{family}/{artifact_id}/tags",
		location: null,
		scopeMode: "none",
		pathParameters: [
			"scope_id",
			"family",
			"artifact_id"
		],
		queryParams: [],
		headerParams: ["If-None-Match"],
		successStatuses: [200, 304],
		emptyStatuses: [304]
	},
	replace_artifact_tags: {
		method: "PUT",
		path: "/v1/scopes/{scope_id}/artifacts/{family}/{artifact_id}/tags",
		location: "body",
		scopeMode: "none",
		pathParameters: [
			"scope_id",
			"family",
			"artifact_id"
		],
		queryParams: [],
		headerParams: ["If-Match"],
		successStatuses: [200],
		emptyStatuses: []
	},
	get_memory_entry_tags: {
		method: "GET",
		path: "/v1/scopes/{scope_id}/artifacts/memory/{artifact_id}/entries/{entry_id}/tags",
		location: null,
		scopeMode: "none",
		pathParameters: [
			"scope_id",
			"artifact_id",
			"entry_id"
		],
		queryParams: [],
		headerParams: ["If-None-Match"],
		successStatuses: [200, 304],
		emptyStatuses: [304]
	},
	replace_memory_entry_tags: {
		method: "PUT",
		path: "/v1/scopes/{scope_id}/artifacts/memory/{artifact_id}/entries/{entry_id}/tags",
		location: "body",
		scopeMode: "none",
		pathParameters: [
			"scope_id",
			"artifact_id",
			"entry_id"
		],
		queryParams: [],
		headerParams: ["If-Match"],
		successStatuses: [200],
		emptyStatuses: []
	},
	query_artifact_tags: {
		method: "POST",
		path: "/v1/scopes/{scope_id}/artifact-tags/query",
		location: "body",
		scopeMode: "none",
		pathParameters: ["scope_id"],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	get_artifact_revision: {
		method: "GET",
		path: "/v1/scopes/{scope_id}/artifacts/{family}/{artifact_id}/revisions/{revision}",
		location: null,
		scopeMode: "current",
		pathParameters: [
			"scope_id",
			"family",
			"artifact_id",
			"revision"
		],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	list_artifact_revisions: {
		method: "GET",
		path: "/v1/scopes/{scope_id}/artifacts/{family}/{artifact_id}/revisions",
		location: "query",
		scopeMode: "none",
		pathParameters: [
			"scope_id",
			"family",
			"artifact_id"
		],
		queryParams: ["limit", "cursor"],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	get_prompt_configuration: {
		method: "GET",
		path: "/v1/scopes/{scope_id}/prompts/{prompt_key}",
		location: null,
		scopeMode: "none",
		pathParameters: ["scope_id", "prompt_key"],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	generate_prompt_demonstrations: {
		method: "POST",
		path: "/v1/scopes/{scope_id}/prompts/{prompt_key}/demonstrations",
		location: "body",
		scopeMode: "none",
		pathParameters: ["scope_id", "prompt_key"],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	get_access_principal: {
		method: "GET",
		path: "/v1/access/me",
		location: null,
		scopeMode: "none",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	check_access: {
		method: "POST",
		path: "/v1/access/check",
		location: "body",
		scopeMode: "none",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	list_access_resources: {
		method: "POST",
		path: "/v1/access/resources/list",
		location: "body",
		scopeMode: "none",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	list_access_roles: {
		method: "POST",
		path: "/v1/access/roles/list",
		location: "body",
		scopeMode: "none",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	list_access_bindings: {
		method: "POST",
		path: "/v1/access/bindings/list",
		location: "body",
		scopeMode: "none",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	create_access_binding: {
		method: "POST",
		path: "/v1/access/bindings/create",
		location: "body",
		scopeMode: "none",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [201],
		emptyStatuses: []
	},
	revoke_access_binding: {
		method: "POST",
		path: "/v1/access/bindings/revoke",
		location: "body",
		scopeMode: "none",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	replace_access_binding: {
		method: "POST",
		path: "/v1/access/bindings/replace",
		location: "body",
		scopeMode: "none",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	list_access_audit: {
		method: "POST",
		path: "/v1/access/audit/list",
		location: "body",
		scopeMode: "none",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	pc_source_capabilities: {
		method: "POST",
		path: "/v1/source-management/capabilities",
		location: "body",
		scopeMode: "none",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	pc_source_add: {
		method: "POST",
		path: "/v1/source-management/add",
		location: "body",
		scopeMode: "none",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	pc_source_status: {
		method: "POST",
		path: "/v1/source-management/status",
		location: "body",
		scopeMode: "none",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	pc_source_target_set_enabled: {
		method: "POST",
		path: "/v1/source-management/target-set-enabled",
		location: "body",
		scopeMode: "none",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	pc_source_instances: {
		method: "POST",
		path: "/v1/source-management/instances",
		location: "body",
		scopeMode: "none",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	pc_source_instance_set_enabled: {
		method: "POST",
		path: "/v1/source-management/instance-set-enabled",
		location: "body",
		scopeMode: "none",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	source_configure: {
		method: "POST",
		path: "/v1/source-management/configure",
		location: "body",
		scopeMode: "none",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	load_source_write_plugin: {
		method: "POST",
		path: "/v1/source-management/write/load",
		location: "body",
		scopeMode: "none",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	source_channel_open: {
		method: "POST",
		path: "/v1/source-management/channel/open",
		location: "body",
		scopeMode: "none",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	source_channel_catalog: {
		method: "POST",
		path: "/v1/source-management/channel/catalog",
		location: "body",
		scopeMode: "none",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	source_channel_poll: {
		method: "POST",
		path: "/v1/source-management/channel/poll",
		location: "body",
		scopeMode: "none",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	source_channel_reply: {
		method: "POST",
		path: "/v1/source-management/channel/reply",
		location: "body",
		scopeMode: "none",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	source_channel_close: {
		method: "POST",
		path: "/v1/source-management/channel/close",
		location: "body",
		scopeMode: "none",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	source_instance_exchange: {
		method: "POST",
		path: "/v1/source-management/instances/{instance_id}/{operation}",
		location: "body",
		scopeMode: "none",
		pathParameters: ["instance_id", "operation"],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	}
};
const OPERATION_IDS = Object.keys(OPERATIONS);

//#endregion
//#region src/transport.ts
function optionalBoolean(value, name) {
	if (value === void 0) return void 0;
	if (typeof value !== "boolean") throw new Error(`${name} must be a boolean`);
	return value;
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

//#endregion
//#region src/client.ts
const MIN_READINESS_TIMEOUT_MS = 4e4;
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
function timeoutSignal(ms) {
	if (typeof AbortSignal.timeout === "function") return AbortSignal.timeout(ms);
	const controller = new AbortController();
	setTimeout(() => controller.abort(), ms);
	return controller.signal;
}
function concatBytes(chunks, total) {
	const out = new Uint8Array(total);
	let offset = 0;
	for (const chunk of chunks) {
		out.set(chunk, offset);
		offset += chunk.byteLength;
	}
	return out;
}
function responsePath(response) {
	try {
		return response.url ? new URL(response.url).pathname : "/";
	} catch {
		return "/";
	}
}
async function readLimitedBody(response, maxBytes = MAX_RESPONSE_BYTES) {
	if (!response.body) {
		const buffer = new Uint8Array(await response.arrayBuffer());
		if (buffer.byteLength > maxBytes) throw new InvalidResponseError(responsePath(response), void 0, void 0, "response_too_large");
		return buffer;
	}
	const reader = response.body.getReader();
	const chunks = [];
	let total = 0;
	while (true) {
		const { done, value } = await reader.read();
		if (done) break;
		total += value.byteLength;
		if (total > maxBytes) {
			await reader.cancel();
			throw new InvalidResponseError(responsePath(response), void 0, void 0, "response_too_large");
		}
		chunks.push(value);
	}
	return concatBytes(chunks, total);
}
function decodeError(bytes) {
	try {
		const parsed = JSON.parse(Buffer.from(bytes).toString("utf8"));
		const details = parsed.error?.details;
		return {
			code: parsed.error?.code,
			message: parsed.error?.message,
			...isPlainObject(details) ? { details } : {}
		};
	} catch {
		return {};
	}
}
function isPlainObject(value) {
	return typeof value === "object" && value !== null && !Array.isArray(value);
}
function queryString(payload) {
	const params = new URLSearchParams();
	for (const [key, value] of Object.entries(payload ?? {})) {
		if (value === void 0 || value === null) continue;
		for (const item of Array.isArray(value) ? value : [value]) params.append(key, String(item));
	}
	const encoded = params.toString();
	return encoded ? `?${encoded}` : "";
}
function encodePathSegment(value) {
	return encodeURIComponent(String(value)).replace(/[!'()*]/g, (character) => `%${character.charCodeAt(0).toString(16).toUpperCase()}`);
}
function headerPayloadKey(name) {
	return name.toLowerCase().replaceAll("-", "_");
}
function prepareRequest(spec, payload) {
	const remaining = { ...payload ?? {} };
	let path = spec.path;
	for (const name of spec.pathParameters) {
		const value = remaining[name];
		if (value === void 0 || value === null) throw new TypeError(`${spec.method} ${spec.path} requires ${name}`);
		path = path.replace(`{${name}}`, encodePathSegment(value));
		delete remaining[name];
	}
	const headers = {};
	for (const name of spec.headerParams) {
		const alias = headerPayloadKey(name);
		const value = remaining[name] ?? remaining[alias];
		delete remaining[name];
		delete remaining[alias];
		if (value !== void 0 && value !== null) headers[name] = String(value);
	}
	const queryPayload = {};
	for (const name of spec.queryParams) {
		const value = remaining[name];
		delete remaining[name];
		if (value !== void 0 && value !== null) queryPayload[name] = value;
	}
	return {
		path,
		query: queryString(queryPayload),
		headers,
		body: spec.location === "body" ? remaining : void 0
	};
}
function hasStatus(statuses, status) {
	return statuses.includes(status);
}
function isRedirect(status) {
	return status >= 300 && status < 400;
}
var PowerContextClient = class {
	baseUrl;
	userAgent;
	authorization;
	requestTimeoutMs;
	fetchImpl;
	constructor(options) {
		this.baseUrl = normalizeServerUrl(options.baseUrl, options.allowInsecureHttp);
		this.userAgent = options.userAgent ?? PLUGIN_USER_AGENT;
		this.authorization = options.authorization;
		this.requestTimeoutMs = options.requestTimeoutMs;
		this.fetchImpl = options.fetch ?? fetch;
	}
	requestTimeoutMsFor(id) {
		return id === "get_readiness" ? Math.max(this.requestTimeoutMs, MIN_READINESS_TIMEOUT_MS) : this.requestTimeoutMs;
	}
	async request(id, payload, signal, options = {}) {
		if (!(id in OPERATIONS)) throw new UnknownOperationError(id);
		const spec = OPERATIONS[id];
		const prepared = prepareRequest(spec, payload);
		const url = `${this.baseUrl}${prepared.path}${prepared.query}`;
		const init = this.buildInit(spec, prepared, signal, this.requestTimeoutMsFor(id));
		if (init.signal?.aborted) throw new RequestNotSentError(prepared.path, this.transportCause(void 0, init.signal));
		try {
			const response = await this.fetchImpl(url, init);
			return await this.parseResponse(id, spec, payload, response, options.readinessResponse === true, init.signal);
		} catch (error) {
			if (error instanceof ServerResponseError || error instanceof InvalidResponseError) throw error;
			if (error instanceof UnknownOperationError) throw error;
			throw this.wrapTransport(prepared.path, error, init.signal);
		}
	}
	async readOpenApi(signal) {
		const path = "/openapi.json";
		const spec = OPERATIONS.get_liveness;
		const init = this.buildInit(spec, {
			path,
			query: "",
			headers: {},
			body: void 0
		}, signal);
		if (init.signal?.aborted) throw new RequestNotSentError(path, this.transportCause(void 0, init.signal));
		try {
			const response = await this.fetchImpl(this.baseUrl + path, init);
			return await this.parseResponse("openapi_document", {
				...spec,
				path
			}, void 0, response, false, init.signal);
		} catch (error) {
			if (error instanceof ServerResponseError || error instanceof InvalidResponseError) throw error;
			throw this.wrapTransport(path, error, init.signal);
		}
	}
	buildInit(spec, request, signal, requestTimeoutMs = this.requestTimeoutMs) {
		const headers = {
			"X-PowerContext-Dream-Contract": "2",
			Accept: "application/json",
			"User-Agent": this.userAgent,
			...request.headers
		};
		if (this.authorization) headers.Authorization = this.authorization;
		const init = {
			method: spec.method,
			headers,
			redirect: "manual",
			signal: combineSignals([timeoutSignal(requestTimeoutMs), ...signal ? [signal] : []])
		};
		if (spec.location === "body") {
			headers["Content-Type"] = "application/json";
			init.body = JSON.stringify(request.body ?? {});
		}
		return init;
	}
	transportCause(error, signal) {
		if (!signal?.aborted) return error;
		return new DOMException("HTTP operation stopped", signal.reason instanceof Error && signal.reason.name === "TimeoutError" ? "TimeoutError" : "AbortError");
	}
	wrapTransport(path, error, signal) {
		if (error instanceof TransportError) return error;
		return new UnavailableError(path, this.transportCause(error, signal));
	}
	async parseResponse(id, spec, payload, response, readinessResponse = false, signal) {
		const success = response.status >= 200 && response.status < 300 || hasStatus(spec.successStatuses, response.status) || readinessResponse && id === "get_readiness" && response.status === 503;
		const requestId = safeRequestId(response.headers.get(REQUEST_ID_HEADER) ?? void 0);
		if (isRedirect(response.status) && !success) throw new InvalidResponseError(spec.path, requestId, response.status, "redirect");
		let bytes;
		try {
			bytes = await readLimitedBody(response);
		} catch (error) {
			if (error instanceof InvalidResponseError) throw new InvalidResponseError(spec.path, requestId, response.status, error.issue);
			throw new ResponseReadError(spec.path, this.transportCause(error, signal), response.status, requestId);
		}
		if (!success) throw this.httpError(response.status, spec.path, requestId, bytes);
		if (hasStatus(spec.emptyStatuses, response.status)) {
			if (bytes.byteLength !== 0) throw new InvalidResponseError(spec.path, requestId, response.status, "unexpected_body");
			return {
				kind: "json",
				value: null,
				status: response.status,
				requestId,
				etag: response.headers.get("ETag") ?? void 0
			};
		}
		if (id === "get_handoff_report" && payload?.download === true) return {
			kind: "bytes",
			value: bytes,
			status: response.status,
			requestId
		};
		if (id === "get_handoff_report" && payload?.format !== "json") return {
			kind: "text",
			value: Buffer.from(bytes).toString("utf8"),
			status: response.status,
			requestId
		};
		try {
			return {
				kind: "json",
				value: JSON.parse(Buffer.from(bytes).toString("utf8")),
				status: response.status,
				requestId,
				etag: response.headers.get("ETag") ?? void 0
			};
		} catch {
			throw new InvalidResponseError(spec.path, requestId, response.status, "invalid_json");
		}
	}
	httpError(status, path, requestId, bytes) {
		const decoded = decodeError(bytes);
		return new ServerResponseError({
			statusCode: status,
			path,
			requestId,
			code: decoded.code,
			message: decoded.message,
			details: decoded.details
		});
	}
};

//#endregion
//#region src/prepared-context.ts
const PREPARED_CONTEXT_SCHEMA = "powercontext.prepared-context.v1";
const PREPARED_FIELDS = new Set([
	"schema",
	"status",
	"content",
	"content_bytes"
]);
function isRecord(value) {
	return typeof value === "object" && value !== null && !Array.isArray(value);
}
function validatePreparedContext(response, path = "/v1/context/prepare", maxBytes = MAX_CONTEXT_BYTES) {
	if (!isRecord(response)) throw new InvalidResponseError(path, void 0, void 0, "prepared_object");
	const keys = Object.keys(response);
	if (keys.length !== PREPARED_FIELDS.size || keys.some((key) => !PREPARED_FIELDS.has(key))) throw new InvalidResponseError(path, void 0, void 0, "prepared_fields");
	if (response.schema !== PREPARED_CONTEXT_SCHEMA) throw new InvalidResponseError(path, void 0, void 0, "prepared_schema");
	const status = response.status;
	const content = response.content;
	const contentBytes = response.content_bytes;
	if (typeof contentBytes !== "number" || !Number.isInteger(contentBytes) || contentBytes < 0) throw new InvalidResponseError(path, void 0, void 0, "prepared_size");
	if (status === "empty") {
		if (content !== null || contentBytes !== 0) throw new InvalidResponseError(path, void 0, void 0, "prepared_empty");
		return {
			schema: PREPARED_CONTEXT_SCHEMA,
			status,
			content: null,
			content_bytes: 0
		};
	}
	if (status !== "ready" || typeof content !== "string" || !content.trim()) throw new InvalidResponseError(path, void 0, void 0, "prepared_content");
	if (Buffer.from(content, "utf8").byteLength !== contentBytes || contentBytes > maxBytes) throw new InvalidResponseError(path, void 0, void 0, "prepared_bytes");
	return {
		schema: PREPARED_CONTEXT_SCHEMA,
		status,
		content,
		content_bytes: contentBytes
	};
}

//#endregion
//#region src/secrets.ts
const SECRET_MARKERS = [
	"sk-",
	"api_key",
	"BEGIN PRIVATE"
];
function containsSecret(text) {
	return SECRET_MARKERS.some((marker) => text.includes(marker));
}

//#endregion
//#region ../../../cindy/src/worker.ts
const CONTEXT_START = "<!-- powercontext:cindy-context:v1 -->";
const CONTEXT_END = "<!-- /powercontext:cindy-context:v1 -->";
const HOOK_BUDGET_MS = 1800;
function authorization(config, raw) {
	if (config.allow_insecure_http !== void 0 && typeof config.allow_insecure_http !== "boolean") throw new Error("invalid_transport_consent");
	const baseUrl = normalizeServerUrl(config.server_url ?? "http://127.0.0.1:8000", config.allow_insecure_http ?? false);
	if (config.auth_mode === void 0 || config.auth_mode === "none") return { baseUrl };
	if (config.auth_mode !== "bearer" || !raw) throw new Error("credential_missing");
	const credential = JSON.parse(raw);
	if (!credential || typeof credential !== "object" || normalizeServerUrl(credential.server_url, config.allow_insecure_http ?? false) !== baseUrl || typeof credential.authorization !== "string") throw new Error("credential_mismatch");
	const token = credential.authorization.replace(/^Bearer /i, "");
	if (!/^[\x21-\x7e]+$/.test(token) || token.toLowerCase() === "bearer") throw new Error("invalid_credential");
	return {
		baseUrl,
		header: `Bearer ${token}`
	};
}
function createClient(config, credential, fetchImpl) {
	if (config.scope_id !== void 0 && (typeof config.scope_id !== "string" || !config.scope_id.trim() || config.scope_id.length > 256)) throw new Error("invalid_scope");
	const connection = authorization(config, credential);
	return new PowerContextClient({
		baseUrl: connection.baseUrl,
		authorization: connection.header,
		allowInsecureHttp: config.allow_insecure_http ?? false,
		requestTimeoutMs: 6e3,
		userAgent: "powercontext-cindy-hooks/0.1.0",
		fetch: fetchImpl
	});
}
async function resolveScope(client, config, sessionId, signal) {
	const resolved = await client.request("resolve_scope_binding", {
		allow_default: false,
		...config.scope_id ? { explicit_scope_id: config.scope_id } : {},
		binding_keys: [{
			integration: "cindy",
			kind: "session",
			external_id: sessionId
		}]
	}, signal);
	const value = resolved.kind === "json" ? resolved.value : void 0;
	if (!value || typeof value.scope_id !== "string" || !value.scope_id.trim()) throw new Error("invalid_scope");
	return value.scope_id;
}
function failureCode(error) {
	if (error instanceof ServerResponseError) {
		if (error.statusCode === 401) return "authentication_failed";
		if (error.statusCode === 403) return "authorization_failed";
		if (error.code === "scope_binding_target_missing") return "scope_binding_target_missing";
		if (error.code === "scope_not_found") return "scope_not_found";
	}
	return "operation_unavailable";
}
async function runReadTool(input, credential, fetchImpl) {
	if (!input?.config || typeof input.session_id !== "string" || !input.session_id.trim() || input.session_id.length > 256) return {
		ok: false,
		code: "session_unavailable"
	};
	const args = input.args ?? {};
	if (typeof args !== "object" || Array.isArray(args) || !args || ![
		"resolve_scope",
		"prepare_context",
		"search_memory"
	].includes(input.tool)) return {
		ok: false,
		code: "invalid_tool"
	};
	const allowed = input.tool === "resolve_scope" ? [] : input.tool === "prepare_context" ? ["query", "max_bytes"] : [
		"query",
		"limit",
		"mode"
	];
	if (Object.keys(args).some((key) => !allowed.includes(key))) return {
		ok: false,
		code: "invalid_arguments"
	};
	if (input.tool !== "resolve_scope" && (typeof args.query !== "string" || !args.query.trim() || args.query.length > 8192)) return {
		ok: false,
		code: "invalid_arguments"
	};
	const maxBytes = args.max_bytes ?? input.config.max_bytes ?? 6e3;
	if (input.tool === "prepare_context" && (!Number.isInteger(maxBytes) || Number(maxBytes) < 512 || Number(maxBytes) > 8192)) return {
		ok: false,
		code: "invalid_arguments"
	};
	if (input.tool === "search_memory" && (args.limit !== void 0 && (!Number.isInteger(args.limit) || Number(args.limit) < 1 || Number(args.limit) > 50) || args.mode !== void 0 && ![
		"auto",
		"fts",
		"vector",
		"hybrid"
	].includes(String(args.mode)))) return {
		ok: false,
		code: "invalid_arguments"
	};
	let client;
	try {
		const connection = authorization(input.config, credential);
		if (typeof args.query === "string" && (containsSecret(args.query) || connection.header && args.query.includes(connection.header.slice(7)) || /\b(?:Bearer\s+\S+|gh[pousr]_\S+)/.test(args.query))) return {
			ok: false,
			code: "sensitive_content"
		};
		client = createClient(input.config, credential, fetchImpl);
	} catch {
		return {
			ok: false,
			code: "invalid_connection"
		};
	}
	try {
		const signal = AbortSignal.timeout(6e3);
		const scopeId = await resolveScope(client, input.config, input.session_id, signal);
		if (input.tool === "resolve_scope") return {
			ok: true,
			data: { scope_id: scopeId }
		};
		const response = input.tool === "prepare_context" ? await client.request("prepare_context", {
			scope_id: scopeId,
			query: args.query,
			max_bytes: maxBytes
		}, signal) : await client.request("search_memory", {
			...args,
			scope_id: scopeId
		}, signal);
		if (response.kind !== "json") return {
			ok: false,
			code: "invalid_response"
		};
		if (input.tool === "prepare_context") validatePreparedContext(response.value, void 0, Number(maxBytes));
		return {
			ok: true,
			data: {
				scope_id: scopeId,
				result: response.value
			}
		};
	} catch (error) {
		return {
			ok: false,
			code: failureCode(error)
		};
	}
}
async function runHook(input, credential, fetchImpl) {
	const outcome = {
		ok: true,
		prepare: "skipped",
		capture: "skipped"
	};
	if (!input?.config || input.config.enabled !== true) return outcome;
	const remaining = input.deadline_at === void 0 ? HOOK_BUDGET_MS : Math.min(HOOK_BUDGET_MS, input.deadline_at - Date.now());
	if (!Number.isFinite(remaining) || remaining <= 0) return {
		...outcome,
		code: "hook_deadline",
		capture: "not_sent"
	};
	if (typeof input.text !== "string" || !input.text.trim() || typeof input.session_id !== "string" || !input.session_id.trim() || input.session_id.length > 256 || typeof input.hook_id !== "string" || !input.hook_id.trim() || input.hook_id.length > 256) return {
		...outcome,
		ok: false,
		code: "invalid_hook"
	};
	if (input.text.includes(CONTEXT_START) || input.text.includes(CONTEXT_END)) return {
		...outcome,
		code: "prepared_context_present"
	};
	let connection;
	try {
		connection = authorization(input.config, credential);
	} catch {
		return {
			...outcome,
			ok: false,
			code: "invalid_connection"
		};
	}
	if (containsSecret(input.text) || connection.header && input.text.includes(connection.header.slice(7)) || /\b(?:Bearer\s+\S+|gh[pousr]_\S+)/.test(input.text)) return {
		...outcome,
		code: "sensitive_content"
	};
	const maxBytes = input.config.max_bytes ?? 6e3;
	if (!Number.isInteger(maxBytes) || maxBytes < 512 || maxBytes > 8192 || input.config.capture_prompts !== void 0 && typeof input.config.capture_prompts !== "boolean" || input.config.scope_id !== void 0 && (typeof input.config.scope_id !== "string" || !input.config.scope_id.trim())) return {
		...outcome,
		ok: false,
		code: "invalid_configuration"
	};
	let client;
	try {
		client = createClient(input.config, credential, fetchImpl);
	} catch {
		return {
			...outcome,
			ok: false,
			code: "invalid_connection"
		};
	}
	const controller = new AbortController();
	const timer = setTimeout(() => controller.abort(), remaining);
	try {
		let scopeId;
		try {
			scopeId = await resolveScope(client, input.config, input.session_id, controller.signal);
			outcome.scope_id = scopeId;
		} catch (error) {
			return {
				...outcome,
				ok: false,
				code: failureCode(error)
			};
		}
		if (input.text.length > 8192) outcome.prepare = "query_too_long";
		else try {
			const signal = combineSignals([controller.signal, AbortSignal.timeout(800)]);
			const response = await client.request("prepare_context", {
				scope_id: scopeId,
				query: input.text,
				max_bytes: maxBytes
			}, signal);
			const prepared = validatePreparedContext(response.kind === "json" ? response.value : void 0, void 0, maxBytes);
			outcome.prepare = prepared.status;
			if (prepared.content) outcome.content = prepared.content;
		} catch {
			outcome.prepare = "unavailable";
		}
		if (input.config.capture_prompts === false) outcome.capture = "disabled";
		else if (input.text.length > MAX_SOURCE_LENGTH) outcome.capture = "source_too_long";
		else {
			const identity = [
				scopeId,
				input.session_id,
				input.hook_id,
				input.text
			].join("\0");
			outcome.source_id = `cindy-user-prompt:${(0, node_crypto.createHash)("sha256").update(identity).digest("hex")}`;
			try {
				const response = await client.request("capture_content_source", {
					scope_id: scopeId,
					source_id: outcome.source_id,
					content: input.text,
					metadata: {
						origin: "cindy",
						event: "user_prompt_submit",
						capture_stage: "will-user-message-input",
						session_id: input.session_id,
						hook_id: input.hook_id
					}
				}, controller.signal);
				const value = response.kind === "json" ? response.value : void 0;
				if (!value || typeof value.position !== "number" || !Number.isInteger(value.position) || value.position < 1) throw new Error("capture_not_confirmed");
				outcome.capture = "accepted";
				outcome.source_position = value.position;
			} catch (error) {
				outcome.capture = error instanceof RequestNotSentError ? "not_sent" : error instanceof ServerResponseError && error.statusCode < 500 ? "rejected" : "unknown";
			}
		}
		return outcome;
	} finally {
		clearTimeout(timer);
	}
}
if (require.main === module) (0, node_readline.createInterface)({
	input: process.stdin,
	crlfDelay: Infinity
}).on("line", async (line) => {
	let request;
	try {
		request = JSON.parse(line);
	} catch {
		return;
	}
	if (request.id === void 0) return;
	let result;
	try {
		if (request.jsonrpc !== "2.0" || ![
			"powercontext/hook",
			"powercontext/authenticated-hook",
			"powercontext/read",
			"powercontext/authenticated-read"
		].includes(request.method ?? "")) throw new Error("invalid_request");
		const input = request.params;
		if (input.config.auth_mode === "bearer" && !request.method.startsWith("powercontext/authenticated-")) throw new Error("invalid_request");
		result = request.method.endsWith("read") ? await runReadTool(input, request.cindy?.secrets?.server_credential) : await runHook(input, request.cindy?.secrets?.server_credential);
	} catch {
		result = {
			ok: false,
			code: "hook_unavailable",
			prepare: "skipped",
			capture: "skipped"
		};
	}
	process.stdout.write(`${JSON.stringify({
		jsonrpc: "2.0",
		id: request.id,
		result
	})}\n`);
});

//#endregion
exports.CONTEXT_END = CONTEXT_END;
exports.CONTEXT_START = CONTEXT_START;
exports.HOOK_BUDGET_MS = HOOK_BUDGET_MS;
exports.runHook = runHook;
exports.runReadTool = runReadTool;
