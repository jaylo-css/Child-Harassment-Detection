package org.safechat.guardian.net

import android.content.Context
import androidx.work.BackoffPolicy
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import org.safechat.guardian.Graph
import org.safechat.guardian.core.QueueFlusher
import java.util.concurrent.TimeUnit

/** Flushes the encrypted offline queue when the network is available; also sends a heartbeat so
 *  the dashboard can warn if this device goes silent. */
class FlushWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {

    override suspend fun doWork(): Result {
        val g = Graph.get(applicationContext)
        val token = g.store.deviceToken ?: return Result.success() // not paired: keep events queued
        val ws = g.store.wsUrl ?: return Result.success()
        val server = g.store.serverUrl
        val result = QueueFlusher(g.queue, WebSocketTransport(g.http, ws, token)).flush()
        val beat = server != null && g.pairing.heartbeat(server, token)
        if (result.sent > 0 || beat) g.store.lastSyncMs = System.currentTimeMillis()
        return if (result.stalled && result.remaining > 0) Result.retry() else Result.success()
    }

    companion object {
        private const val ONE_SHOT = "guardian_flush"
        private const val PERIODIC = "guardian_flush_periodic"

        private fun constraints() = Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build()

        fun enqueue(context: Context) {
            val req = OneTimeWorkRequestBuilder<FlushWorker>()
                .setConstraints(constraints())
                .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 30, TimeUnit.SECONDS)
                .build()
            WorkManager.getInstance(context).enqueueUniqueWork(ONE_SHOT, ExistingWorkPolicy.APPEND_OR_REPLACE, req)
        }

        fun schedulePeriodic(context: Context) {
            val req = PeriodicWorkRequestBuilder<FlushWorker>(15, TimeUnit.MINUTES).setConstraints(constraints()).build()
            WorkManager.getInstance(context).enqueueUniquePeriodicWork(PERIODIC, ExistingPeriodicWorkPolicy.KEEP, req)
        }
    }
}
