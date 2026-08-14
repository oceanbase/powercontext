import { createRequire } from 'node:module'
import { homedir } from 'node:os'
import { join } from 'node:path'
import { pathToFileURL } from 'node:url'

export function profileNodeModulesDir(env: NodeJS.ProcessEnv = process.env): string {
  const home = env.DSH_HOME?.trim() || join(homedir(), '.dsh')
  const profile = env.DSH_PROFILE?.trim() || 'web'
  return join(home, 'profiles', profile, 'node_modules')
}

function profileModulesAnchor(env: NodeJS.ProcessEnv = process.env): string {
  return join(profileNodeModulesDir(env), 'powercontext-dsh-resolver.cjs')
}

function resolvePeer(specifier: string): string {
  try {
    return createRequire(import.meta.url).resolve(specifier)
  } catch {
    return createRequire(profileModulesAnchor()).resolve(specifier)
  }
}

export async function loadPeer<T>(specifier: string): Promise<T> {
  const href = pathToFileURL(resolvePeer(specifier)).href
  return await import(href) as T
}
