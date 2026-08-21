# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Audited, offline Gold-validation overrides for known dataset defects."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Literal

SOURCE559_INSTANCE_ID = "instance_gravitational__teleport-c335534e02de143508ebebc7341021d7f8656e8f"
SOURCE559_DATASET_PATCH_SHA256 = "de187c18609f9a6fdedca6fb8b0fb2beb381bca169f02fa21550f67072e4f464"
SOURCE559_REFERENCE_PATCH_SHA256 = "f4c611735a6dc7731d84bd4c01eacacf67bf1f93ef02f1db489723085a8d3fcb"
SOURCE559_REFERENCE_DATASET = "livesweagent/claude-sonnet-4-5_swebench_pro_traj"
SOURCE559_REFERENCE_REVISION = "e9c3cf3611956d75ad8a78b9cce5b4a524828e22"
SOURCE559_REFERENCE_FILE_OID = "7d910a550fc80f16647b795e2ab23fa032ac91fa"
SOURCE595_INSTANCE_ID = (
    "instance_ansible__ansible-de5858f48dc9e1ce9117034e0d7e76806f420ca8-v1055803c3a812189a1133297f7f5468579283f86"
)
SOURCE595_DATASET_PATCH_SHA256 = "f984e4a44cf8ce42671e5a4740656f99da379f829d312c6885f9d13ffb875c22"
SOURCE595_SELECTED_TEST_FILES = (
    '["test/integration/targets/ansible-galaxy-collection/library/setup_collections.py", '
    '"test/units/galaxy/test_api.py"]'
)
SOURCE595_EFFECTIVE_TEST_FILES = '["test/units/galaxy/test_api.py"]'
OFFICIAL_EVALUATION_DOCKER_PROXY = "docker_proxy"
OFFICIAL_EVALUATION_DIRECT = "direct"
OFFICIAL_EVALUATION_PROXY_BYPASSED = "proxy_bypassed_for_test_isolation"
OfficialEvaluationTransport = Literal["direct", "docker_proxy", "proxy_bypassed_for_test_isolation"]
OfficialEvaluationTestSelection = Literal[
    "dataset_selected_files",
    "required_unit_tests_only_for_invalid_integration_target",
]

