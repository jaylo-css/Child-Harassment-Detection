package org.safechat.guardian.listener

import android.Manifest
import android.annotation.SuppressLint
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.app.NotificationChannelCompat
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import org.safechat.guardian.R
import org.safechat.guardian.ui.MainActivity

/** The persistent, low-priority "protection is on" notification. Transparency is required:
 *  the app never hides its icon or disguises itself. */
object StatusNotifier {
    private const val CHANNEL = "protection_status"
    private const val ID = 1001

    fun canPost(context: Context): Boolean =
        (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU ||
            ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) ==
            PackageManager.PERMISSION_GRANTED) && NotificationManagerCompat.from(context).areNotificationsEnabled()

    fun showProtectionOn(context: Context) =
        post(context, "SafeChat Guardian protection is on", "Checking chat notifications for warning signs.")

    fun showPaused(context: Context) =
        post(context, "SafeChat Guardian protection is paused", "Notification access is off. Open the app to fix it.")

    @SuppressLint("MissingPermission") // checked in canPost()
    private fun post(context: Context, title: String, text: String) {
        if (!canPost(context)) return
        val nm = NotificationManagerCompat.from(context)
        nm.createNotificationChannel(
            NotificationChannelCompat.Builder(CHANNEL, NotificationManagerCompat.IMPORTANCE_LOW)
                .setName("Protection status")
                .setDescription("Shows that SafeChat Guardian is running")
                .setShowBadge(false)
                .build()
        )
        val open = PendingIntent.getActivity(
            context, 0, Intent(context, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        val n = NotificationCompat.Builder(context, CHANNEL)
            .setSmallIcon(R.drawable.ic_shield)
            .setContentTitle(title)
            .setContentText(text)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .setContentIntent(open)
            .build()
        nm.notify(ID, n)
    }
}
