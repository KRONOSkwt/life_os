export interface Exercise {
  id: string
  name: string
  target_sets: number
  reps_min: number
  reps_max: number
}

export interface Routine {
  name: string
  exercises: Exercise[]
}

export interface PerformedSet {
  id: string
  exercise_id: string
  weight: string
  unit: 'kg' | 'lb'
  reps: number
  kind: 'warmup' | 'work'
  rir?: number | null
  rest_seconds?: number | null
}

export interface Session {
  routine_id: string
  routine_version: number | null
  routine_snapshot: Routine
  started_at: string
  finished_at: string | null
  sets: PerformedSet[]
}

export type EntityType = 'routine' | 'session'
export type SyncedRecord = {
  entity_id: string
  version: number
} & (
  | { entity_type: EntityType; deleted: true; data: null }
  | { entity_type: 'routine'; deleted: false; data: Routine }
  | { entity_type: 'session'; deleted: false; data: Session }
)

export interface CreateRoutine {
  operation_id: string
  entity_type: 'routine'
  entity_id: string
  base_version: 0
  action: 'upsert'
  data: Routine
}

export const isObject = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value)

export const isId = (value: unknown): value is string =>
  typeof value === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(value)

const integer = (value: unknown, min: number, max: number): value is number =>
  typeof value === 'number' && Number.isInteger(value) && value >= min && value <= max

const name = (value: unknown): value is string =>
  typeof value === 'string' && value.trim().length > 0 && value.length <= 120

export function isRoutine(value: unknown): value is Routine {
  if (!isObject(value) || !name(value.name) || !Array.isArray(value.exercises)) return false
  return value.exercises.length > 0 && value.exercises.length <= 100 &&
    new Set(value.exercises.map(item => isObject(item) ? item.id : null)).size === value.exercises.length &&
    value.exercises.every(item => isObject(item) && isId(item.id) && name(item.name) &&
      integer(item.target_sets, 1, 100) && integer(item.reps_min, 1, 1000) &&
      integer(item.reps_max, 1, 1000) && item.reps_min <= item.reps_max)
}

const timestamp = (value: unknown): value is string =>
  typeof value === 'string' && /[tT].*(?:[zZ]|[+-]\d{2}:\d{2})$/.test(value) && Number.isFinite(Date.parse(value))

export function isSession(value: unknown): value is Session {
  if (!isObject(value) || !isId(value.routine_id) || !isRoutine(value.routine_snapshot) ||
    !(value.routine_version === null || integer(value.routine_version, 1, Number.MAX_SAFE_INTEGER)) ||
    !timestamp(value.started_at) || !(value.finished_at === null || timestamp(value.finished_at)) ||
    !Array.isArray(value.sets) || value.sets.length > 1000) return false
  if (value.finished_at !== null && Date.parse(value.finished_at) < Date.parse(value.started_at)) return false
  const ids = new Set(value.routine_snapshot.exercises.map(item => item.id))
  return new Set(value.sets.map(item => isObject(item) ? item.id : null)).size === value.sets.length &&
    value.sets.every(item => isObject(item) && isId(item.id) && isId(item.exercise_id) && ids.has(item.exercise_id) &&
      typeof item.weight === 'string' && /^(0|[1-9][0-9]{0,3})(\.[0-9]{1,3})?$/.test(item.weight) &&
      (item.unit === 'kg' || item.unit === 'lb') && integer(item.reps, 0, 1000) &&
      (item.kind === 'warmup' || item.kind === 'work') &&
      (item.rir == null || integer(item.rir, 0, 20)) &&
      (item.rest_seconds == null || integer(item.rest_seconds, 0, 86400)))
}

export function isRecord(value: unknown): value is SyncedRecord {
  if (!isObject(value) || !isId(value.entity_id) || !integer(value.version, 1, Number.MAX_SAFE_INTEGER)) return false
  if (value.entity_type !== 'routine' && value.entity_type !== 'session') return false
  if (value.deleted === true) return value.data === null
  return value.deleted === false && (value.entity_type === 'routine' ? isRoutine(value.data) : isSession(value.data))
}

export function createRoutine(data: Routine): CreateRoutine {
  if (!isRoutine(data)) throw new Error('Completa los nombres, las series y un intervalo válido de repeticiones.')
  return {
    operation_id: crypto.randomUUID(), entity_type: 'routine', entity_id: crypto.randomUUID(),
    base_version: 0, action: 'upsert', data: structuredClone(data),
  }
}

export function isCreateRoutine(value: unknown): value is CreateRoutine {
  return isObject(value) && isId(value.operation_id) && isId(value.entity_id) &&
    value.entity_type === 'routine' && value.base_version === 0 && value.action === 'upsert' && isRoutine(value.data)
}
