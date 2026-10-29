import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'
import { createRoutine, isRoutine, isSession } from '../src/domain.ts'
import { clearPending, PENDING_KEY, readPending, writePending } from '../src/pending.ts'
import { SyncClient, SyncError } from '../src/sync.ts'

const example = JSON.parse(fs.readFileSync(new URL('../../../contracts/examples/create-routine.json', import.meta.url), 'utf8'))
const template = example.operations[0]
const token = 'test-token-only-'.repeat(3)
const response = value => new Response(JSON.stringify(value), { headers: { 'Content-Type': 'application/json' } })
const record = (operation = template, version = 1) => ({
  entity_type: 'routine', entity_id: operation.entity_id, version, deleted: false, data: operation.data,
})
const page = (changes, next = 'cursor-1', more = false) => ({
  protocol_version: 1, changes, next_cursor: next, has_more: more,
})
const receipt = operation => ({
  protocol_version: 1,
  results: [{ operation_id: operation.operation_id, status: 'applied', record: record(operation) }],
})
function storage() {
  const data = new Map()
  return {
    getItem: key => data.get(key) ?? null,
    setItem: (key, value) => data.set(key, value),
    removeItem: key => data.delete(key),
  }
}

test('shared routine example is valid and new operation owns a snapshot', () => {
  assert.ok(isRoutine(template.data))
  const draft = structuredClone(template.data)
  const operation = createRoutine(draft)
  draft.name = 'Edited later'
  assert.equal(operation.data.name, template.data.name)
  assert.notEqual(operation.operation_id, operation.entity_id)
  assert.throws(() => createRoutine({ ...draft, name: ' ' }))
})

test('fetch is invoked without binding it to the SyncClient instance', async () => {
  const client = new SyncClient(token, function () {
    assert.equal(this, undefined)
    return Promise.resolve(response(page([])))
  })
  assert.deepEqual(await client.records(), [])
})

test('lost response preserves exact operation across reload and retry', async () => {
  const local = storage()
  const operation = createRoutine(template.data)
  writePending(local, operation)
  const bodies = []
  const committed = new Map()
  const client = new SyncClient(token, async (url, init) => {
    assert.equal(url, '/api/v1/sync/push')
    assert.equal(init.headers.Authorization, `Bearer ${token}`)
    bodies.push(init.body)
    const item = JSON.parse(init.body).operations[0]
    committed.set(item.operation_id, receipt(item))
    if (bodies.length === 1) throw new TypeError('Response lost')
    return response(committed.get(item.operation_id))
  })
  await assert.rejects(client.create(operation), error => error instanceof SyncError && error.code === 'connection')
  const restored = readPending(local)
  assert.deepEqual(restored, operation)
  const result = await client.create(restored)
  assert.equal(result.status, 'applied')
  assert.equal(bodies[0], bodies[1])
  assert.equal(committed.size, 1)
  assert.ok(!local.getItem(PENDING_KEY).includes(token))
  clearPending(local, operation.operation_id)
  assert.equal(readPending(local), null)
})

test('pending operation is not replaced or cleared by another operation', () => {
  const local = storage()
  const first = createRoutine(template.data)
  const other = createRoutine(template.data)
  writePending(local, first)
  assert.throws(() => writePending(local, other), /otra rutina pendiente/)
  clearPending(local, other.operation_id)
  assert.deepEqual(readPending(local), first)
})

test('storage failure or corrupt pending content does not silently discard the draft', () => {
  const unavailable = { ...storage(), setItem() { throw new Error('Quota exceeded') } }
  assert.throws(() => writePending(unavailable, template), /no pudo conservar/)
  const local = storage()
  local.setItem(PENDING_KEY, '{broken')
  assert.throws(() => readPending(local), /No se pudo leer/)
  assert.equal(local.getItem(PENDING_KEY), '{broken')
})

test('download folds pages by entity and version and respects tombstones', async () => {
  const other = createRoutine({ ...template.data, name: 'Pull' })
  const pages = [
    page([record(template), record(other)], 'cursor + 1', true),
    page([{ ...record(template, 2), deleted: true, data: null }, record(other, 2)], 'cursor-2'),
  ]
  const paths = []
  const client = new SyncClient(token, async url => {
    paths.push(url)
    return response(pages.shift())
  })
  const records = await client.records()
  assert.equal(records.length, 1)
  assert.equal(records[0].entity_id, other.entity_id)
  assert.equal(records[0].version, 2)
  assert.equal(paths[1], '/api/v1/sync/pull?cursor=cursor%20%2B%201')
})

test('failed second page rejects instead of returning a partial view', async () => {
  let calls = 0
  const client = new SyncClient(token, async () => {
    if (++calls === 1) return response(page([record()], 'cursor-1', true))
    throw new TypeError('Disconnected')
  })
  await assert.rejects(client.records(), error => error.code === 'connection')
  assert.equal(calls, 2)
})

