package personal.lifeos

import android.content.Context
import androidx.test.core.app.ApplicationProvider
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.flow.first
import org.json.JSONArray
import org.json.JSONObject
import org.junit.*
import org.junit.Assert.*
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import java.io.IOException
import java.time.Instant

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34], application = android.app.Application::class)
class OfflineTest {
    private val context get() = ApplicationProvider.getApplicationContext<Context>()
    private lateinit var db: LifeDatabase
    private lateinit var repo: Repository
    private val name = "test-${newId()}.db"
    private val routineId = newId()
    private val exerciseId = newId()
    private fun routine() = Central("routine", routineId, 1, false, JSONObject().put("name", "Push")
        .put("exercises", JSONArray().put(JSONObject().put("id", exerciseId).put("name", "Press")
            .put("target_sets", 3).put("reps_min", 8).put("reps_max", 12))).toString())
    @Before fun open() { db = LifeDatabase.open(context, name); repo = Repository(db) }
    @After fun close() { db.close(); context.deleteDatabase(name) }

    @Test fun offlineSeriesSurviveDatabaseReopen() = runBlocking {
        repo.dao.put(routine())
        val draft = repo.start(routine())
        repo.addSet(draft.id, exerciseId, "22.125", "kg", 10, "work", 2)
        db.close(); open()
        val restored = requireNotNull(repo.dao.draft(draft.id))
        val data = JSONObject(restored.json)
        assertEquals("22.125", data.getJSONArray("sets").getJSONObject(0).getString("weight"))
        assertEquals("Push", data.getJSONObject("routine_snapshot").getString("name"))
        repo.finish(draft.id)
        db.close(); open()
        assertTrue(requireNotNull(repo.dao.draft(draft.id)).completed)
        assertEquals(1, repo.dao.pending().size)
    }

    @Test fun lostResponseReplaysIdenticalOperationAfterReopen() = runBlocking {
        val draft = repo.start(routine()); repo.finish(draft.id)
        val original = repo.dao.pending().single()
        var received: String? = null
        val api = object : SyncApi {
            var lose = true
            override fun push(operation: String): JSONObject {
                if (received == null) received = operation else assertEquals(received, operation)
                if (lose) { lose = false; throw IOException("response lost") }
                return applied(operation)
            }
            override fun pull(cursor: String?) = page(emptyList(), "cursor-1", false)
        }
        try { repo.sync(api); fail("Expected network failure") } catch (_: IOException) { }
        db.close(); open()
        assertEquals(original.json, repo.dao.pending().single().json)
        repo.sync(api)
        assertTrue(repo.dao.pending().isEmpty())
        assertNull(repo.dao.draft(draft.id))
        assertNotNull(repo.dao.central("session", draft.id))
    }

    @Test fun paginatedPullKeepsDraftsAndIgnoresOlderVersions() = runBlocking {
        val draft = repo.start(routine())
        val newer = routine().copy(version = 2)
        val api = object : SyncApi {
            override fun push(operation: String) = error("Unexpected push")
            override fun pull(cursor: String?) = if (cursor == null) page(listOf(newer), "first", true)
                else page(listOf(routine()), "second", false)
        }
        repo.sync(api)
        assertEquals(2L, repo.dao.central("routine", routineId)?.version)
        assertEquals(draft, repo.dao.draft(draft.id))
        assertEquals("second", repo.dao.meta("cursor"))
    }

    @Test fun conflictRemainsVisibleAndDeletedRecordResolvesAsCopy() = runBlocking {
        val draft = repo.start(routine()); repo.finish(draft.id)
        val deleted = Central("session", draft.id, 4, true, "null")
        val api = object : SyncApi {
            override fun push(operation: String) = JSONObject().put("protocol_version", 1).put("results", JSONArray()
                .put(JSONObject().put("operation_id", JSONObject(operation).getString("operation_id"))
                    .put("status", "conflict").put("record", wire(deleted))))
            override fun pull(cursor: String?) = page(emptyList(), "end", false)
        }
        repo.sync(api)
        val pending = repo.dao.pending().single()
        assertNotNull(pending.conflict)
        assertNotNull(repo.dao.draft(draft.id))
        repo.resolve(pending.id, true)
        val replacement = repo.dao.pending().single()
        assertNotEquals(pending.id, replacement.id)
        assertNotEquals(draft.id, replacement.entityId)
        assertEquals(0, JSONObject(replacement.json).getInt("base_version"))
        assertNotNull(repo.dao.draft(replacement.entityId))
    }

    @Test fun choosingCentralRemovesOnlyConflictingLocalDraft() = runBlocking {
        val draft = repo.start(routine()); repo.finish(draft.id)
        val pending = repo.dao.pending().single()
        repo.dao.put(pending.copy(conflict = "null"))
        repo.resolve(pending.id, false)
        assertTrue(repo.dao.pending().isEmpty())
        assertNull(repo.dao.draft(draft.id))
    }

    @Test fun oneConflictingSessionHasOneOverviewCountAndAnActionableStatus() = runBlocking {
        val draft = repo.start(routine()); repo.finish(draft.id)
        val remote = Central("session", draft.id, 3, false, repo.dao.draft(draft.id)!!.json)
        val api = object : SyncApi {
            override fun push(operation: String) = JSONObject().put("protocol_version", 1).put("results", JSONArray()
                .put(JSONObject().put("operation_id", JSONObject(operation).getString("operation_id"))
                    .put("status", "conflict").put("record", wire(remote))))
            override fun pull(cursor: String?) = page(emptyList(), "end", false)
        }
        repo.sync(api)
        assertTrue(syncCompletionMessage(repo.dao.pending()).contains("conflicto"))
        assertEquals(1, registeredSessionCount(repo.dao.observeCentral().first(), repo.dao.observeDrafts().first()))
        repo.resolve(repo.dao.pending().single().id, false)
        assertEquals(1, registeredSessionCount(repo.dao.observeCentral().first(), repo.dao.observeDrafts().first()))
        assertEquals("Sincronización completada", syncCompletionMessage(repo.dao.pending()))
    }

    @Test fun invalidWeightsAndDatesNeverEnterOutbox() = runBlocking {
        val draft = repo.start(routine())
        for (weight in listOf("-1", "01", "2.0001", "NaN", "10000")) {
            try { repo.addSet(draft.id, exerciseId, weight, "kg", 8, "work", null); fail(weight) }
            catch (_: IllegalArgumentException) { }
        }
        assertEquals(0, JSONObject(repo.dao.draft(draft.id)!!.json).getJSONArray("sets").length())
        try { Training.finish(draft, Instant.EPOCH); fail("Backwards time") } catch (_: IllegalArgumentException) { }
        assertTrue(repo.dao.pending().isEmpty())
    }

    private fun applied(operation: String): JSONObject {
        val op = JSONObject(operation)
        val record = Central("session", op.getString("entity_id"), 1, false, op.getJSONObject("data").toString())
        return JSONObject().put("protocol_version", 1).put("results", JSONArray().put(JSONObject()
            .put("operation_id", op.getString("operation_id")).put("status", "applied").put("record", wire(record))))
    }
}

fun wire(record: Central): JSONObject = JSONObject().put("entity_type", record.type).put("entity_id", record.id)
    .put("version", record.version).put("deleted", record.deleted)
    .put("data", if (record.deleted) JSONObject.NULL else JSONObject(record.json))
fun page(records: List<Central>, cursor: String, more: Boolean): JSONObject = JSONObject().put("protocol_version", 1)
    .put("changes", JSONArray(records.map(::wire))).put("next_cursor", cursor).put("has_more", more)
