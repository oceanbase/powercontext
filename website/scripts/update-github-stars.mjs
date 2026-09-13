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

export async function updateGitHubStars(github, { owner, repo }, now = Date.now()) {
  const repository = `${owner}/${repo}`.toLowerCase();
  const branch = 'website-stats';
  const path = 'github-stars.json';
  const { data } = await github.rest.repos.get({ owner, repo });
  if (!Number.isSafeInteger(data.stargazers_count) || data.stargazers_count < 0) {
    throw new Error('GitHub returned an invalid Star count; keeping the published snapshot');
  }
  const snapshot = { repository, count: data.stargazers_count, updatedAt: now };
  const content = `${JSON.stringify(snapshot)}\n`;
  const message = 'chore: refresh website GitHub stars';

  let branchExists = true;
  try {
    await github.rest.git.getRef({ owner, repo, ref: `heads/${branch}` });
  } catch (error) {
    if (error.status !== 404) throw error;
    branchExists = false;
  }

  if (!branchExists) {
    // Bootstrap an orphan data-only branch, without copying the website or source history.
    const tree = await github.rest.git.createTree({
      owner, repo, tree: [{ path, mode: '100644', type: 'blob', content }],
    });
    const commit = await github.rest.git.createCommit({ owner, repo, message, tree: tree.data.sha, parents: [] });
    await github.rest.git.createRef({ owner, repo, ref: `refs/heads/${branch}`, sha: commit.data.sha });
    return snapshot;
  }

  const previous = await github.rest.repos.getContent({ owner, repo, path, ref: branch });
  if (previous.data.type !== 'file') throw new Error('Expected a snapshot file');
  const saved = JSON.parse(Buffer.from(previous.data.content, 'base64').toString('utf8'));
  // Avoid empty commits when the count is unchanged, while retaining a daily freshness marker.
  if (saved?.repository === repository && saved.count === snapshot.count
    && Number.isFinite(saved.updatedAt) && saved.updatedAt > now - 24 * 60 * 60 * 1000
    && saved.updatedAt <= now) return saved;

  await github.rest.repos.createOrUpdateFileContents({
    owner, repo, branch, path, sha: previous.data.sha, message, content: Buffer.from(content).toString('base64'),
  });
  return snapshot;
}
