// Copyright (c) 2026 OceanBase.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
// http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

// Each caller owns a unique artifact name within the workflow run.
module.exports = async function artifactState({github, context, core, mode}) {
  const name = process.env.DIAGNOSTIC_ARTIFACT_NAME;
  const list = async () => (await github.paginate(github.rest.actions.listWorkflowRunArtifacts, {
    ...context.repo,
    run_id: context.runId,
    name,
    per_page: 100,
    request: {signal: AbortSignal.timeout(30_000)},
  })).filter(artifact => artifact.name === name);

  if (mode === 'snapshot') {
    core.setOutput('known', 'false');
    try {
      const artifacts = await list();
      core.setOutput('ids', JSON.stringify(artifacts.map(artifact => artifact.id)));
      core.setOutput('known', 'true');
    } catch (error) {
      core.warning(`Artifact baseline unavailable (status ${error.status || 'unknown'}); only uploader success can recover this call.`);
    }
    return;
  }

  core.setOutput('recovered', 'false');
  if (process.env.DIAGNOSTIC_BASELINE_KNOWN === 'true') {
    try {
      const before = new Set(JSON.parse(process.env.DIAGNOSTIC_BASELINE_IDS));
      const artifacts = (await list()).filter(artifact => !before.has(artifact.id));
      // Recover only a finalized archive; an incomplete upload is not successful evidence.
      if (artifacts.length === 1) {
        const artifact = artifacts[0];
        if (artifact.expired === false && artifact.size_in_bytes > 0 && /^sha256:[a-f0-9]{64}$/.test(artifact.digest || '')) {
          core.setOutput('artifact-id', artifact.id);
          core.setOutput('artifact-url', `${process.env.GITHUB_SERVER_URL}/${context.repo.owner}/${context.repo.repo}/actions/runs/${context.runId}/artifacts/${artifact.id}`);
          core.setOutput('artifact-digest', artifact.digest.slice('sha256:'.length));
          core.setOutput('recovered', 'true');
          core.info(`Recovered finalized diagnostic artifact ${artifact.id}; the original archive was preserved.`);
          return;
        }
      }
    } catch (error) {
      core.warning(`Artifact recovery lookup unavailable (status ${error.status || 'unknown'}); upload remains unsuccessful.`);
    }
  }
  const delay = Number(process.env.DIAGNOSTIC_RETRY_DELAY_MS || '0');
  if (delay > 0) {
    core.info(`Retrying diagnostic upload in ${delay / 1000} seconds.`);
    await new Promise(resolve => setTimeout(resolve, delay));
  }
};
