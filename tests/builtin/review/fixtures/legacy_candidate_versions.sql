-- Copyright (c) 2026 OceanBase.
--
-- Licensed under the Apache License, Version 2.0 (the "License");
-- you may not use this file except in compliance with the License.
-- You may obtain a copy of the License at
--
-- http://www.apache.org/licenses/LICENSE-2.0
--
-- Unless required by applicable law or agreed to in writing, software
-- distributed under the License is distributed on an "AS IS" BASIS,
-- WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
-- See the License for the specific language governing permissions and
-- limitations under the License.

CREATE TABLE pc_artifact_candidate_versions (
	scope_id VARCHAR(256) NOT NULL,
	candidate_id VARCHAR(128) NOT NULL,
	version INTEGER NOT NULL,
	family VARCHAR(128) NOT NULL,
	proposal BLOB NOT NULL,
	source_refs BLOB NOT NULL,
	artifact_refs BLOB NOT NULL,
	memory_citations BLOB,
	target_family VARCHAR(128),
	target_artifact_id VARCHAR(128),
	target_revision INTEGER,
	reason TEXT,
	PRIMARY KEY (scope_id, candidate_id, version),
	FOREIGN KEY(scope_id, target_family, target_artifact_id, target_revision) REFERENCES pc_artifacts (scope_id, family, artifact_id, revision) ON DELETE RESTRICT,
	CONSTRAINT ck_pc_artifact_candidate_versions_version_positive CHECK (version > 0),
	CONSTRAINT ck_pc_artifact_candidate_versions_target_complete CHECK ((target_family IS NULL AND target_artifact_id IS NULL AND target_revision IS NULL) OR (target_family IS NOT NULL AND target_artifact_id IS NOT NULL AND target_revision > 0))
);
