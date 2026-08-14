import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { profileNodeModulesDir } from '../src/peers.ts'

describe('profileNodeModulesDir', () => {
  it('resolves peer modules under the web profile by default', () => {
    expect(profileNodeModulesDir({ DSH_HOME: '/tmp/dsh-home' } as NodeJS.ProcessEnv)).toBe(
      join('/tmp/dsh-home', 'profiles', 'web', 'node_modules'),
    )
  })

  it('honors DSH_PROFILE when the host uses a non-default profile', () => {
    expect(profileNodeModulesDir({
      DSH_HOME: '/tmp/dsh-home',
      DSH_PROFILE: 'desktop',
    } as NodeJS.ProcessEnv)).toBe(join('/tmp/dsh-home', 'profiles', 'desktop', 'node_modules'))
  })
})
