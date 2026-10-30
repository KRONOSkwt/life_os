package personal.lifeos

import android.app.Application
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.BorderStroke
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.sp
import androidx.core.view.WindowCompat
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.saveable.rememberSaveableStateHolder
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import androidx.lifecycle.viewmodel.compose.viewModel
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.*
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject

class LifeViewModel(application: Application) : AndroidViewModel(application) {
    private val app = application as LifeApplication
    private val repo = app.repository
    val records = repo.dao.observeCentral().stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), emptyList())
    val drafts = repo.dao.observeDrafts().stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), emptyList())
    val pending = repo.dao.observePending().stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), emptyList())
    var message by mutableStateOf("Vincula el móvil para descargar tus rutinas. Después podrás entrenar sin conexión.")
        private set
    var busy by mutableStateOf(false)
        private set
    var linked by mutableStateOf(false)
        private set
    init { run {
        linked = app.vault.read() != null
        message = repo.dao.meta("sync_status") ?: if (linked) "Tu entrenamiento está guardado en este móvil." else message
    } }

    private fun run(action: suspend () -> Unit) {
        if (busy) return
        busy = true
        viewModelScope.launch {
            try { withContext(Dispatchers.IO) { action() } }
            catch (error: Exception) {
                if (error is kotlinx.coroutines.CancellationException) throw error
                message = syncError(error)
            } finally { busy = false }
        }
    }
    fun pair(invitation: String) = run {
        val expected = JSONObject(invitation).getString("server_id")
        val previous = repo.dao.meta("server_id")
        require(previous == null || previous == expected) { "Esta instalación pertenece a otro servidor. Conserva sus datos antes de cambiarlo." }
        val credentials = ServerApi.pair(invitation, android.os.Build.MODEL)
        // Bind the DB before writing the credential; a failed disk write cannot mix servers.
        repo.dao.put(Metadata("server_id", credentials.serverId))
        app.vault.save(credentials)
        linked = true
        message = "Móvil vinculado. Descargando rutinas…"
        repo.sync(ServerApi(credentials))
        message = syncCompletionMessage(repo.dao.pending())
    }
    fun sync() = run {
        val credentials = requireNotNull(app.vault.read()) { "Vincula primero el móvil" }
        repo.sync(ServerApi(credentials))
        message = syncCompletionMessage(repo.dao.pending())
    }
    fun start(routine: Central) = run { repo.start(routine); message = "Sesión guardada en el móvil" }
    fun add(id: String, exercise: String, weight: String, unit: String, reps: String, warmup: Boolean, rir: String) = run {
        val count = requireNotNull(reps.toIntOrNull()) { "Introduce repeticiones enteras" }
        val reserve = if (rir.isBlank()) null else requireNotNull(rir.toIntOrNull()) { "RIR debe ser entero" }
        repo.addSet(id, exercise, weight.trim().replace(',', '.'), unit, count, if (warmup) "warmup" else "work", reserve)
        message = "Serie guardada en el móvil"
    }
    fun finish(id: String) = run { repo.finish(id); app.scheduleSync(); message = "Sesión terminada y guardada para sincronizar" }
    fun resolve(id: String, local: Boolean) = run { repo.resolve(id, local); message = "Decisión guardada"; app.scheduleSync() }
}

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        WindowCompat.getInsetsController(window, window.decorView).apply {
            isAppearanceLightStatusBars = false
            isAppearanceLightNavigationBars = false
        }
        setContent { LifeTheme { LifeScreen() } }
    }
}

