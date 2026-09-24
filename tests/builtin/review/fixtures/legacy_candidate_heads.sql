CREATE TABLE pc_artifact_candidate_heads (
	scope_id VARCHAR(256) NOT NULL,
	candidate_id VARCHAR(128) NOT NULL,
	family VARCHAR(128) NOT NULL,
	version INTEGER NOT NULL,
	status VARCHAR(16) NOT NULL,
	result_family VARCHAR(128),
	result_artifact_id VARCHAR(128),
	result_revision INTEGER,
	decision_reason TEXT,
	PRIMARY KEY (scope_id, candidate_id),
	FOREIGN KEY(scope_id, candidate_id, version) REFERENCES pc_artifact_candidate_versions (scope_id, candidate_id, version) ON DELETE RESTRICT,
	FOREIGN KEY(scope_id, result_family, result_artifact_id, result_revision) REFERENCES pc_artifacts (scope_id, family, artifact_id, revision) ON DELETE RESTRICT,
	CONSTRAINT ck_pc_artifact_candidate_heads_status CHECK (status IN ('pending', 'approved', 'rejected')),
	CONSTRAINT ck_pc_artifact_candidate_heads_terminal_result CHECK ((status = 'approved' AND result_family IS NOT NULL AND result_artifact_id IS NOT NULL AND result_revision > 0 AND decision_reason IS NULL) OR (status = 'rejected' AND result_family IS NULL AND result_artifact_id IS NULL AND result_revision IS NULL AND decision_reason IS NOT NULL) OR (status = 'pending' AND result_family IS NULL AND result_artifact_id IS NULL AND result_revision IS NULL AND decision_reason IS NULL))
);
