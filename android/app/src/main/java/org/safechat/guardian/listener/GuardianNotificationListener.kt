package org.safechat.guardian.listener

import android.app.Notification
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import android.util.Log
import kotlinx.coroutines.launch
import org.safechat.guardian.BuildConfig
import org.safechat.guardian.Graph
import org.safechat.guardian.core.NotificationSnapshot
import org.safechat.guardian.core.SIM_CHANNEL_ID
import org.safechat.guardian.core.SIM_PLATFORM_LABEL

/**
 * Reads incoming chat notifications from allowlisted apps. READ-ONLY: it never replies, never
 * opens chats, never triggers notification actions and never dismisses notifications.
 */
class GuardianNotificationListener : NotificationListenerService() {

    override fun onListenerConnected() {
        StatusNotifier.showProtectionOn(this)
    }

    override fun onListenerDisconnected() {
        StatusNotifier.showPaused(this)
    }

    override fun onNotificationPosted(sbn: StatusBarNotification) {
        try {
            handle(sbn)
        } catch (e: Exception) {
            Log.w(TAG, "could not process a notification: ${e.javaClass.simpleName}") // never log content
        }
    }

    private fun handle(sbn: StatusBarNotification) {
        val g = Graph.get(this)
        val pkg = sbn.packageName
        val n = sbn.notification ?: return
        // Our own package is accepted only in debug builds, only on the simulated-chat test channel.
        val isOwnDebugSim = BuildConfig.DEBUG && pkg == packageName && n.channelId == SIM_CHANNEL_ID
        if (pkg == packageName && !isOwnDebugSim) return
        if (!isOwnDebugSim && !g.allowlist.isAllowed(pkg, g.enabledApps())) return

        val snapshot = NotificationSnapshot(
            packageName = pkg,
            postTime = sbn.postTime,
            whenTime = n.`when`,
            isGroupSummary = (n.flags and Notification.FLAG_GROUP_SUMMARY) != 0,
            isOngoing = (n.flags and Notification.FLAG_ONGOING_EVENT) != 0,
            category = n.category,
            channelId = n.channelId,
            extras = BundleExtras.from(n.extras),
        )
        val fresh = g.parser.parse(snapshot).filter { g.dedup.firstTime(it) }
        if (fresh.isEmpty()) return
        val platform = if (isOwnDebugSim) SIM_PLATFORM_LABEL else g.allowlist.label(pkg) ?: pkg
        g.scope.launch {
            fresh.forEach { g.queue.enqueue(g.mapper.toWire(it, platform)) }
            g.scheduleFlush()
        }
    }

    companion object {
        private const val TAG = "GuardianListener"
    }
}