# This patch is embedded to make the override deterministic and offline.
SOURCE559_REFERENCE_PATCH = """diff --git a/lib/client/keyagent.go b/lib/client/keyagent.go
index 37d7bfbc35..d368ed2cfd 100644
--- a/lib/client/keyagent.go
+++ b/lib/client/keyagent.go
@@ -19,6 +19,7 @@ package client
 import (
__CONTEXT_TAB__"context"
__CONTEXT_TAB__"crypto/subtle"
+	"crypto/x509"
__CONTEXT_TAB__"fmt"
__CONTEXT_TAB__"io"
__CONTEXT_TAB__"net"
@@ -554,3 +555,22 @@ func (a *LocalKeyAgent) certsForCluster(clusterName string) ([]ssh.Signer, error) {
__CONTEXT_TAB__}
__CONTEXT_TAB__return certs, nil
 }
+// ClientCertPool returns a *x509.CertPool populated with the trusted TLS
+// Certificate Authorities (CAs) for the specified Teleport cluster.
+func (a *LocalKeyAgent) ClientCertPool(cluster string) (*x509.CertPool, error) {
+	key, err := a.GetKey(cluster)
+	if err != nil {
+		return nil, trace.Wrap(err)
+	}
+
+	pool := x509.NewCertPool()
+	for _, ca := range key.TrustedCA {
+		for i, certPEM := range ca.TLSCertificates {
+			if !pool.AppendCertsFromPEM(certPEM) {
+				return nil, trace.BadParameter("failed to parse TLS CA certificate #%d for cluster %q", i, cluster)
+			}
+		}
+	}
+
+	return pool, nil
+}
__PLUS_SPACE__
diff --git a/lib/srv/alpnproxy/local_proxy.go b/lib/srv/alpnproxy/local_proxy.go
index c9df27f88f..83e4078c61 100644
--- a/lib/srv/alpnproxy/local_proxy.go
+++ b/lib/srv/alpnproxy/local_proxy.go
@@ -109,7 +109,7 @@ func NewLocalProxy(cfg LocalProxyConfig) (*LocalProxy, error) {
 // SSHProxy is equivalent of `ssh -o 'ForwardAgent yes' -p port  %r@host -s proxy:%h:%p` but established SSH
 // connection to RemoteProxyAddr is wrapped with TLS protocol.
 func (l *LocalProxy) SSHProxy() error {
-	if l.cfg.ClientTLSConfig != nil {
+	if l.cfg.ClientTLSConfig == nil {
__CONTEXT_TAB__	return trace.BadParameter("client TLS config is missing")
__CONTEXT_TAB__}
diff --git a/tool/tsh/proxy.go b/tool/tsh/proxy.go
index 40fb3df0f0..22c09b0951 100644
--- a/tool/tsh/proxy.go
+++ b/tool/tsh/proxy.go
@@ -17,6 +17,7 @@ limitations under the License.
 package main
__CONTEXT_SPACE__
 import (
+	"crypto/tls"
__CONTEXT_TAB__"fmt"
__CONTEXT_TAB__"net"
__CONTEXT_TAB__"os"
@@ -42,16 +43,42 @@ func onProxyCommandSSH(cf *CLIConf) error {
__CONTEXT_TAB__	return trace.Wrap(err)
__CONTEXT_TAB__}
__CONTEXT_SPACE__
+	// Get the local agent to access cluster CA certificates
+	localAgent := client.LocalAgent()
+	if localAgent == nil {
+		return trace.BadParameter("local agent is not available")
+	}
+
+	// Determine the cluster name for CA pool lookup
+	// Use the site name if specified, otherwise use empty string for root cluster
+	clusterName := cf.SiteName
+	if clusterName == "" {
+		clusterName = client.SiteName
+	}
+
+	// Get the CA pool for the cluster
+	pool, err := localAgent.ClientCertPool(clusterName)
+	if err != nil {
+		return trace.Wrap(err, "failed to load trusted CA certificates")
+	}
+
+	// Create TLS config with proper CA pool and ServerName for SNI
+	tlsConfig := &tls.Config{
+		RootCAs:    pool,
+		ServerName: address.Host(),
+	}
+
__CONTEXT_TAB__lp, err := alpnproxy.NewLocalProxy(alpnproxy.LocalProxyConfig{
__CONTEXT_TAB__	RemoteProxyAddr:    client.WebProxyAddr,
__CONTEXT_TAB__	Protocol:           alpncommon.ProtocolProxySSH,
__CONTEXT_TAB__	InsecureSkipVerify: cf.InsecureSkipVerify,
__CONTEXT_TAB__	ParentContext:      cf.Context,
__CONTEXT_TAB__	SNI:                address.Host(),
-		SSHUser:            cf.Username,
+		SSHUser:            client.Config.HostLogin,
__CONTEXT_TAB__	SSHUserHost:        cf.UserHost,
__CONTEXT_TAB__	SSHHostKeyCallback: client.HostKeyCallback,
__CONTEXT_TAB__	SSHTrustedCluster:  cf.SiteName,
+		ClientTLSConfig:    tlsConfig,
__CONTEXT_TAB__})
__CONTEXT_TAB__if err != nil {
__CONTEXT_TAB__	return trace.Wrap(err)
"""

# Keep the patch byte-for-byte identical to the fixed-revision submission.  The
# explicit replacements preserve the one whitespace-only diff context line.
SOURCE559_REFERENCE_PATCH = (
    SOURCE559_REFERENCE_PATCH.replace("([]ssh.Signer, error) {\n", "([]ssh.Signer, error\n")
    .replace("+}\n__PLUS_SPACE__\ndiff --git a/lib/srv", "+}\ndiff --git a/lib/srv")
    .replace("__CONTEXT_SPACE__\n", " \n")
    .replace("__CONTEXT_TAB__", " \t")
    .replace("\t}\ndiff --git a/tool/tsh", "\t}\n \ndiff --git a/tool/tsh")
)
SOURCE559_REFERENCE_PATCH += "\n"