@Composable
fun LifeScreen(model: LifeViewModel = viewModel()) {
    val records by model.records.collectAsState()
    val drafts by model.drafts.collectAsState()
    val pending by model.pending.collectAsState()
    var invitation by remember { mutableStateOf("") }
    var pairing by rememberSaveable { mutableStateOf(false) }
    var page by rememberSaveable { mutableStateOf("inicio") }
    val pageState = rememberSaveableStateHolder()
    val routines = records.filter { it.type == "routine" && !it.deleted }
    val sessions = records.filter { it.type == "session" && !it.deleted }
    val open = drafts.filter { !it.completed }
    val tabs = listOf("inicio" to "Inicio", "gym" to "Entrenar", "historial" to "Historial", "espacio" to "Mi espacio")
    val modules = listOf("salud" to "Salud y lesiones", "deporte" to "Deporte", "finanzas" to "Finanzas")
    Scaffold(containerColor = MaterialTheme.colorScheme.background, bottomBar = {
        NavigationBar(containerColor = MaterialTheme.colorScheme.surface.copy(alpha = 0.97f), tonalElevation = 0.dp) {
            tabs.forEach { (id, label) ->
                NavigationBarItem(selected = page == id || (id == "espacio" && modules.any { it.first == page }),
                    onClick = { page = id }, icon = { LifeGlyph(id) }, label = { Text(label, fontSize = 11.sp) })
            }
        }
    }) { insets ->
        pageState.SaveableStateProvider(page) {
            Column(Modifier.fillMaxSize().padding(insets).verticalScroll(rememberScrollState()).padding(20.dp),
                verticalArrangement = Arrangement.spacedBy(18.dp)) {
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                    Text("life / os.", style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Medium)
                    Text(if (model.linked) "Móvil vinculado" else "Sin vincular", color = MaterialTheme.colorScheme.primary,
                        style = MaterialTheme.typography.labelSmall, modifier = Modifier.padding(top = 7.dp))
                }
                Column(Modifier.padding(top = 14.dp, bottom = 4.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text("TU VIDA, A TU RITMO", color = MaterialTheme.colorScheme.primary, style = MaterialTheme.typography.labelSmall, letterSpacing = 2.sp)
                    Text(when (page) { "inicio" -> "Todo en su sitio."; "gym" -> "Entrenamiento."; "historial" -> "Tu recorrido."; "espacio" -> "Mi espacio."; else -> modules.first { it.first == page }.second },
                        style = MaterialTheme.typography.headlineLarge, fontWeight = FontWeight.Medium)
                    Text(when (page) { "inicio" -> "Una mirada a tu día. El resto, paso a paso."; "gym" -> "Construye tu constancia, una serie a la vez."; "historial" -> "Cada sesión cuenta."; else -> "Un espacio que crece contigo." },
                        style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                if (model.busy) LinearProgressIndicator(Modifier.fillMaxWidth())
                if (page == "inicio" || page == "espacio" || page == "gym") {
                    LifePanel {
                        Text(displaySyncMessage(model.message), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        Text("${pending.size} cambios pendientes · ${open.size} sesiones abiertas", style = MaterialTheme.typography.labelMedium)
                    }
                }
                for (operation in pending.filter { it.conflict != null }) {
                    LifePanel {
                        Text("Sesión en conflicto", style = MaterialTheme.typography.titleMedium, color = MaterialTheme.colorScheme.error)
                        Text("Tu sesión sigue guardada. Compara las versiones antes de decidir.")
                        Text("Móvil: ${sessionDetails(JSONObject(operation.json).getJSONObject("data"))}")
                        val remote = operation.conflict?.takeUnless { it == "null" }?.let(::JSONObject)
                        Text("PC: " + when {
                            remote == null -> "Sin registro"
                            remote.getBoolean("deleted") -> "Sesión eliminada"
                            else -> sessionDetails(remote.getJSONObject("data"))
                        })
                        TextButton(onClick = { model.resolve(operation.id, true) }, enabled = !model.busy) { Text("Conservar mi sesión") }
                        TextButton(onClick = { model.resolve(operation.id, false) }, enabled = !model.busy) { Text("Aceptar versión del PC") }
                    }
                }
                when (page) {
                    "inicio" -> {
                        LifePanel(accent = true) {
                            Text("ENTRENAMIENTO", style = MaterialTheme.typography.labelSmall, letterSpacing = 1.5.sp, color = MaterialTheme.colorScheme.primary)
                            Text(if (open.isNotEmpty()) "Continúa donde lo dejaste." else "Un poco más fuerte.\nUn poco más tú.",
                                style = MaterialTheme.typography.headlineMedium, modifier = Modifier.padding(vertical = 8.dp))
                            Text(if (open.isNotEmpty()) JSONObject(open.first().json).getJSONObject("routine_snapshot").getString("name") else "Tus rutinas, contigo. Incluso sin conexión.",
                                style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                            Button(onClick = { page = if (model.linked) "gym" else "espacio" }, modifier = Modifier.padding(top = 8.dp)) {
                                Text(if (!model.linked) "Vincular mi móvil" else if (open.isNotEmpty()) "Continuar sesión" else "Ver mis rutinas")
                            }
                        }
                        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                            LifePanel(Modifier.weight(1f)) {
                                Text(routines.size.toString().padStart(2, '0'), style = MaterialTheme.typography.headlineLarge, color = MaterialTheme.colorScheme.primary)
                                Text("Rutinas guardadas", style = MaterialTheme.typography.labelSmall)
                            }
                            LifePanel(Modifier.weight(1f)) {
                                Text(registeredSessionCount(records, drafts).toString().padStart(2, '0'), style = MaterialTheme.typography.headlineLarge)
                                Text("Sesiones registradas", style = MaterialTheme.typography.labelSmall)
                            }
                        }
                        val last = (sessions.map { JSONObject(it.json) } + drafts.filter { it.completed }.map { JSONObject(it.json) })
                            .maxByOrNull { java.time.Instant.parse(it.getString("started_at")) }
                        LifePanel {
                            Text("ÚLTIMA SESIÓN", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.primary, letterSpacing = 1.5.sp)
                            if (last == null) Text("Tu recorrido empieza contigo.", style = MaterialTheme.typography.titleMedium)
                            else { Text(sessionSummary(last)); TextButton(onClick = { page = "historial" }) { Text("Ver mi historial") } }
                        }
                        modules.forEach { (id, label) ->
                            Card(onClick = { page = id }, modifier = Modifier.fillMaxWidth(), colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface), border = BorderStroke(1.dp, MaterialTheme.colorScheme.outlineVariant)) {
                                Row(Modifier.fillMaxWidth().padding(20.dp), horizontalArrangement = Arrangement.SpaceBetween) {
                                    Text(label, style = MaterialTheme.typography.titleSmall)
                                    Text("En preparación", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                                }
                            }
                        }
                    }
                    "gym" -> {
                        open.forEach { draft -> key(draft.id) { SessionEditor(draft, model) } }
                        Text("Tus rutinas", style = MaterialTheme.typography.titleLarge)
                        if (routines.isEmpty()) LifePanel { Text("Crea una rutina desde la web del PC y sincroniza para llevarla contigo.") }
                        routines.forEach { routine ->
                            val data = JSONObject(routine.json)
                            LifePanel {
                                Text(data.getString("name"), style = MaterialTheme.typography.titleLarge)
                                data.getJSONArray("exercises").objects().forEach { exercise ->
                                    Text("${exercise.getString("name")} · ${exercise.getInt("target_sets")} × ${exercise.getInt("reps_min")}–${exercise.getInt("reps_max")}", style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                                }
                                Button(onClick = { model.start(routine) }, enabled = !model.busy && open.isEmpty()) { Text("Empezar sesión") }
                            }
                        }
                    }
                    "historial" -> {
                        if (sessions.isEmpty() && drafts.none { it.completed }) LifePanel { Text("Las sesiones que termines aparecerán aquí.") }
                        drafts.filter { it.completed }.forEach { LifePanel { Text(sessionSummary(JSONObject(it.json))); Text("Pendiente de sincronizar", color = MaterialTheme.colorScheme.primary, style = MaterialTheme.typography.labelMedium) } }
                        sessions.sortedByDescending { java.time.Instant.parse(JSONObject(it.json).getString("started_at")) }.forEach { LifePanel { Text(sessionSummary(JSONObject(it.json))); Text("En el PC", color = MaterialTheme.colorScheme.primary, style = MaterialTheme.typography.labelMedium) } }
                    }
                    "espacio" -> {
                        LifePanel {
                            Text("Conexión con tu PC", style = MaterialTheme.typography.titleLarge)
                            if (model.linked) {
                                Text("Tus sesiones se guardan en el móvil. Sincroniza cuando tu PC esté disponible.", color = MaterialTheme.colorScheme.onSurfaceVariant)
                                Button(onClick = model::sync, enabled = !model.busy) { Text("Sincronizar") }
                                TextButton(onClick = { pairing = !pairing }, enabled = !model.busy) { Text("Nueva invitación") }
                            }
                            if (!model.linked || pairing) {
                                Text("Vincular con el PC", style = MaterialTheme.typography.titleMedium)
                                Text("Pega la invitación completa generada en tu PC.", style = MaterialTheme.typography.bodyMedium)
                                OutlinedTextField(invitation, { invitation = it }, label = { Text("Invitación de vinculación") },
                                    visualTransformation = PasswordVisualTransformation(), modifier = Modifier.fillMaxWidth(), maxLines = 5)
                                Button(onClick = { model.pair(invitation); invitation = "" }, enabled = !model.busy && invitation.isNotBlank()) { Text("Vincular") }
                                if (model.linked) TextButton(onClick = { pairing = false; invitation = "" }) { Text("Cerrar vinculación") }
                            }
                        }
                        Text("Tus módulos", style = MaterialTheme.typography.titleLarge)
                        modules.forEach { (id, label) -> OutlinedButton(onClick = { page = id }, modifier = Modifier.fillMaxWidth()) { Text(label) } }
                    }
                    else -> LifePanel {
                        Text("EN PREPARACIÓN", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.primary, letterSpacing = 1.5.sp)
                        Text("Un espacio que crecerá contigo.", style = MaterialTheme.typography.headlineSmall)
                        Text(when (page) { "salud" -> "Aquí tendrás el seguimiento de lesiones, molestias y recuperación."; "deporte" -> "Aquí podrás organizar tus actividades y eventos deportivos."; else -> "Aquí tendrás tus movimientos, balances y categorías." }, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        Text("Este módulo todavía no permite registrar información.", style = MaterialTheme.typography.bodySmall)
                        TextButton(onClick = { page = "inicio" }) { Text("Volver a mi espacio") }
                    }
                }
                Text("Tu vida. Tu ritmo. Tu espacio.", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.padding(vertical = 12.dp))
            }
        }
    }
}

@Composable
private fun LifePanel(modifier: Modifier = Modifier, accent: Boolean = false, content: @Composable ColumnScope.() -> Unit) {
    Card(modifier.fillMaxWidth(), colors = CardDefaults.cardColors(containerColor = if (accent) Color(0xFF1D2A21) else MaterialTheme.colorScheme.surface),
        border = BorderStroke(1.dp, if (accent) Color(0xFF3B503C) else MaterialTheme.colorScheme.outlineVariant)) {
        Column(Modifier.padding(20.dp), verticalArrangement = Arrangement.spacedBy(12.dp), content = content)
    }
}

@Composable
private fun LifeGlyph(kind: String) {
    val color = MaterialTheme.colorScheme.onSurface
    Canvas(Modifier.size(22.dp)) {
        val unit = size.width / 24
        fun point(x: Float, y: Float) = Offset(x * unit, y * unit)
        fun line(x1: Float, y1: Float, x2: Float, y2: Float) = drawLine(color, point(x1, y1), point(x2, y2), 1.5f * unit)
        when (kind) {
            "inicio" -> listOf(3f to 3f, 14f to 3f, 3f to 14f, 14f to 14f).forEach { (x, y) -> drawRoundRect(color, point(x, y), Size(7 * unit, 7 * unit), CornerRadius(1.5f * unit), style = Stroke(1.5f * unit)) }
            "gym" -> { line(3f,9f,3f,15f); line(6f,6f,6f,18f); line(6f,12f,18f,12f); line(18f,6f,18f,18f); line(21f,9f,21f,15f) }
            "historial" -> { drawCircle(color, 9 * unit, point(12f,12f), style = Stroke(1.5f * unit)); line(12f,7f,12f,12f); line(12f,12f,16f,14f) }
            else -> { drawCircle(color, 4 * unit, point(12f,7f), style = Stroke(1.5f * unit)); drawRoundRect(color, point(4f,14f), Size(16 * unit, 7 * unit), CornerRadius(3f * unit), style = Stroke(1.5f * unit)) }
        }
    }
}

private fun localMoment(value: String): String = java.time.OffsetDateTime.parse(value)
    .atZoneSameInstant(java.time.ZoneId.systemDefault())
    .format(java.time.format.DateTimeFormatter.ofPattern("d MMM · HH:mm", java.util.Locale.forLanguageTag("es")))

private fun displaySyncMessage(value: String): String = if (value.startsWith("Sincronizado: ")) {
    runCatching { "Última sincronización · ${localMoment(value.removePrefix("Sincronizado: "))}" }.getOrDefault(value)
} else value

fun sessionSummary(data: JSONObject): String {
    val count = data.getJSONArray("sets").length()
    return "${data.getJSONObject("routine_snapshot").getString("name")} · ${localMoment(data.getString("started_at"))} · $count ${if (count == 1) "serie" else "series"}"
}

fun sessionDetails(data: JSONObject): String {
    val exercises = data.getJSONObject("routine_snapshot").getJSONArray("exercises").objects()
    return sessionSummary(data) + "\nFin: ${data.optString("finished_at", "En curso")}" +
        data.getJSONArray("sets").objects().joinToString(separator = "\n", prefix = "\n") { set ->
            val name = exercises.firstOrNull { it.getString("id") == set.getString("exercise_id") }?.getString("name") ?: "Ejercicio"
            "$name: ${set.getString("weight")} ${set.getString("unit")} × ${set.getInt("reps")} · ${if (set.getString("kind") == "warmup") "Calentamiento" else "Trabajo"} · RIR ${if (set.isNull("rir")) "—" else set.getInt("rir")}" 
        }
}

@Composable
private fun SessionEditor(draft: Draft, model: LifeViewModel) {
    val data = JSONObject(draft.json)
    val exercises = data.getJSONObject("routine_snapshot").getJSONArray("exercises").objects()
    var selected by rememberSaveable { mutableIntStateOf(0) }
    var weight by rememberSaveable { mutableStateOf("") }
    var reps by rememberSaveable { mutableStateOf("") }
    var rir by rememberSaveable { mutableStateOf("") }
    var unit by rememberSaveable { mutableStateOf("kg") }
    var warmup by rememberSaveable { mutableStateOf(false) }
    var confirmFinish by remember { mutableStateOf(false) }
    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text("Sesión en curso", style = MaterialTheme.typography.titleLarge)
            Text(data.getJSONObject("routine_snapshot").getString("name"))
            exercises.forEachIndexed { index, exercise ->
                TextButton(onClick = { selected = index }) { Text((if (selected == index) "● " else "○ ") + exercise.getString("name")) }
            }
            OutlinedTextField(weight, { weight = it }, label = { Text("Peso ($unit)") }, modifier = Modifier.fillMaxWidth())
            TextButton(onClick = { unit = if (unit == "kg") "lb" else "kg" }) { Text("Unidad: $unit") }
            OutlinedTextField(reps, { reps = it }, label = { Text("Repeticiones") }, modifier = Modifier.fillMaxWidth())
            OutlinedTextField(rir, { rir = it }, label = { Text("RIR opcional (0–20)") }, modifier = Modifier.fillMaxWidth())
            Row { Checkbox(warmup, { warmup = it }); Text("Calentamiento", Modifier.padding(top = 12.dp)) }
            Button(onClick = { model.add(draft.id, exercises[selected].getString("id"), weight, unit, reps, warmup, rir) }, enabled = !model.busy) { Text("Guardar serie") }
            data.getJSONArray("sets").objects().forEach { set ->
                val name = exercises.first { it.getString("id") == set.getString("exercise_id") }.getString("name")
                Text("$name · ${set.getString("weight")} ${set.getString("unit")} × ${set.getInt("reps")}" + if (set.getString("kind") == "warmup") " · Calentamiento" else "")
            }
            OutlinedButton(onClick = { confirmFinish = true }, enabled = !model.busy) { Text("Terminar sesión") }
        }
    }
    if (confirmFinish) AlertDialog(onDismissRequest = { confirmFinish = false }, title = { Text("¿Terminar sesión?") },
        text = { Text("Las series quedarán guardadas para sincronizar. Esta primera versión no permite editar una sesión terminada.") },
        confirmButton = { TextButton(onClick = { confirmFinish = false; model.finish(draft.id) }) { Text("Terminar") } },
        dismissButton = { TextButton(onClick = { confirmFinish = false }) { Text("Seguir entrenando") } })
}
