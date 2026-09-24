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
