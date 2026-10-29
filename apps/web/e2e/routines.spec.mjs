import { randomUUID } from 'node:crypto'
import { readFileSync } from 'node:fs'
import { test, expect } from './fixtures.mjs'

const example = JSON.parse(readFileSync(new URL('../../../contracts/examples/create-routine.json', import.meta.url), 'utf8'))
const authorization = backend => ({ Authorization: `Bearer ${backend.token}` })
const library = page => page.getByRole('region', { name: 'Mis rutinas', exact: true })

async function connect(page, backend) {
  await page.getByLabel('Clave de acceso').fill(backend.token)
  await page.getByRole('button', { name: 'Acceder', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Cerrar sesión', exact: true })).toBeVisible()
}

async function fillRoutine(page, name) {
  await page.getByLabel('Nombre de la rutina').fill(name)
  await page.getByLabel('Ejercicio', { exact: true }).first().fill('Press de banca')
}

async function changes(request, backend) {
  const response = await request.get(`${backend.url}/api/v1/sync/pull`, { headers: authorization(backend) })
  expect(response.ok()).toBeTruthy()
  return (await response.json()).changes
}

test('recordar el acceso al recargar y cerrar sesión sin guardar la clave en JavaScript', async ({ page, backend }, testInfo) => {
  const name = `Push navegador ${randomUUID().slice(0, 8)}`
  await page.goto(`${backend.url}/#entrenamiento`)
  await page.getByLabel('Clave de acceso').fill('token-incorrecto')
  await page.getByRole('button', { name: 'Acceder', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('La clave de acceso no es válida')
  await connect(page, backend)
  await fillRoutine(page, name)
  await page.getByRole('button', { name: 'Añadir ejercicio' }).click()
  await page.getByLabel('Ejercicio', { exact: true }).nth(1).fill('Press de hombros')
  await page.getByLabel('Series', { exact: true }).nth(1).fill('4')
  await page.getByRole('button', { name: 'Guardar rutina', exact: true }).click()
  await expect(page.getByText('Rutina guardada.', { exact: true })).toBeVisible()
  const card = library(page).getByRole('article').filter({ has: page.getByRole('heading', { name, exact: true }) })
  await expect(card).toHaveCount(1)
  await expect(card.getByRole('listitem')).toHaveCount(2)
  await expect(card).toContainText('4 × 8–12')
  await page.reload()
  await expect(page.getByRole('button', { name: 'Cerrar sesión', exact: true })).toBeVisible()
  expect(await page.evaluate(() => JSON.stringify({ local: { ...localStorage }, session: { ...sessionStorage } }))).not.toContain(backend.token)
  expect(await page.evaluate(() => document.cookie)).not.toContain(backend.token)
  await expect(card).toHaveCount(1)
  await page.getByRole('button', { name: 'Cerrar sesión', exact: true }).click()
  await page.reload()
  await expect(page.getByLabel('Clave de acceso')).toHaveValue('')
  await connect(page, backend)
  await page.evaluate(() => window.scrollTo(0, 0))
  await page.screenshot({ path: testInfo.outputPath('rutinas-desktop.png'), fullPage: true })
})

test('recuperar tras recargar una creación confirmada cuya respuesta se perdió', async ({ page, request, backend }) => {
  const name = `Recuperación ${randomUUID().slice(0, 8)}`
  await page.goto(`${backend.url}/#entrenamiento`)
  await connect(page, backend)
  await fillRoutine(page, name)
  let submitted
  await page.route('**/api/v1/sync/push', async route => {
    submitted = route.request().postDataJSON()
    const response = await route.fetch()
    expect(response.status()).toBe(200)
    await route.abort('failed')
  })
  await page.getByRole('button', { name: 'Guardar rutina', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('No se pudo contactar')
  await expect(page.getByText('Una rutina pendiente de confirmar', { exact: true })).toBeVisible()
  const operation = submitted.operations[0]
  expect((await changes(request, backend)).filter(item => item.entity_id === operation.entity_id)).toHaveLength(1)
  await page.reload()
  await expect(page.getByRole('button', { name: 'Cerrar sesión', exact: true })).toBeVisible()
  await expect(page.getByLabel('Nombre de la rutina')).toHaveValue(name)
  await expect(page.getByLabel('Nombre de la rutina')).toBeDisabled()
  await expect(page.getByRole('button', { name: 'Cerrar sesión', exact: true })).toBeVisible()
  await page.unroute('**/api/v1/sync/push')
  const replay = page.waitForRequest(value => value.url().endsWith('/api/v1/sync/push'))
  await page.getByRole('button', { name: 'Reintentar guardado', exact: true }).click()
  expect((await replay).postDataJSON()).toEqual(submitted)
  await expect(page.getByText('Rutina guardada.', { exact: true })).toBeVisible()
  await expect(library(page).getByRole('heading', { name, exact: true })).toHaveCount(1)
  expect((await changes(request, backend)).filter(item => item.entity_id === operation.entity_id)).toHaveLength(1)
  expect(await page.evaluate(() => localStorage.getItem('life-os.pending-routine.v1'))).toBeNull()
})

test('consultar en pantalla estrecha una sesión real de la API con su snapshot', async ({ page, request, backend }, testInfo) => {
  const routine = structuredClone(example.operations[0])
  routine.operation_id = randomUUID()
  routine.entity_id = randomUUID()
  routine.data.name = `Sesión navegador ${randomUUID().slice(0, 8)}`
  const training = {
    operation_id: randomUUID(), entity_type: 'session', entity_id: randomUUID(), base_version: 0, action: 'upsert',
    data: {
      routine_id: routine.entity_id, routine_version: 1, routine_snapshot: routine.data,
      started_at: '2026-10-28T07:15:00-04:00', finished_at: '2026-10-28T08:00:00-04:00',
      sets: [{ id: randomUUID(), exercise_id: routine.data.exercises[0].id, weight: '22.500', unit: 'kg', reps: 10, kind: 'work' }],
    },
  }
  const response = await request.post(`${backend.url}/api/v1/sync/push`, {
    headers: authorization(backend), data: { protocol_version: 1, operations: [routine, training] },
  })
  expect(response.ok()).toBeTruthy()
  await page.setViewportSize({ width: 412, height: 915 })
  await page.goto(`${backend.url}/#historial`)
  await connect(page, backend)
  const history = page.getByRole('region', { name: 'Historial de sesiones', exact: true })
  const card = history.getByRole('article').filter({ has: page.getByRole('heading', { name: routine.data.name, exact: true }) })
  await expect(card).toContainText('1 serie registrada')
  await expect(card).toContainText('Finalizada')
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy()
  await page.screenshot({ path: testInfo.outputPath('rutinas-estrecho.png'), fullPage: true })
})


test('dashboard real y navegación entre módulos en escritorio y móvil', async ({ page, request, backend }, testInfo) => {
  await page.goto(backend.url)
  await expect(page.getByRole('heading', { name: 'Todo en su sitio.', exact: true })).toBeVisible()
  await expect(page.locator('.training-stats strong').first()).toHaveText('—')
  await connect(page, backend)
  const records = await changes(request, backend)
  const routines = records.filter(record => record.entity_type === 'routine' && !record.deleted)
  await expect(page.locator('.training-stats strong').first()).toHaveText(String(routines.length).padStart(2, '0'))
  await page.evaluate(() => window.scrollTo(0, 0))
  await page.screenshot({ path: testInfo.outputPath('dashboard-desktop.png'), fullPage: true })
  const nav = page.getByRole('navigation', { name: 'Navegación principal' })
  await nav.getByRole('link', { name: 'Entrenamiento', exact: true }).click()
  await expect(page.getByLabel('Nombre de la rutina')).toBeVisible()
  await page.getByLabel('Nombre de la rutina').fill('Borrador sin enviar')
  await nav.getByRole('link', { name: 'Salud y lesiones', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Un espacio que crecerá contigo.' })).toBeVisible()
  await nav.getByRole('link', { name: 'Entrenamiento', exact: true }).click()
  await expect(page.getByLabel('Nombre de la rutina')).toHaveValue('Borrador sin enviar')
  await page.setViewportSize({ width: 412, height: 915 })
  await page.setViewportSize({ width: 320, height: 800 })
  for (const name of ['Vista general', 'Finanzas', 'Deporte', 'Historial', 'Entrenamiento']) {
    await nav.getByRole('link', { name, exact: true }).click()
    await expect(nav.getByRole('link', { name, exact: true })).toHaveAttribute('aria-current', 'page')
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy()
  }
  await nav.getByRole('link', { name: 'Vista general', exact: true }).click()
  await page.setViewportSize({ width: 412, height: 915 })
  await page.screenshot({ path: testInfo.outputPath('dashboard-mobile.png'), fullPage: true })
  await expect(page.getByRole('button', { name: 'Cerrar sesión', exact: true })).toBeVisible()
})
