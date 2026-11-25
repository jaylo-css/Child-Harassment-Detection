package org.safechat.guardian

import android.app.Application
import android.content.Context
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import okhttp3.OkHttpClient
import org.safechat.guardian.core.Allowlist
import org.safechat.guardian.core.Deduplicator
import org.safechat.guardian.core.EventMapper
import org.safechat.guardian.core.EventQueue
import org.safechat.guardian.core.Hasher
import org.safechat.guardian.core.NotificationParser
import org.safechat.guardian.data.QueueDatabase
import org.safechat.guardian.data.RoomEventQueue
import org.safechat.guardian.data.SecureStore
import org.safechat.guardian.net.FlushWorker
import org.safechat.guardian.net.PairingClient
import java.util.concurrent.TimeUnit

/** Tiny service locator (no DI framework for the prototype). */
class Graph private constructor(private val app: Context) {
    val store = SecureStore(app)
    val allowlist: Allowlist = app.assets.open("allowlist.json").bufferedReader().use { Allowlist.parse(it.readText()) }
    val parser = NotificationParser()
    val dedup = Deduplicator(500)
    val hasher by lazy { Hasher(store.salt()) }
    val mapper by lazy { EventMapper(hasher) }
    private val db by lazy { QueueDatabase.build(app, store.dbPassphrase()) }
    val queue: EventQueue by lazy { RoomEventQueue(db.queueDao()) }
    val http: OkHttpClient = OkHttpClient.Builder()
        .connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(30, TimeUnit.SECONDS)
        .pingInterval(20, TimeUnit.SECONDS)
        .build()
    val pairing = PairingClient(http)
    val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    fun enabledApps(): Set<String> = store.enabledApps ?: allowlist.defaults()

    fun scheduleFlush() = FlushWorker.enqueue(app)

    companion object {
        @Volatile private var instance: Graph? = null

        fun get(context: Context): Graph =
            instance ?: synchronized(this) { instance ?: Graph(context.applicationContext).also { instance = it } }
    }
}

class GuardianApp : Application() {
    override fun onCreate() {
        super.onCreate()
        if (Graph.get(this).store.isPaired) FlushWorker.schedulePeriodic(this)
    }
}
