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

// Generated from integrations/lifecycle/git-worktree.ts; do not edit.
// Edit the source and run `make integration-lifecycle` instead.

import { execFile, execFileSync } from 'node:child_process'
import { realpathSync } from 'node:fs'
import { basename, dirname, isAbsolute, join, relative, resolve, sep } from 'node:path'
import { promisify } from 'node:util'

// Find the paths whose workspace bindings apply to a directory, so a linked Git worktree falls back to its checkout.
// src/powercontext_integration_lifecycle/git_worktree.py applies the same rule for the Python hosts.
//
// --path-format applies to the options after it, so the top level stays exactly what --show-toplevel alone prints.
// It needs Git 2.31 or later.
const GIT_ARGUMENTS = ['rev-parse', '--show-toplevel', '--path-format=absolute', '--git-common-dir']
// Older Git does not know --path-format and prints it back as an argument.
const UNKNOWN_FORMAT = '\n--path-format=absolute'
const runGit = promisify(execFile)

/**
 * Return the paths whose workspace bindings apply to `directory`, the worktree's own first.
 *
 * The first path is the one the host has always keyed: with `gitRoot`, the Git top level as Git prints it, or
 * `directory` outside a work tree; otherwise `directory` itself. In a linked worktree of a non-bare repository, a
 * second path follows from the main worktree: its root with `gitRoot`, otherwise the path at `directory`'s position in
 * it. Each host keys both paths the way it keys the first, so the second matches a binding made in the main checkout,
 * and resolves them in this order, so the worktree's own binding wins. Binding and clearing use only the first.
 *
 * The main worktree is the parent of Git's common directory when that directory is named `.git`; bare repositories,
 * submodules, and other layouts get no second path.
 */
export async function workspacePaths(directory, gitRoot, timeout) {
  try {
    const { stdout } = await runGit('git', ['-C', directory, ...GIT_ARGUMENTS], {
      encoding: 'utf8', timeout, windowsHide: true,
    })
    return pathsFromGit(directory, stdout, gitRoot)
  } catch {
    return pathsFromGit(directory, '', gitRoot)
  }
}

/** Return what `workspacePaths` returns, for hosts whose hooks compute their keys synchronously. */
export function workspacePathsSync(directory, gitRoot, timeout) {
  try {
    const stdout = execFileSync('git', ['-C', directory, ...GIT_ARGUMENTS], {
      encoding: 'utf8', timeout, windowsHide: true, stdio: ['ignore', 'pipe', 'ignore'],
    })
    return pathsFromGit(directory, stdout, gitRoot)
  } catch {
    return pathsFromGit(directory, '', gitRoot)
  }
}

function pathsFromGit(directory, output, gitRoot) {
  const lines = output.replace(/\n+$/u, '')
  const split = lines.lastIndexOf('\n')
  let head = split < 0 ? '' : lines.slice(0, split)
  let common = lines.slice(split + 1)
  if (head.endsWith(UNKNOWN_FORMAT)) {
    head = head.slice(0, -UNKNOWN_FORMAT.length)
    common = ''
  }
  const top = head.trim()
  const first = gitRoot && top ? top : directory
  const main = mainWorktreePath(directory, top, common, gitRoot)
  return main === undefined ? [first] : [first, main]
}

function mainWorktreePath(directory, top, common, gitRoot) {
  if (!top || !common || !isAbsolute(common)) return undefined
  const commonDirectory = realPath(common)
  if (basename(commonDirectory) !== '.git') return undefined
  const mainRoot = dirname(commonDirectory)
  const worktreeRoot = realPath(top)
  if (mainRoot === worktreeRoot) return undefined
  if (gitRoot) return mainRoot
  const position = relative(worktreeRoot, realPath(directory))
  if (position === '..' || position.startsWith('..' + sep) || isAbsolute(position)) return undefined
  return join(mainRoot, position)
}

function realPath(path) {
  try {
    return realpathSync(path)
  } catch {
    return resolve(path)
  }
}
