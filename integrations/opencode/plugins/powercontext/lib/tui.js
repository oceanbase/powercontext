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
import { createHash } from "node:crypto";

//#region src/errors.ts
const REQUEST_ID_HEADER = "X-PowerContext-Request-ID";
const MAX_RESPONSE_BYTES = 1048576;
const PLUGIN_NAME = "powercontext-opencode";
const PLUGIN_VERSION = "0.0.1";
const PLUGIN_USER_AGENT = `${PLUGIN_NAME}/${PLUGIN_VERSION}`;
var ClientError = class extends Error {
	requestId;
	constructor(message, requestId) {
		super(message);
		this.name = new.target.name;
		this.requestId = requestId;
	}
};
var UnavailableError = class extends ClientError {
	path;
	constructor(path, cause) {
		super(`request to ${path} failed`);
		this.path = path;
		this.cause = cause;
	}
};
var InvalidResponseError = class extends ClientError {
	constructor(path, requestId) {
		super(`response from ${path} violated the API schema`, requestId);
		this.path = path;
	}
};
var UnknownOperationError = class extends ClientError {
	constructor(operationId) {
		super(`unknown PowerContext operation: ${operationId}`);
		this.operationId = operationId;
	}
};
var ServerResponseError = class extends ClientError {
	statusCode;
	code;
	serverMessage;
	constructor(options) {
		super(`PowerContext returned HTTP ${options.statusCode}${options.code ? ` (${options.code})` : ""}`, options.requestId);
		this.statusCode = options.statusCode;
		this.code = options.code;
		this.serverMessage = options.message;
	}
};

