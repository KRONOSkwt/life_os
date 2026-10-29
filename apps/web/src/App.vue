<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import LifeIcon from './LifeIcon.vue'
import { createRoutine } from './domain.ts'
import type { CreateRoutine, Exercise, SyncedRecord } from './domain.ts'
import { clearPending, readPending, writePending } from './pending.ts'
import { SyncClient } from './sync.ts'

const token = ref('')
const connected = ref(false)
const busy = ref(false)
const loaded = ref(false)
const error = ref('')
const notice = ref('')
const name = ref('')
const records = ref<SyncedRecord[]>([])
const pending = ref<CreateRoutine | null>(null)
const storageProblem = ref(false)
const exercises = ref<Exercise[]>([newExercise()])
let client: SyncClient | null = null

function newExercise(): Exercise {
  return { id: crypto.randomUUID(), name: '', target_sets: 3, reps_min: 8, reps_max: 12 }
}

const routines = computed(() => records.value.filter(
  (record): record is Extract<SyncedRecord, { entity_type: 'routine'; deleted: false }> =>
    record.entity_type === 'routine' && !record.deleted,
))
const sessions = computed(() => records.value.filter(
  (record): record is Extract<SyncedRecord, { entity_type: 'session'; deleted: false }> =>
    record.entity_type === 'session' && !record.deleted,
).sort((a, b) => Date.parse(b.data.started_at) - Date.parse(a.data.started_at)))

const message = (problem: unknown) => problem instanceof Error ? problem.message : 'No se pudo completar la acción.'

onMounted(() => {
  try {
    const stored = readPending(window.localStorage)
    pending.value = stored
    if (stored) {
      name.value = stored.data.name
      exercises.value = structuredClone(stored.data.exercises)
    }
  } catch (problem) {
    storageProblem.value = true
    error.value = message(problem)
  }
})

async function connect() {
  if (busy.value) return
  busy.value = true
  error.value = ''
  notice.value = ''
  try {
    const next = new SyncClient(token.value.trim())
    const latest = await next.records()
    client = next
    records.value = latest
    connected.value = true
    loaded.value = true
  } catch (problem) {
    client = null
    connected.value = false
    records.value = []
    loaded.value = false
    error.value = message(problem)
  } finally { busy.value = false }
}

function disconnect() {
  client = null
  token.value = ''
  connected.value = false
  loaded.value = false
  records.value = []
  notice.value = ''
  error.value = ''
}

async function refresh() {
  if (!client || busy.value) return
  busy.value = true
  error.value = ''
  try { records.value = await client.records() }
  catch (problem) { error.value = message(problem) }
  finally { busy.value = false }
}

async function save() {
  if (!client || busy.value || storageProblem.value) return
  busy.value = true
  error.value = ''
  notice.value = ''
  try {
    const operation = pending.value ?? createRoutine({
      name: name.value.trim(),
      exercises: exercises.value.map(item => ({ ...item, name: item.name.trim() })),
    })
    writePending(window.localStorage, operation)
    pending.value = operation
    const result = await client.create(operation)
    if (result.status === 'conflict') {
      error.value = 'El servidor tiene otra versión de esta rutina. Tu copia pendiente se conserva; puedes descargarla para revisarla.'
      return
    }
    clearPending(window.localStorage, operation.operation_id)
    pending.value = null
    name.value = ''
    exercises.value = [newExercise()]
    notice.value = 'Rutina guardada.'
    try { records.value = await client.records() }
    catch { error.value = 'La rutina se guardó, pero no se pudo actualizar la lista. Pulsa Actualizar cuando vuelva la conexión.' }
  } catch (problem) { error.value = message(problem) }
  finally { busy.value = false }
}

