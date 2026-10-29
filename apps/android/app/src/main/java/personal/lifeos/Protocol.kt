package personal.lifeos

import org.json.JSONArray
import org.json.JSONObject
import java.time.Instant
import java.util.UUID

fun newId(): String = UUID.randomUUID().toString()
fun JSONArray.objects(): List<JSONObject> = (0 until length()).map { getJSONObject(it) }

object Training {
    fun start(routine: Central, now: Instant = Instant.now()): Draft {
        require(routine.type == "routine" && !routine.deleted)
        return Draft(newId(), JSONObject().put("routine_id", routine.id)
            .put("routine_version", routine.version).put("routine_snapshot", JSONObject(routine.json))
            .put("started_at", now.toString()).put("finished_at", JSONObject.NULL)
            .put("sets", JSONArray()).toString())
    }

    fun addSet(draft: Draft, exerciseId: String, weight: String, unit: String, reps: Int, kind: String, rir: Int?): Draft {
        require(!draft.completed) { "La sesión ya terminó" }
        require(Regex("^(0|[1-9][0-9]{0,3})(\\.[0-9]{1,3})?$").matches(weight)) { "Peso: 0–9999, hasta tres decimales" }
        require(unit in listOf("kg", "lb") && reps in 0..1000 && kind in listOf("work", "warmup")) { "Serie inválida" }
        require(rir == null || rir in 0..20) { "RIR: 0–20" }
        val data = JSONObject(draft.json)
        require(data.getJSONObject("routine_snapshot").getJSONArray("exercises").objects().any { it.getString("id") == exerciseId })
        val sets = data.getJSONArray("sets")
        require(sets.length() < 1000) { "Máximo de 1000 series por sesión" }
        sets.put(JSONObject().put("id", newId()).put("exercise_id", exerciseId)
            .put("weight", weight).put("unit", unit).put("reps", reps).put("kind", kind)
            .put("rir", rir ?: JSONObject.NULL))
        return draft.copy(json = data.toString())
    }

    fun finish(draft: Draft, now: Instant = Instant.now()): Pair<Draft, Pending> {
        require(!draft.completed)
        val data = JSONObject(draft.json)
        require(!now.isBefore(Instant.parse(data.getString("started_at")))) { "Revisa la fecha del móvil" }
        data.put("finished_at", now.toString())
        val done = draft.copy(json = data.toString(), completed = true)
        return done to operation(done.id, 0, data)
    }

    fun operation(entityId: String, base: Long, data: JSONObject): Pending {
        val id = newId()
        return Pending(id, entityId, JSONObject().put("operation_id", id).put("entity_type", "session")
            .put("entity_id", entityId).put("base_version", base).put("action", "upsert").put("data", data).toString())
    }
}

fun centralRecord(json: JSONObject): Central {
    val type = json.getString("entity_type")
    require(type in listOf("routine", "session"))
    val id = json.getString("entity_id")
    require(UUID.fromString(id).toString() == id)
    val version = json.getLong("version")
    require(version > 0)
    return Central(type, id, version, json.getBoolean("deleted"), json.get("data").toString())
}
