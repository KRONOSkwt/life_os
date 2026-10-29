package personal.lifeos

import android.content.Context
import androidx.room.*
import kotlinx.coroutines.flow.Flow

// Central records and local drafts are separate: pull can never overwrite a draft.
@Entity(primaryKeys = ["type", "id"])
data class Central(val type: String, val id: String, val version: Long, val deleted: Boolean, val json: String)
@Entity
data class Draft(@PrimaryKey val id: String, val json: String, val completed: Boolean = false)
@Entity
data class Pending(@PrimaryKey val id: String, val entityId: String, val json: String, val conflict: String? = null)
@Entity
data class Metadata(@PrimaryKey val name: String, val value: String)

@Dao
interface LifeDao {
    @Query("SELECT * FROM Central ORDER BY type, id") fun observeCentral(): Flow<List<Central>>
    @Query("SELECT * FROM Draft ORDER BY id") fun observeDrafts(): Flow<List<Draft>>
    @Query("SELECT * FROM Pending ORDER BY id") fun observePending(): Flow<List<Pending>>
    @Query("SELECT * FROM Central WHERE type = :type AND id = :id") suspend fun central(type: String, id: String): Central?
    @Query("SELECT * FROM Draft WHERE id = :id") suspend fun draft(id: String): Draft?
    @Query("SELECT * FROM Pending ORDER BY id") suspend fun pending(): List<Pending>
    @Query("SELECT value FROM Metadata WHERE name = :name") suspend fun meta(name: String): String?
    @Upsert suspend fun put(record: Central)
    @Upsert suspend fun put(draft: Draft)
    @Upsert suspend fun put(pending: Pending)
    @Upsert suspend fun put(metadata: Metadata)
    @Query("DELETE FROM Pending WHERE id = :id") suspend fun removePending(id: String)
    @Query("DELETE FROM Draft WHERE id = :id") suspend fun removeDraft(id: String)
}

@Database(entities = [Central::class, Draft::class, Pending::class, Metadata::class], version = 1, exportSchema = true)
abstract class LifeDatabase : RoomDatabase() {
    abstract fun dao(): LifeDao
    companion object {
        fun open(context: Context, name: String = "life-os.db") =
            Room.databaseBuilder(context, LifeDatabase::class.java, name).build()
    }
}
