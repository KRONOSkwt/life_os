import { test as base, expect } from '@playwright/test'
import { randomBytes } from 'node:crypto'
import { spawn } from 'node:child_process'
import { mkdir, mkdtemp, rm } from 'node:fs/promises'
import { createServer } from 'node:net'
import { fileURLToPath } from 'node:url'
import path from 'node:path'
import { setTimeout as delay } from 'node:timers/promises'

const root = fileURLToPath(new URL('../../../', import.meta.url))

async function unusedPort() {
  const server = createServer()
  await new Promise((resolve, reject) => {
    server.once('error', reject)
    server.listen(0, '127.0.0.1', resolve)
  })
  const port = server.address().port
  await new Promise((resolve, reject) => server.close(error => error ? reject(error) : resolve()))
  return port
}

export const test = base.extend({
  backend: [async ({}, use) => {
    const cache = path.resolve(root, '.cache')
    await mkdir(cache, { recursive: true })
    const directory = await mkdtemp(path.join(cache, 'browser-backend-'))
    let child
    let closed
    try {
      const port = await unusedPort()
      const url = `http://127.0.0.1:${port}`
      const token = randomBytes(32).toString('base64url')
      const python = path.join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')
      child = spawn(python, ['-I', '-m', 'uvicorn', 'life_os.api:create_app', '--factory',
        '--host', '127.0.0.1', '--port', String(port), '--no-proxy-headers'], {
        cwd: path.join(root, 'apps/server'),
        env: { ...process.env, LIFE_OS_MODE: 'development', LIFE_OS_DATABASE: path.join(directory, 'browser.sqlite3'), LIFE_OS_DEV_TOKEN: token },
        windowsHide: true,
        stdio: ['ignore', 'pipe', 'pipe'],
      })
      let logs = ''
      let spawnError
      const capture = chunk => { logs = (logs + chunk.toString()).slice(-16000) }
      child.stdout.on('data', capture)
      child.stderr.on('data', capture)
      child.on('error', error => { spawnError = error })
      closed = new Promise(resolve => child.once('close', resolve))
      const deadline = Date.now() + 15000
      for (;;) {
        if (spawnError) throw spawnError
        if (child.exitCode !== null) throw new Error(`El backend terminó al arrancar:\n${logs}`)
        try {
          const response = await fetch(`${url}/health`, { signal: AbortSignal.timeout(1000) })
          if (response.ok && (await response.json()).status === 'ok') break
        } catch { /* Wait for this newly spawned server, not an existing instance. */ }
        if (Date.now() >= deadline) throw new Error(`El backend no arrancó en 15 segundos:\n${logs}`)
        await delay(100)
      }
      await use({ url, token })
    } finally {
      if (child) {
        if (child.exitCode === null && child.signalCode === null) child.kill()
        await Promise.race([
          closed,
          delay(5000, undefined, { ref: false }).then(() => { throw new Error('No se pudo detener el backend de prueba') }),
        ])
      }
      // Only remove the directory allocated by this fixture, inside the workspace cache.
      const target = path.resolve(directory)
      if (path.dirname(target) !== cache || !path.basename(target).startsWith('browser-backend-')) {
        throw new Error('Se rechazó la limpieza fuera del directorio temporal de prueba')
      }
      await rm(target, { recursive: true, force: true })
    }
  }, { scope: 'worker' }],

  browserErrors: [async ({ page }, use) => {
    const errors = []
    page.on('pageerror', error => errors.push(error.message))
    await use(errors)
    expect(errors, 'Errores de ejecución del frontend').toEqual([])
  }, { auto: true }],
})

export { expect }
