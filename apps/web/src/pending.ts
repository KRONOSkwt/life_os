import { isCreateRoutine } from './domain.ts'
import type { CreateRoutine } from './domain.ts'

export const PENDING_KEY = 'life-os.pending-routine.v1'
export type PendingStorage = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>

export function readPending(storage: PendingStorage): CreateRoutine | null {
  const raw = storage.getItem(PENDING_KEY)
  if (raw === null) return null
  let value: unknown
  try { value = JSON.parse(raw) } catch { throw new Error('No se pudo leer la rutina pendiente. Consérvala antes de borrar los datos del navegador.') }
  if (!isCreateRoutine(value)) throw new Error('La rutina pendiente tiene un formato inválido. No se enviará automáticamente.')
  return value
}

export function writePending(storage: PendingStorage, operation: CreateRoutine): void {
  if (!isCreateRoutine(operation)) throw new Error('La rutina no es válida.')
  const existing = readPending(storage)
  if (existing && JSON.stringify(existing) !== JSON.stringify(operation)) {
    throw new Error('Hay otra rutina pendiente. Guarda esa rutina antes de crear una nueva.')
  }
  try { storage.setItem(PENDING_KEY, JSON.stringify(operation)) }
  catch { throw new Error('El navegador no pudo conservar la rutina. No se ha enviado; revisa el almacenamiento disponible.') }
}

export function clearPending(storage: PendingStorage, operationId: string): void {
  if (readPending(storage)?.operation_id === operationId) storage.removeItem(PENDING_KEY)
}
