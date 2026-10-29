import { isObject, isRecord } from './domain.ts'
import type { CreateRoutine, SyncedRecord } from './domain.ts'

export class SyncError extends Error {
  code: 'credentials' | 'connection' | 'response' | 'reused' | 'request'

  constructor(code: SyncError['code'], message: string) {
    super(message)
    this.name = 'SyncError'
    this.code = code
  }
}

export class SyncClient {
  #token: string | null
  #send: typeof fetch
  #records = new Map<string, SyncedRecord>()
  #cursor: string | null = null
  #loading: Promise<void> | null = null

  constructor(token: string | null, send: typeof fetch = fetch) {
    if (token !== null && (!token || !/^[\x21-\x7e]+$/.test(token))) throw new SyncError('credentials', 'Introduce una clave de acceso válida.')
    this.#token = token
    // Native Window.fetch rejects a SyncClient receiver (Illegal invocation).
    this.#send = (input, init) => send(input, init)
  }

  async #request(path: string, init: RequestInit = {}): Promise<unknown> {
    let response: Response
    try {
      response = await this.#send(path, {
        ...init, signal: AbortSignal.timeout(15000), cache: 'no-store', credentials: 'same-origin',
        headers: { ...init.headers, ...(this.#token === null ? {} : { Authorization: `Bearer ${this.#token}` }) },
      })
    } catch {
      throw new SyncError('connection', 'No se pudo contactar con el servidor. Comprueba que esté encendido e inténtalo de nuevo.')
    }
    if (response.status === 401) throw new SyncError('credentials', 'Tu acceso ha caducado o no es válido. Vuelve a acceder.')
    if (response.status === 409) throw new SyncError('reused', 'La operación guardada no coincide con la del servidor. Conserva la rutina pendiente para revisarla.')
    if (!response.ok) throw new SyncError('request', 'El servidor no pudo completar la solicitud. La rutina pendiente se conserva.')
    try { return await response.json() }
    catch { throw new SyncError('response', 'La respuesta del servidor no es válida. No se descartarán los datos pendientes.') }
  }

  records(): Promise<SyncedRecord[]> {
    if (!this.#loading) this.#loading = this.#download().finally(() => { this.#loading = null })
    return this.#loading.then(() => structuredClone([...this.#records.values()].filter(record => !record.deleted)))
  }

  async #download(): Promise<void> {
    // Publish the cache and cursor together only after every page succeeds.
    const records = new Map(this.#records)
    const seenCursors = new Set<string>(this.#cursor === null ? [] : [this.#cursor])
    let cursor = this.#cursor
    let more: boolean
    do {
      const query: string = cursor === null ? '' : `?cursor=${encodeURIComponent(cursor)}`
      const page = await this.#request(`/api/v1/sync/pull${query}`)
      if (!isObject(page) || page.protocol_version !== 1 || !Array.isArray(page.changes) ||
        !page.changes.every(isRecord) || typeof page.next_cursor !== 'string' || !page.next_cursor ||
        typeof page.has_more !== 'boolean' || (page.has_more && page.changes.length === 0)) {
        throw new SyncError('response', 'No se pudo interpretar el historial. Se conservará la vista anterior.')
      }
      if (page.has_more && seenCursors.has(page.next_cursor)) {
        throw new SyncError('response', 'La descarga no avanzó. Vuelve a intentarlo.')
      }
      seenCursors.add(page.next_cursor)
      for (const record of page.changes as SyncedRecord[]) {
        const key = `${record.entity_type}:${record.entity_id}`
        if ((records.get(key)?.version ?? 0) < record.version) records.set(key, record)
      }
      cursor = page.next_cursor
      more = page.has_more
    } while (more)
    this.#records = records
    this.#cursor = cursor
  }

  async create(operation: CreateRoutine): Promise<{ status: 'applied' | 'conflict'; record: SyncedRecord | null }> {
    const response = await this.#request('/api/v1/sync/push', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ protocol_version: 1, operations: [operation] }),
    })
    if (!isObject(response) || response.protocol_version !== 1 || !Array.isArray(response.results) || response.results.length !== 1) {
      throw new SyncError('response', 'No se pudo confirmar el guardado. Puedes reintentarlo con la misma rutina.')
    }
    const result: unknown = response.results[0]
    if (!isObject(result) || result.operation_id !== operation.operation_id ||
      (result.status !== 'applied' && result.status !== 'conflict') ||
      !(result.record === null || isRecord(result.record))) {
      throw new SyncError('response', 'La confirmación no corresponde a la rutina pendiente.')
    }
    if (result.record !== null && (result.record.entity_type !== 'routine' || result.record.entity_id !== operation.entity_id)) {
      throw new SyncError('response', 'El servidor confirmó un registro diferente. Se conserva la rutina pendiente.')
    }
    if (result.status === 'applied' && (result.record === null || result.record.deleted)) {
      throw new SyncError('response', 'No se pudo confirmar la creación de la rutina.')
    }
    return { status: result.status, record: result.record }
  }
}
