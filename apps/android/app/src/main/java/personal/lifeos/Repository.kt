package personal.lifeos

import androidx.room.withTransaction
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import org.json.JSONObject

class Repository(val db: LifeDatabase) {
    val dao = db.dao()
    private val syncLock = Mutex()

    suspend fun start(routine: Central) = Training.start(routine).also { dao.put(it) }
    suspend fun addSet(id: String, exercise: String, weight: String, unit: String, reps: Int, kind: String, rir: Int?) = db.withTransaction {
        dao.put(Training.addSet(requireNotNull(dao.draft(id)), exercise, weight, unit, reps, kind, rir))
    }
    suspend fun finish(id: String) = db.withTransaction {
        val (draft, operation) = Training.finish(requireNotNull(dao.draft(id)))
        dao.put(draft)
        dao.put(operation)
    }
    private suspend fun integrate(record: Central) {
        val previous = dao.central(record.type, record.id)
        if (previous == null || record.version > previous.version) dao.put(record)
    }

    suspend fun sync(api: SyncApi) = syncLock.withLock {
        for (pending in dao.pending().filter { it.conflict == null }) {
            // Network happens outside transactions. The persisted operation never changes on retry.
            val response = api.push(pending.json)
            require(response.getInt("protocol_version") == 1)
            val results = response.getJSONArray("results")
            require(results.length() == 1)
            val result = results.getJSONObject(0)
            require(result.getString("operation_id") == pending.id)
            val status = result.getString("status")
            require(status in listOf("applied", "conflict"))
            val record = if (result.isNull("record")) null else centralRecord(result.getJSONObject("record"))
            require(record == null || (record.type == "session" && record.id == pending.entityId))
            require(status != "applied" || record != null)
            db.withTransaction {
                // A conflict decision in the UI may have superseded this snapshot.
                if (dao.pending().any { it.id == pending.id && it.conflict == null }) {
                    record?.let { integrate(it) }
                    if (status == "applied") {
                        dao.removePending(pending.id)
                        dao.removeDraft(pending.entityId)
                    } else dao.put(pending.copy(conflict = result.get("record").toString()))
                }
            }
        }
        var more: Boolean
        do {
            val cursor = dao.meta("cursor")
            val response = api.pull(cursor)
            require(response.getInt("protocol_version") == 1)
            val records = response.getJSONArray("changes").objects().map(::centralRecord)
            val next = response.getString("next_cursor")
            more = response.getBoolean("has_more")
            require(next.isNotEmpty() && (!more || next != cursor)) { "El cursor no avanzó" }
            db.withTransaction {
                records.forEach { integrate(it) }
                dao.put(Metadata("cursor", next))
            }
        } while (more)
    }

    suspend fun resolve(id: String, keepLocal: Boolean) = syncLock.withLock {
        db.withTransaction {
            val pending = dao.pending().first { it.id == id }
            val conflict = requireNotNull(pending.conflict)
            if (keepLocal) {
                val current = if (conflict == "null") null else centralRecord(JSONObject(conflict))
                val original = JSONObject(pending.json).getJSONObject("data")
                // A deleted ID cannot be reused. Preserve history by creating a new copy.
                val target = if (current?.deleted == true) newId() else pending.entityId
                dao.put(Training.operation(target, if (current?.deleted == true) 0 else current?.version ?: 0, original))
                if (target != pending.entityId) {
                    dao.put(Draft(target, original.toString(), true))
                    dao.removeDraft(pending.entityId)
                }
            } else dao.removeDraft(pending.entityId)
            dao.removePending(id)
        }
    }
}