function downloadPending() {
  if (!pending.value) return
  const blob = new Blob([JSON.stringify(pending.value, null, 2)], { type: 'application/json' })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = 'life-os-rutina-pendiente.json'
  anchor.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

const date = (value: string) => new Intl.DateTimeFormat('es', {
  day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit',
}).format(new Date(value))


type Page = 'inicio' | 'entrenamiento' | 'historial' | 'salud' | 'deporte' | 'finanzas'
const navigation: { id: Page; label: string; icon: string }[] = [
  { id: 'inicio', label: 'Vista general', icon: 'grid' },
  { id: 'entrenamiento', label: 'Entrenamiento', icon: 'gym' },
  { id: 'salud', label: 'Salud y lesiones', icon: 'health' },
  { id: 'deporte', label: 'Deporte', icon: 'sport' },
  { id: 'finanzas', label: 'Finanzas', icon: 'wallet' },
  { id: 'historial', label: 'Historial', icon: 'clock' },
]
function readPage(): Page {
  const hash = window.location.hash.slice(1)
  if (hash === 'rutinas') return 'entrenamiento'
  if (hash === 'sesiones') return 'historial'
  return navigation.some(item => item.id === hash) ? hash as Page : 'inicio'
}
const page = ref<Page>(readPage())
const pageLabel = computed(() => navigation.find(item => item.id === page.value)?.label ?? 'Vista general')
function followHash() { page.value = readPage(); window.scrollTo({ top: 0, behavior: 'instant' }) }
onMounted(() => window.addEventListener('hashchange', followHash))
onUnmounted(() => window.removeEventListener('hashchange', followHash))
const today = new Intl.DateTimeFormat('es', { weekday: 'long', day: 'numeric', month: 'long' }).format(new Date())
const week = computed(() => Array.from({ length: 7 }, (_, index) => {
  const day = new Date(); day.setDate(day.getDate() - 6 + index); day.setHours(0, 0, 0, 0)
  const end = new Date(day); end.setDate(end.getDate() + 1)
  return { label: new Intl.DateTimeFormat('es', { weekday: 'short' }).format(day).replace('.', ''),
    count: sessions.value.filter(item => item.data.finished_at && Date.parse(item.data.started_at) >= +day && Date.parse(item.data.started_at) < +end).length }
}))
const weekTotal = computed(() => week.value.reduce((sum, day) => sum + day.count, 0))
const weekMax = computed(() => Math.max(1, ...week.value.map(day => day.count)))
const modules = [
  { id: 'salud', icon: 'health', label: 'Salud y lesiones', description: 'Escucha lo que tu cuerpo te cuenta.', detail: 'Aquí tendrás el seguimiento de lesiones, molestias y recuperación.' },
  { id: 'deporte', icon: 'sport', label: 'Deporte', description: 'Haz espacio para lo que te mueve.', detail: 'Aquí podrás organizar tus actividades y eventos deportivos.' },
  { id: 'finanzas', icon: 'wallet', label: 'Finanzas', description: 'Una mirada tranquila a tus números.', detail: 'Aquí tendrás tus movimientos, balances y categorías.' },
]
const currentModule = computed(() => modules.find(item => item.id === page.value))
function documentFocus() { document.getElementById('contenido')?.focus() }
</script>

<template>
  <a href="#contenido" class="skip-link" @click.prevent="documentFocus">Ir al contenido</a>
  <div class="shell">
    <aside class="sidebar">
      <a class="brand" href="#inicio" aria-label="Life OS, inicio"><span class="brand-mark" aria-hidden="true"><span></span><span></span><span></span><span></span></span><span>life<span class="brand-slash"> / </span>os<span class="brand-period">.</span></span></a>
      <p class="sidebar-caption">TU ESPACIO PERSONAL</p>
      <nav aria-label="Navegación principal"><a v-for="item in navigation" :key="item.id" :href="`#${item.id}`" :aria-label="item.label" :title="item.label" :class="{ 'nav-current': page === item.id }" :aria-current="page === item.id ? 'page' : undefined"><LifeIcon :name="item.icon" /><span>{{ item.label }}</span><span v-if="page === item.id" class="nav-dot"></span></a></nav>
      <div class="sidebar-bottom"><span class="personal-avatar">L</span><div><strong>Tu propio ritmo</strong><span>Un día a la vez.</span></div><LifeIcon name="leaf" /></div>
    </aside>
    <main id="contenido" tabindex="-1">
      <header class="topbar"><span>Mi espacio <span class="separator">/</span> <strong>{{ pageLabel }}</strong></span><div class="topbar-right"><span class="topbar-date">{{ today }}</span><span class="status" :class="{ 'status-online': connected }"><span class="status-dot"></span>{{ connected ? 'Conectado al PC' : 'Sin conectar' }}</span></div></header>
      <section class="intro" aria-labelledby="page-title">
        <div><p class="eyebrow">{{ page === 'inicio' ? 'MENOS RUIDO. MÁS ESPACIO PARA TI.' : 'A TU RITMO' }}</p><h1 id="page-title">{{ page === 'inicio' ? 'Todo en su sitio.' : pageLabel }}<span class="title-dot" v-if="page !== 'inicio'">.</span></h1><p class="intro-copy">{{ page === 'inicio' ? 'Una mirada a tu día. El resto, paso a paso.' : page === 'entrenamiento' ? 'Construye tu rutina. Encuentra tu constancia.' : page === 'historial' ? 'Cada sesión cuenta. Aquí está tu recorrido.' : currentModule?.description }}</p></div>
        <span class="intro-tag"><LifeIcon name="leaf" /> Tu vida, con intención</span>
      </section>
      <p v-if="error" class="feedback feedback-error" role="alert">{{ error }}</p>
      <p v-if="notice" class="feedback feedback-success" role="status">{{ notice }}</p>
      <template v-if="page === 'inicio'">
        <section class="bento-grid" aria-label="Resumen personal">
          <article class="card training-overview">
            <div class="tile-heading"><span class="icon-box"><LifeIcon name="gym" /></span><span>ENTRENAMIENTO</span><a href="#entrenamiento" class="icon-link" aria-label="Abrir entrenamiento"><LifeIcon name="arrow" /></a></div>
            <div class="training-copy"><span class="subtle-label">TU PRÓXIMO PASO</span><h2>Un poco más fuerte.<br /><span>Un poco más tú.</span></h2><p>{{ loaded ? routines.length ? 'Tus rutinas están listas para acompañarte.' : 'Dale forma a tu primera rutina.' : 'Conecta tu PC para encontrar tus rutinas.' }}</p><a class="button-link" href="#entrenamiento">{{ loaded && !routines.length ? 'Crear mi primera rutina' : 'Ir a mis rutinas' }}<LifeIcon name="arrow" /></a></div>
            <div class="training-stats"><div><strong>{{ loaded ? String(routines.length).padStart(2, '0') : '—' }}</strong><span>Rutinas guardadas</span></div><span class="stat-divider"></span><div><strong>{{ loaded ? String(sessions.length).padStart(2, '0') : '—' }}</strong><span>Sesiones registradas</span></div></div>
            <div class="training-art" aria-hidden="true"><span></span><span></span><span></span></div>
          </article>
          <article class="card activity-overview"><div class="tile-heading"><span class="icon-box"><LifeIcon name="activity" /></span><span>EN MOVIMIENTO</span><span class="subtle-label">Últimos 7 días</span></div><div class="activity-total"><strong>{{ loaded ? weekTotal : '—' }}</strong><span>{{ weekTotal === 1 ? 'sesión completada' : 'sesiones completadas' }}</span></div><div class="week-chart" role="img" :aria-label="loaded ? `Sesiones completadas en los últimos siete días: ${week.map(day => `${day.label}: ${day.count}`).join(', ')}` : 'Conecta para consultar la actividad de los últimos siete días'"><div v-for="(day, index) in week" :key="index" class="chart-day"><div class="bar-track"><span :class="{ 'has-activity': loaded && day.count > 0 }" :style="{ height: loaded && day.count ? `${Math.max(15, day.count / weekMax * 100)}%` : '4px' }"></span></div><span>{{ day.label }}</span></div></div><p class="chart-note">{{ loaded ? weekTotal ? 'Cada pequeño paso suma.' : 'Tu semana aún tiene espacio para moverte.' : 'Tu actividad aparecerá al conectar.' }}</p></article>
          <article class="card recent-overview"><div class="tile-heading"><span class="icon-box"><LifeIcon name="clock" /></span><span>ÚLTIMA SESIÓN</span><a href="#historial" class="icon-link" aria-label="Ver historial"><LifeIcon name="arrow" /></a></div><template v-if="sessions[0]"><h3>{{ sessions[0].data.routine_snapshot.name }}</h3><p>{{ date(sessions[0].data.started_at) }}</p><span class="quiet-badge">{{ sessions[0].data.sets.length }} {{ sessions[0].data.sets.length === 1 ? 'serie' : 'series' }} · {{ sessions[0].data.finished_at ? 'Finalizada' : 'En curso' }}</span></template><template v-else><h3>Tu recorrido empieza contigo.</h3><p>{{ loaded ? 'Tus sesiones aparecerán aquí cuando sincronices desde el móvil.' : 'Conecta para volver a tus entrenamientos.' }}</p></template></article>
          <a v-for="module in modules" :key="module.id" :href="`#${module.id}`" class="card module-tile"><div class="tile-heading"><span class="icon-box"><LifeIcon :name="module.icon" /></span><span class="quiet-badge">En preparación</span><LifeIcon name="arrow" class="module-arrow" /></div><h2>{{ module.label }}</h2><p>{{ module.description }}</p><div class="module-bottom"><span>Aquí habrá espacio para más.</span><span class="module-line"></span></div></a>
        </section>
        <div class="dashboard-footnote"><LifeIcon name="leaf" /><span>No hace falta hacerlo todo hoy. Solo dar el siguiente paso.</span></div>
      </template>
      <template v-if="page === 'entrenamiento'">
      <div id="rutinas" class="workspace">
        <section class="editor card" aria-labelledby="editor-title">
          <div class="section-heading"><div><p class="eyebrow">DISEÑA TU SESIÓN</p><h2 id="editor-title">Nueva rutina</h2></div><span class="outline-badge">Manual</span></div>
          <div v-if="pending" class="pending-notice" role="status">
            <strong>Una rutina pendiente de confirmar</strong>
            <p>Conservamos lo que enviaste para que puedas reintentar el guardado.</p>
            <button class="button-text" @click="downloadPending">Descargar copia</button>
          </div>
          <form @submit.prevent="save">
            <fieldset :disabled="busy || pending !== null || storageProblem">
              <label for="routine-name">Nombre de la rutina</label>
              <input id="routine-name" v-model="name" required maxlength="120" placeholder="Por ejemplo, Push A" />
              <div v-for="(exercise, index) in exercises" :key="exercise.id" class="exercise">
                <div class="exercise-heading"><span class="exercise-number">{{ String(index + 1).padStart(2, '0') }}</span><h3>Ejercicio {{ index + 1 }}</h3><button v-if="exercises.length > 1" type="button" class="button-text remove" :aria-label="`Quitar ejercicio ${index + 1}`" @click="exercises.splice(index, 1)">Quitar</button></div>
                <label :for="`name-${exercise.id}`">Ejercicio</label>
                <input :id="`name-${exercise.id}`" v-model="exercise.name" required maxlength="120" placeholder="Nombre del ejercicio" />
                <div class="exercise-numbers">
                  <div><label :for="`sets-${exercise.id}`">Series</label><input :id="`sets-${exercise.id}`" v-model.number="exercise.target_sets" type="number" min="1" max="100" step="1" required /></div>
                  <div><label :for="`min-${exercise.id}`">Reps. mín.</label><input :id="`min-${exercise.id}`" v-model.number="exercise.reps_min" type="number" min="1" :max="exercise.reps_max" step="1" required /></div>
                  <div><label :for="`max-${exercise.id}`">Reps. máx.</label><input :id="`max-${exercise.id}`" v-model.number="exercise.reps_max" type="number" :min="exercise.reps_min" max="1000" step="1" required /></div>
                </div>
              </div>
              <button type="button" class="add-exercise" :disabled="exercises.length >= 100" @click="exercises.push(newExercise())">+ Añadir ejercicio</button>
            </fieldset>
            <div class="save-row"><span>{{ exercises.length }} {{ exercises.length === 1 ? 'ejercicio' : 'ejercicios' }}</span><button type="submit" :disabled="!connected || busy || storageProblem">{{ busy ? 'Guardando…' : pending ? 'Reintentar guardado' : 'Guardar rutina' }}</button></div>
          </form>
        </section>

        <section class="routine-library" aria-labelledby="routines-title">
          <div class="section-heading"><div><p class="eyebrow">TU BIBLIOTECA</p><h2 id="routines-title">Mis rutinas</h2></div><span class="count">{{ loaded ? routines.length : '—' }}</span></div>
          <div v-if="!routines.length" class="empty-state card"><span class="empty-symbol" aria-hidden="true">↗</span><h3>{{ loaded ? 'Tu primera rutina empieza aquí' : 'Tus rutinas, en un solo lugar' }}</h3><p>{{ loaded ? 'Crea una rutina con tus ejercicios para empezar a organizar tus sesiones.' : 'Conecta con tu servidor para consultar lo que has guardado.' }}</p></div>
          <article v-for="routine in routines" :key="routine.entity_id" class="routine-card card">
            <div class="section-heading"><h3>{{ routine.data.name }}</h3><span class="outline-badge">{{ routine.data.exercises.length }} {{ routine.data.exercises.length === 1 ? 'ejercicio' : 'ejercicios' }}</span></div>
            <ol><li v-for="exercise in routine.data.exercises" :key="exercise.id"><span>{{ exercise.name }}</span><small>{{ exercise.target_sets }} × {{ exercise.reps_min }}–{{ exercise.reps_max }}</small></li></ol>
          </article>
        </section>
      </div>

      </template>
      <template v-if="page === 'historial'">
      <section id="sesiones" class="history" aria-labelledby="history-title">
        <div class="section-heading"><div><p class="eyebrow">LO QUE HAS HECHO</p><h2 id="history-title">Historial de sesiones</h2></div></div>
        <div v-if="!sessions.length" class="history-empty card"><p>{{ loaded ? 'Todavía no hay sesiones registradas.' : 'Conecta para consultar tu historial.' }}</p></div>
        <div v-else class="session-list">
          <article v-for="session in sessions" :key="session.entity_id" class="session-card card">
            <div><h3>{{ session.data.routine_snapshot.name }}</h3><time :datetime="session.data.started_at">{{ date(session.data.started_at) }}</time></div>
            <span>{{ session.data.sets.length }} {{ session.data.sets.length === 1 ? 'serie registrada' : 'series registradas' }}</span><span class="outline-badge">{{ session.data.finished_at ? 'Finalizada' : 'En curso' }}</span>
          </article>
        </div>
      </section>
      </template>
      <section v-if="currentModule" class="card module-page" :aria-label="currentModule.label"><span class="module-emblem"><LifeIcon :name="currentModule.icon" /></span><span class="quiet-badge">En preparación</span><h2>Un espacio que crecerá contigo.</h2><p>{{ currentModule.detail }} Este módulo todavía no permite registrar información.</p><a href="#inicio" class="button-link button-secondary">Volver a mi espacio <LifeIcon name="arrow" /></a></section>
      <section class="connection card" aria-labelledby="connection-title">
        <div><h2 id="connection-title">Tu espacio, conectado</h2><p>Conecta con tu PC para consultar y guardar tus entrenamientos.</p></div>
        <form v-if="!connected" class="connection-form" @submit.prevent="connect">
          <label for="server-token">Token del servidor</label>
          <div class="input-action">
            <input id="server-token" v-model="token" type="password" autocomplete="off" spellcheck="false" required :disabled="busy" placeholder="Introduce tu token" />
            <button type="submit" :disabled="busy">{{ busy ? 'Conectando…' : 'Conectar' }}</button>
          </div>
          <small>Se utiliza solo durante esta sesión del navegador.</small>
        </form>
        <div v-else class="connection-actions">
          <button class="button-secondary" :disabled="busy" @click="refresh">{{ busy ? 'Actualizando…' : 'Actualizar' }}</button>
          <button class="button-text" :disabled="busy" @click="disconnect">Desconectar</button>
        </div>
      </section>


      <footer><span>life / os<span class="brand-period">.</span></span><span>Tu vida. Tu ritmo. Tu espacio.</span></footer>
    </main>
  </div>
</template>