@dataclass(frozen=True)
class GoldValidationSelection:
    """The immutable patch and audit metadata selected for Gold validation."""

    instance_id: str
    mode: str
    dataset_patch_sha256: str
    validation_patch: str
    validation_patch_sha256: str
    dataset_patch_status: str
    reference_validation_status: str
    attempt_gold_validation_status: str = "pending"
    official_evaluation_transport: OfficialEvaluationTransport = OFFICIAL_EVALUATION_DOCKER_PROXY
    official_evaluation_test_selection: OfficialEvaluationTestSelection = "dataset_selected_files"
    evaluator_selected_test_files_to_run: str | None = None
    source_dataset: str | None = None
    source_revision: str | None = None
    source_file_oid: str | None = None
    source_kind: str | None = None

    @property
    def audit(self) -> dict[str, str | None]:
        return {
            "instance_id": self.instance_id,
            "mode": self.mode,
            "dataset_patch_sha256": self.dataset_patch_sha256,
            "validation_patch_sha256": self.validation_patch_sha256,
            "dataset_patch_status": self.dataset_patch_status,
            "reference_validation_status": self.reference_validation_status,
            "attempt_gold_validation_status": self.attempt_gold_validation_status,
            "official_evaluation_transport": self.official_evaluation_transport,
            "official_evaluation_test_selection": self.official_evaluation_test_selection,
            "source_dataset": self.source_dataset,
            "source_revision": self.source_revision,
            "source_file_oid": self.source_file_oid,
            "source_kind": self.source_kind,
        }


class GoldValidationOverrideError(ValueError):
    """A fixed override or its source patch failed closed validation."""


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def select_gold_validation(
    instance_id: str,
    dataset_patch: str,
    selected_test_files_to_run: str | None = None,
) -> GoldValidationSelection:
    """Select the exact Gold patch without changing the supplied source row."""

    if not isinstance(instance_id, str) or not isinstance(dataset_patch, str):
        raise TypeError("Gold validation identity and patch must be text")
    dataset_hash = _sha256(dataset_patch)
    if instance_id == SOURCE595_INSTANCE_ID:
        if dataset_hash != SOURCE595_DATASET_PATCH_SHA256:
            raise GoldValidationOverrideError("source595 dataset Gold patch hash does not match the pinned source")
        if selected_test_files_to_run != SOURCE595_SELECTED_TEST_FILES:
            raise GoldValidationOverrideError("source595 selected test files do not match the pinned source")
        return GoldValidationSelection(
            instance_id=instance_id,
            mode="dataset_patch",
            dataset_patch_sha256=dataset_hash,
            validation_patch=dataset_patch,
            validation_patch_sha256=dataset_hash,
            dataset_patch_status="unverified",
            reference_validation_status="not_applicable",
            official_evaluation_test_selection="required_unit_tests_only_for_invalid_integration_target",
            evaluator_selected_test_files_to_run=SOURCE595_EFFECTIVE_TEST_FILES,
        )
    if instance_id != SOURCE559_INSTANCE_ID:
        return GoldValidationSelection(
            instance_id=instance_id,
            mode="dataset_patch",
            dataset_patch_sha256=dataset_hash,
            validation_patch=dataset_patch,
            validation_patch_sha256=dataset_hash,
            dataset_patch_status="unverified",
            reference_validation_status="not_applicable",
        )
    if dataset_hash != SOURCE559_DATASET_PATCH_SHA256:
        raise GoldValidationOverrideError("source559 dataset Gold patch hash does not match the pinned source")
    reference_hash = _sha256(SOURCE559_REFERENCE_PATCH)
    if reference_hash != SOURCE559_REFERENCE_PATCH_SHA256:
        raise GoldValidationOverrideError("source559 reference Gold patch hash does not match the pinned source")
    return GoldValidationSelection(
        instance_id=instance_id,
        mode="verified_override",
        dataset_patch_sha256=dataset_hash,
        validation_patch=SOURCE559_REFERENCE_PATCH,
        validation_patch_sha256=reference_hash,
        dataset_patch_status="known_failed",
        reference_validation_status="passed",
        official_evaluation_transport=OFFICIAL_EVALUATION_PROXY_BYPASSED,
        source_dataset=SOURCE559_REFERENCE_DATASET,
        source_revision=SOURCE559_REFERENCE_REVISION,
        source_file_oid=SOURCE559_REFERENCE_FILE_OID,
        source_kind="verified_reference_submission",
    )
