package org.safechat.guardian.data

import android.content.Context
import androidx.room.Dao
import androidx.room.Database
import androidx.room.Entity
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.PrimaryKey
import androidx.room.Query
import androidx.room.Room
import androidx.room.RoomDatabase
import kotlinx.serialization.json.Json
import net.zetetic.database.sqlcipher.SupportOpenHelperFactory
import org.safechat.guardian.core.EventQueue
import org.safechat.guardian.core.WireEvent

@Entity(tableName = "queued_events")
data class QueuedEventEntity(
    @PrimaryKey val eventId: String,
    val payload: String,
    val createdAt: Long,
)

@Dao
interface QueueDao {
    @Insert(onConflict = OnConflictStrategy.IGNORE)
    suspend fun insert(e: QueuedEventEntity)

    @Query("SELECT * FROM queued_events ORDER BY createdAt ASC LIMIT :limit")
    suspend fun peek(limit: Int): List<QueuedEventEntity>

    @Query("DELETE FROM queued_events WHERE eventId IN (:ids)")
    suspend fun delete(ids: List<String>)

    @Query("SELECT COUNT(*) FROM queued_events")
    suspend fun count(): Int

    @Query("DELETE FROM queued_events")
    suspend fun clear()

    @Query(
        "DELETE FROM queued_events WHERE eventId NOT IN " +
            "(SELECT eventId FROM queued_events ORDER BY createdAt DESC LIMIT :keep)"
    )
    suspend fun trim(keep: Int)
}

@Database(entities = [QueuedEventEntity::class], version = 1, exportSchema = false)
abstract class QueueDatabase : RoomDatabase() {
    abstract fun queueDao(): QueueDao

    companion object {
        /** SQLCipher-encrypted database; the passphrase lives in EncryptedSharedPreferences. */
        fun build(context: Context, passphrase: ByteArray): QueueDatabase {
            System.loadLibrary("sqlcipher")
            return Room.databaseBuilder(context, QueueDatabase::class.java, "guardian_queue.db")
                .openHelperFactory(SupportOpenHelperFactory(passphrase))
                .fallbackToDestructiveMigration()
                .build()
        }
    }
}

/** Events are deleted as soon as the backend acknowledges them; the queue is capped. */
class RoomEventQueue(private val dao: QueueDao, private val maxSize: Int = 5000) : EventQueue {
    private val json = Json { ignoreUnknownKeys = true }

    override suspend fun enqueue(event: WireEvent) {
        dao.insert(QueuedEventEntity(event.eventId, json.encodeToString(WireEvent.serializer(), event), System.currentTimeMillis()))
        dao.trim(maxSize)
    }

    override suspend fun peek(limit: Int): List<WireEvent> =
        dao.peek(limit).mapNotNull { runCatching { json.decodeFromString(WireEvent.serializer(), it.payload) }.getOrNull() }

    override suspend fun delete(eventIds: Collection<String>) {
        eventIds.chunked(500).forEach { dao.delete(it) }
    }

    override suspend fun count(): Int = dao.count()

    override suspend fun clear() = dao.clear()
}