test('incremental refresh keeps an atomic cursor and cache across a failed page', async () => {
  const other = createRoutine({ ...template.data, name: 'Pull' })
  const deletion = { ...record(template, 2), deleted: true, data: null }
  const responses = [
    page([record(template), record(other)], 'initial'),
    page([], 'initial'),
    page([deletion], 'partial', true),
    new TypeError('response lost'),
    page([deletion, record(other, 2)], 'latest'),
    page([], 'latest'),
  ]
  const paths = []
  const client = new SyncClient(token, async path => {
    paths.push(path)
    const next = responses.shift()
    if (next instanceof Error) throw next
    return response(next)
  })
  const initial = await client.records()
  initial.find(item => item.entity_id === other.entity_id).data.name = 'Outside mutation'
  assert.equal((await client.records()).find(item => item.entity_id === other.entity_id).data.name, 'Pull')
  await assert.rejects(client.records(), error => error.code === 'connection')
  const recovered = await client.records()
  assert.equal(recovered.length, 1)
  assert.equal(recovered[0].entity_id, other.entity_id)
  assert.equal(recovered[0].version, 2)
  assert.deepEqual(await client.records(), recovered)
  assert.deepEqual(paths, [
    '/api/v1/sync/pull',
    '/api/v1/sync/pull?cursor=initial',
    '/api/v1/sync/pull?cursor=initial',
    '/api/v1/sync/pull?cursor=partial',
    '/api/v1/sync/pull?cursor=initial',
    '/api/v1/sync/pull?cursor=latest',
  ])
})

test('concurrent refreshes share one download and return independent views', async () => {
  let release
  const ready = new Promise(resolve => { release = resolve })
  let calls = 0
  const client = new SyncClient(token, async () => {
    calls++
    await ready
    return response(page([record()]))
  })
  const first = client.records()
  const second = client.records()
  assert.equal(calls, 1)
  release()
  const [a, b] = await Promise.all([first, second])
  assert.deepEqual(a, b)
  assert.notEqual(a[0], b[0])
})

test('repeating cursors cannot leave download in an infinite loop', async () => {
  let calls = 0
  const client = new SyncClient(token, async () => {
    calls++
    return response(page([record()], 'repeated', true))
  })
  await assert.rejects(client.records(), /no avanzó/)
  assert.equal(calls, 2)
})

test('invalid server response cannot confirm and clear a pending save', async () => {
  const local = storage()
  writePending(local, template)
  const wrong = receipt(template)
  wrong.results[0].operation_id = crypto.randomUUID()
  const client = new SyncClient(token, async () => response(wrong))
  await assert.rejects(client.create(template), error => error.code === 'response')
  assert.deepEqual(readPending(local), template)
})

test('receipt must match the entity and contain an actual created record', async () => {
  for (const invalid of [
    { ...record(), entity_id: crypto.randomUUID() },
    { ...record(), deleted: true, data: null },
    null,
  ]) {
    const value = receipt(template)
    value.results[0].record = invalid
    const client = new SyncClient(token, async () => response(value))
    await assert.rejects(client.create(template), error => error.code === 'response')
  }
})

test('conflict is returned for explicit review rather than automatically overwritten', async () => {
  let calls = 0
  const value = receipt(template)
  value.results[0].status = 'conflict'
  const client = new SyncClient(token, async () => { calls++; return response(value) })
  const result = await client.create(template)
  assert.equal(result.status, 'conflict')
  assert.equal(calls, 1)
})

test('credential, reused-ID and server errors have actionable messages without echoing secrets', async () => {
  for (const [status, code] of [[401, 'credentials'], [409, 'reused'], [500, 'request']]) {
    const client = new SyncClient(token, async () => new Response(token, { status }))
    await assert.rejects(client.create(template), error => error.code === code && !error.message.includes(token))
  }
})

test('HTML response and structurally invalid records are rejected', async () => {
  const html = new SyncClient(token, async () => new Response('<html>Proxy error</html>'))
  await assert.rejects(html.records(), error => error.code === 'response')
  const invalid = new SyncClient(token, async () => response(page([{ ...record(), version: 0 }])))
  await assert.rejects(invalid.records(), error => error.code === 'response')
})

test('session wire guard keeps exact weight strings and snapshot references', () => {
  const exercise = template.data.exercises[0]
  const session = {
    routine_id: template.entity_id, routine_version: 1, routine_snapshot: template.data,
    started_at: '2026-10-28T07:15:00-04:00', finished_at: '2026-10-28T08:00:00-04:00',
    sets: [{ id: crypto.randomUUID(), exercise_id: exercise.id, weight: '22.500', unit: 'kg', reps: 10, kind: 'work' }],
  }
  assert.ok(isSession(session))
  assert.ok(!isSession({ ...session, sets: [{ ...session.sets[0], weight: 22.5 }] }))
  assert.ok(!isSession({ ...session, sets: [{ ...session.sets[0], exercise_id: crypto.randomUUID() }] }))
})