//#endregion
//#region src/operations.generated.ts
const OPERATIONS = {
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
		scopeMode: "current",
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
		scopeMode: "current",
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
		scopeMode: "current",
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
	list_artifact_candidates: {
		method: "POST",
		path: "/v1/artifact-candidates/list",
		location: "body",
		scopeMode: "current",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	get_artifact_candidate: {
		method: "POST",
		path: "/v1/artifact-candidates/get",
		location: "body",
		scopeMode: "current",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	approve_artifact_candidate: {
		method: "POST",
		path: "/v1/artifact-candidates/approve",
		location: "body",
		scopeMode: "current",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	reject_artifact_candidate: {
		method: "POST",
		path: "/v1/artifact-candidates/reject",
		location: "body",
		scopeMode: "current",
		pathParameters: [],
		queryParams: [],
		headerParams: [],
		successStatuses: [200],
		emptyStatuses: []
	},
	revise_artifact_candidate: {
		method: "POST",
		path: "/v1/artifact-candidates/revise",
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
	get_artifact: {
		method: "GET",
		path: "/v1/scopes/{scope_id}/artifacts/{family}/{artifact_id}",
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
	replace_artifact: {
		method: "PUT",
		path: "/v1/scopes/{scope_id}/artifacts/{family}/{artifact_id}",
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
		scopeMode: "none",
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
//#region src/client.ts
function combineSignals(signals) {
	if (signals.length === 1) return signals[0];
	if (typeof AbortSignal.any === "function") return AbortSignal.any([...signals]);
	const controller = new AbortController();
	for (const signal of signals) if (signal.aborted) controller.abort(signal.reason);
	else signal.addEventListener("abort", () => controller.abort(signal.reason), { once: true });
	return controller.signal;
}
function createTimeoutSignal(timeoutMs) {
	if (typeof AbortSignal.timeout === "function") return AbortSignal.timeout(timeoutMs);
	const controller = new AbortController();
	setTimeout(() => controller.abort(), timeoutMs).unref();
	return controller.signal;
}
async function readLimitedBody(response) {
	const declared = response.headers.get("content-length");
	const parsedLength = declared === null ? void 0 : Number(declared);
	const declaredBytes = parsedLength !== void 0 && Number.isFinite(parsedLength) && parsedLength >= 0 ? parsedLength : void 0;
	if (declaredBytes !== void 0 && declaredBytes > MAX_RESPONSE_BYTES) {
		try {
			await response.body?.cancel();
		} catch {}
		throw new InvalidResponseError("/");
	}
	if (!response.body) return new Uint8Array();
	const reader = response.body.getReader();
	const chunks = [];
	let length = 0;
	try {
		while (true) {
			const { done, value } = await reader.read();
			if (done) break;
			if (!value?.byteLength) continue;
			if (length + value.byteLength > MAX_RESPONSE_BYTES) {
				try {
					await reader.cancel();
				} catch {}
				throw new InvalidResponseError("/");
			}
			chunks.push(value);
			length += value.byteLength;
			if (declaredBytes === void 0 && length === MAX_RESPONSE_BYTES) {
				try {
					await reader.cancel();
				} catch {}
				throw new InvalidResponseError("/");
			}
		}
	} finally {
		reader.releaseLock();
	}
	const body = new Uint8Array(length);
	let offset = 0;
	for (const chunk of chunks) {
		body.set(chunk, offset);
		offset += chunk.byteLength;
	}
	return body;
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
	fetchImpl;
	constructor(options) {
		this.options = options;
		this.options = {
			...options,
			baseUrl: normalizeServerUrl(options.baseUrl, options.allowInsecureHttp)
		};
		this.fetchImpl = options.fetch ?? fetch;
	}
	async request(id, payload, signal) {
		if (!(id in OPERATIONS)) throw new UnknownOperationError(id);
		const spec = OPERATIONS[id];
		const prepared = prepareRequest(spec, payload);
		try {
			const response = await this.fetchImpl(this.url(prepared), this.init(spec, prepared, signal));
			const success = response.status >= 200 && response.status < 300 || hasStatus(spec.successStatuses, response.status);
			if (isRedirect(response.status) && !success) throw new InvalidResponseError(spec.path);
			const bytes = await readLimitedBody(response);
			const requestId = response.headers.get(REQUEST_ID_HEADER) ?? void 0;
			if (!success) {
				let error = {};
				try {
					error = JSON.parse(Buffer.from(bytes).toString("utf8"));
				} catch {}
				throw new ServerResponseError({
					statusCode: response.status,
					requestId,
					code: error.error?.code,
					message: error.error?.message
				});
			}
			if (hasStatus(spec.emptyStatuses, response.status)) {
				if (bytes.byteLength !== 0) throw new InvalidResponseError(spec.path, requestId);
				return {
					kind: "json",
					value: null,
					status: response.status,
					requestId,
					etag: response.headers.get("ETag") ?? void 0
				};
			}
			try {
				return {
					kind: "json",
					value: JSON.parse(Buffer.from(bytes).toString("utf8")),
					status: response.status,
					requestId,
					etag: response.headers.get("ETag") ?? void 0
				};
			} catch {
				throw new InvalidResponseError(spec.path, requestId);
			}
		} catch (error) {
			if (error instanceof ServerResponseError || error instanceof InvalidResponseError || error instanceof UnknownOperationError) throw error;
			throw new UnavailableError(prepared.path, error);
		}
	}
	url(request) {
		return `${this.options.baseUrl.replace(/\/+$/, "")}${request.path}${request.query}`;
	}
	init(spec, request, signal) {
		const headers = {
			Accept: "application/json",
			"User-Agent": PLUGIN_USER_AGENT,
			...request.headers
		};
		if (this.options.authorization) headers.Authorization = this.options.authorization;
		const signals = [createTimeoutSignal(this.options.requestTimeoutMs)];
		if (signal) signals.push(signal);
		const init = {
			method: spec.method,
			headers,
			redirect: "manual",
			signal: combineSignals(signals)
		};
		if (spec.location === "body") {
			headers["Content-Type"] = "application/json";
			init.body = JSON.stringify(request.body ?? {});
		}
		return init;
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
const WRITE_OPERATIONS = new Set([
	"remember_memory",
	"capture_content_source",
	"revise_memory_entry",
	"retire_memory_entry",
	"activate_handoff",
	"commit_handoff",
	"generate_experience",
	"generate_skill"
]);
function operationMutates(id) {
	return WRITE_OPERATIONS.has(id);
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
			code: error.code ?? "conflict",
			message: error.serverMessage ?? "Citation conflict; refresh and retry once.",
			status: 409,
			request_id: error.requestId
		};
		return {
			ok: false,
			code: error.code ?? (error.statusCode === 404 ? "not_found" : "invalid_request"),
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
async function handleDoctor(runtime, scopeId, signal) {
	const live = await invokeOperation(runtime.client, "get_liveness", {}, scopeId, signal);
	const ready = await invokeOperation(runtime.client, "get_readiness", {}, scopeId, signal);
	const ok = live.ok && ready.ok;
	return {
		kind: ok ? "success" : "error",
		text: formatResult({
			ok,
			data: {
				live,
				ready
			}
		})
	};
}
async function handlePcCommand(rawInput, runtime, scopeId, signal) {
	const tokens = rawInput.trim().split(/\s+/).filter(Boolean);
	const command = tokens[0];
	if (!command) return {
		kind: "success",
		text: `scope=${scopeId}\nbaseUrl=${runtime.config.baseUrl}\nUse /pc doctor to check Server readiness.`
	};
	if (command === "doctor") return handleDoctor(runtime, scopeId, signal);
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
