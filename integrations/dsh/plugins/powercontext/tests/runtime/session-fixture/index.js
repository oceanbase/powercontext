/* Test-only Host fixture: create genuine sessions for real-host session-switch acceptance. */

import { isAbsolute } from 'node:path'

export const name = 'powercontext-session-fixture'
export const inject = ['webServer', 'connection', 'sessions']

function json(response, status, body) {
  response.statusCode = status
  response.setHeader('content-type', 'application/json; charset=utf-8')
  response.setHeader('cache-control', 'no-store')
  response.end(JSON.stringify(body))
}

async function body(request) {
  const chunks = []
  let size = 0
  for await (const chunk of request) {
    size += chunk.byteLength
    if (size > 64 * 1024) return null
    chunks.push(chunk)
  }
  try { return JSON.parse(Buffer.concat(chunks).toString('utf8')) } catch { return null }
}

export function apply(ctx) {
  ctx.webServer.register({
    kind: 'exact',
    path: '/__powercontext_test/sessions',
    handler: async (request, response) => {
      const rejection = ctx.connection.requestRejection(request)
      if (rejection !== undefined) { json(response, rejection, { code: 'dsh_rejected' }); return }
      if (request.method !== 'POST') {
        response.statusCode = 405
        response.setHeader('allow', 'POST')
        response.end()
        return
      }
      const payload = await body(request)
      if (payload === null || typeof payload.cwd !== 'string' || !isAbsolute(payload.cwd)) {
        json(response, 400, { code: 'invalid_cwd' })
        return
      }
      const session = ctx.sessions.create(undefined, { meta: { cwd: payload.cwd } })
      json(response, 200, { id: session.header.id, cwd: session.header.cwd })
    },
  })
}
