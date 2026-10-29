package personal.lifeos

import android.content.Context
import androidx.test.core.app.ApplicationProvider
import kotlinx.coroutines.runBlocking
import org.json.JSONObject
import org.junit.Test
import org.junit.Assert.*
import org.junit.Assume.assumeTrue
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import java.io.File
import java.io.IOException

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34], application = android.app.Application::class)
class ServerJourneyTest {
    @Test fun pairDownloadOfflineSessionReopenAndReplayAgainstFastApi() = runBlocking {
        val invitation = System.getenv("LIFE_OS_TEST_INVITATION")
        assumeTrue("Run scripts/smoke-android.py to enable real HTTPS journey", invitation != null)
        val credentials = ServerApi.pair(File(invitation!!).readText(), "Android JVM journey")
        val api = ServerApi(credentials)
        val context = ApplicationProvider.getApplicationContext<Context>()
        val name = "journey-${newId()}.db"
        var db = LifeDatabase.open(context, name)
        try {
            var repo = Repository(db)
            repo.sync(api)
            val pull = api.pull(null)
            val routine = pull.getJSONArray("changes").objects().map(::centralRecord).first { it.type == "routine" }
            assertNotNull(repo.dao.central("routine", routine.id))
            val draft = repo.start(routine)
            val exercise = JSONObject(routine.json).getJSONArray("exercises").getJSONObject(0).getString("id")
            repo.addSet(draft.id, exercise, "22.500", "kg", 10, "work", 2)
            db.close(); db = LifeDatabase.open(context, name); repo = Repository(db)
            assertEquals(1, JSONObject(repo.dao.draft(draft.id)!!.json).getJSONArray("sets").length())
            repo.finish(draft.id)
            val exact = repo.dao.pending().single().json
            val lossy = object : SyncApi {
                override fun push(operation: String): JSONObject { api.push(operation); throw IOException("Response deliberately lost") }
                override fun pull(cursor: String?) = api.pull(cursor)
            }
            try { repo.sync(lossy); fail("Expected lost response") } catch (_: IOException) { }
            db.close(); db = LifeDatabase.open(context, name); repo = Repository(db)
            assertEquals(exact, repo.dao.pending().single().json)
            repo.sync(api)
            repo.sync(api)
            assertTrue(repo.dao.pending().isEmpty())
            assertNull(repo.dao.draft(draft.id))
            val changes = mutableListOf<JSONObject>()
            var cursor: String? = null
            do {
                val page = api.pull(cursor)
                changes.addAll(page.getJSONArray("changes").objects())
                cursor = page.getString("next_cursor")
            } while (page.getBoolean("has_more"))
            val sessions = changes.filter { it.getString("entity_type") == "session" }
            assertEquals(1, sessions.size)
            assertEquals("22.500", sessions.single().getJSONObject("data").getJSONArray("sets").getJSONObject(0).getString("weight"))
        } finally { db.close(); context.deleteDatabase(name) }
    }
}
