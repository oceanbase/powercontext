import { describe, expect, it } from 'vitest'
import { resolveConfig } from '../src/config.ts'

describe('Pi configuration', () => {
  it('reads PowerContext Pi environment overrides', () => {
    expect(resolveConfig({
      POWERCONTEXT_PI_BASE_URL: 'https://memory.example.test/',
      POWERCONTEXT_PI_SCOPE_ID: 'project:demo',
      POWERCONTEXT_PI_AUTHORIZATION: 'Bearer token',
      POWERCONTEXT_PI_CAPTURE_PROMPTS: 'false',
      POWERCONTEXT_PI_REQUEST_TIMEOUT_MS: '1200',
      POWERCONTEXT_PI_HTTP_BUDGET_MS: '5000',
      POWERCONTEXT_PI_MAX_BYTES: '12000',
    })).toEqual({
      baseUrl: 'https://memory.example.test',
      scopeId: 'project:demo',
      authorization: 'Bearer token',
      capturePrompts: false,
      requestTimeoutMs: 1200,
      httpBudgetMs: 5000,
      maxBytes: 12000,
      flushOnCapture: false,
      flushMaxCalls: 4,
    })
  })
})
