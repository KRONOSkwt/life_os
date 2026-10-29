// Real Neon verification. Run only against a disposable lifeos-setup-* branch.
import assert from 'node:assert/strict'
import { randomBytes, randomUUID } from 'node:crypto'
import { spawn } from 'node:child_process'
import { createServer } from 'node:net'
import { mkdir, writeFile } from 'node:fs/promises'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import { setTimeout as delay } from 'node:timers/promises'
import { neonCommand, root } from './neon.mjs'

if (!process.env.NEON_BRANCH?.startsWith('lifeos-setup-')) {
  throw new Error('Usa el entorno de una rama desechable lifeos-setup-*; no ejecutar sobre production.')
}
const require = createRequire(new URL('../apps/web/package.json', import.meta.url))
const { chromium, expect } = require('@playwright/test')
const reservation = createServer()
await new Promise(resolve => reservation.listen(0, '127.0.0.1', resolve))
const port = reservation.address().port
await new Promise(resolve => reservation.close(resolve))
const token = randomBytes(32).toString('base64url')
const settings = neonCommand('serve', { ...process.env, LIFE_OS_DEV_TOKEN: token, LIFE_OS_PORT: String(port) })
const base = `http://127.0.0.1:${port}`
let child, closed, browser
let logs = ''
const headers = { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }

async function stop() {
  if (child && child.exitCode === null) child.kill()
  if (closed) await Promise.race([closed, delay(5000, undefined, { ref: false }).then(() => { throw new Error('No se cerró la API de prueba') })])
}
async function start() {
  child = spawn(settings.command, settings.args, { cwd: root, env: settings.env, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] })
  closed = new Promise(resolve => child.once('close', resolve))
  child.stdout.on('data', chunk => { logs = (logs + chunk).slice(-12000) })
  child.stderr.on('data', chunk => { logs = (logs + chunk).slice(-12000) })
  let error
  child.on('error', problem => { error = problem })
  for (let attempt = 0; attempt < 100; attempt++) {
    if (error || child.exitCode !== null) throw new Error('La API Neon no pudo arrancar')
    try {
      if ((await fetch(`${base}/health`, { signal: AbortSignal.timeout(1000) })).ok) return
    } catch { /* Wait for this process. */ }
    await delay(200)
  }
  throw new Error('La API Neon no respondió a tiempo')
}
try {
  await start()
  browser = await chromium.launch({ channel: process.env.LIFE_OS_BROWSER_CHANNEL || 'msedge', headless: true })
  const page = await browser.newPage({ viewport: { width: 1365, height: 900 } })
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  await page.goto(`${base}/#entrenamiento`)
  await page.getByLabel('Clave de acceso').fill(token)
  await page.getByRole('button', { name: 'Acceder', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Cerrar sesión', exact: true })).toBeVisible()
  const name = `Prueba Neon ${randomUUID().slice(0, 8)}`
  await page.getByLabel('Nombre de la rutina').fill(name)
  await page.getByLabel('Ejercicio', { exact: true }).first().fill('Ejercicio de prueba')
  const response = page.waitForResponse(item => item.url().endsWith('/api/v1/sync/push'))
  await page.getByRole('button', { name: 'Guardar rutina', exact: true }).click()
  const routine = (await (await response).json()).results[0].record
  await expect(page.getByText('Rutina guardada.', { exact: true })).toBeVisible()
  console.log('OK: navegador -> FastAPI -> Neon pooled: rutina creada y consultada.')
  const now = new Date().toISOString()
  const payload = { protocol_version: 1, operations: [{
    operation_id: randomUUID(), entity_type: 'session', entity_id: randomUUID(), base_version: 0, action: 'upsert',
    data: { routine_id: routine.entity_id, routine_version: routine.version, routine_snapshot: routine.data,
      started_at: now, finished_at: now, sets: [{ id: randomUUID(), exercise_id: routine.data.exercises[0].id,
        weight: '1.250', unit: 'kg', reps: 3, kind: 'work' }] },
  }] }
  async function push() {
    const result = await fetch(`${base}/api/v1/sync/push`, { method: 'POST', headers, body: JSON.stringify(payload) })
    assert.equal(result.status, 200)
    return result.json()
  }
  const receipt = await push()
  await stop()
  await start()
  assert.deepEqual(await push(), receipt)
  const pull = await fetch(`${base}/api/v1/sync/pull`, { headers })
  const changes = (await pull.json()).changes
  const sessions = changes.filter(item => item.entity_id === payload.operations[0].entity_id)
  assert.equal(sessions.length, 1)
  assert.equal(sessions[0].data.sets[0].weight, '1.250')
  await page.getByRole('button', { name: 'Actualizar', exact: true }).click()
  await page.getByRole('navigation', { name: 'Navegación principal' }).getByRole('link', { name: 'Historial', exact: true }).click()
  const card = page.getByRole('region', { name: 'Historial de sesiones', exact: true }).getByRole('article')
    .filter({ has: page.getByRole('heading', { name, exact: true }) })
  await expect(card).toHaveCount(1)
  await expect(card).toContainText('1 serie registrada')
  await expect(card).toContainText('Finalizada')
  assert.deepEqual(errors, [])
  await mkdir(new URL('../.cache/', import.meta.url), { recursive: true })
  await page.screenshot({ path: fileURLToPath(new URL('../.cache/neon-browser-e2e.png', import.meta.url)), fullPage: true })
  await writeFile(new URL('../.cache/neon-e2e-result.json', import.meta.url), JSON.stringify({
    branch: process.env.NEON_BRANCH, verified: ['pooled API', 'browser routine', 'exact session', 'process restart', 'idempotent replay', 'browser history'],
    javascriptErrors: 0, completedAt: new Date().toISOString(),
  }, null, 2))
  console.log('OK: sesión exacta 1.250; reinicio, reenvío sin duplicados e historial web verificados.')
} catch (error) {
  console.error(logs)
  throw error
} finally {
  if (browser) await browser.close()
  await stop()
}
