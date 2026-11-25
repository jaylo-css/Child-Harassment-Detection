package org.safechat.guardian.debug

import android.Manifest
import android.annotation.SuppressLint
import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.material3.Button
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.core.app.NotificationChannelCompat
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.app.Person
import androidx.core.content.ContextCompat
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import org.safechat.guardian.R
import org.safechat.guardian.core.SIM_CHANNEL_ID
import org.safechat.guardian.ui.Page

/** DEBUG BUILD ONLY. Lets you test phone -> backend -> alert without any real chats. */
object DebugTools {
    const val ENABLED = true

    @Composable
    fun SimulateChatScreen(onBack: () -> Unit) {
        val context = LocalContext.current
        val sim = remember { ChatSimulator(context) }
        val scope = rememberCoroutineScope()
        var step by remember { mutableIntStateOf(0) }
        Page("Debug: Simulate chat", onBack) {
            Text(
                "Posts fake MessagingStyle notifications from a synthetic, non-explicit scenario under a test channel. " +
                    "The listener accepts them only in debug builds. Notification access must be on."
            )
            Text("Step $step of ${ChatSimulator.SCENARIO.size}")
            Button(onClick = {
                if (step < ChatSimulator.SCENARIO.size) {
                    val (fromChild, text) = ChatSimulator.SCENARIO[step]
                    sim.post(fromChild, text)
                    step++
                }
            }, modifier = Modifier.fillMaxWidth()) { Text("Post next message") }
            Button(onClick = {
                scope.launch {
                    while (step < ChatSimulator.SCENARIO.size) {
                        val (fromChild, text) = ChatSimulator.SCENARIO[step]
                        sim.post(fromChild, text)
                        step++
                        delay(1500)
                    }
                }
            }, modifier = Modifier.fillMaxWidth()) { Text("Play whole scenario") }
            OutlinedButton(onClick = { sim.reset(); step = 0 }, modifier = Modifier.fillMaxWidth()) { Text("Reset") }
        }
    }
}

class ChatSimulator(private val context: Context) {
    private data class Line(val fromChild: Boolean, val text: String, val time: Long)

    private val history = mutableListOf<Line>()
    private val me = Person.Builder().setName("Me").setKey("sim-me").build()
    private val stranger = Person.Builder().setName("SimStranger").setKey("sim-stranger").build()

    init {
        NotificationManagerCompat.from(context).createNotificationChannel(
            NotificationChannelCompat.Builder(SIM_CHANNEL_ID, NotificationManagerCompat.IMPORTANCE_DEFAULT)
                .setName("Debug: simulated chat")
                .build()
        )
    }

    fun canPost(): Boolean = Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU ||
        ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED

    /** Re-posts the whole recent history, exactly like real chat apps do (exercises dedup). */
    @SuppressLint("MissingPermission")
    fun post(fromChild: Boolean, text: String): Boolean {
        if (!canPost()) return false
        history += Line(fromChild, text, System.currentTimeMillis())
        val style = NotificationCompat.MessagingStyle(me).setConversationTitle("Simulated chat")
        history.takeLast(8).forEach { line ->
            style.addMessage(NotificationCompat.MessagingStyle.Message(line.text, line.time, if (line.fromChild) null else stranger))
        }
        val n = NotificationCompat.Builder(context, SIM_CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_shield)
            .setContentTitle("SimStranger")
            .setContentText(text)
            .setStyle(style)
            .setCategory(NotificationCompat.CATEGORY_MESSAGE)
            .build()
        NotificationManagerCompat.from(context).notify(NOTIFICATION_ID, n)
        return true
    }

    fun reset() {
        history.clear()
        NotificationManagerCompat.from(context).cancel(NOTIFICATION_ID)
    }

    companion object {
        const val NOTIFICATION_ID = 4242

        /** Synthetic, non-explicit grooming trajectory (signals only), same as the backend scenario. */
        val SCENARIO: List<Pair<Boolean, String>> = listOf(
            false to "you're really good at bedwars",
            true to "thanks!",
            false to "i'll give you 800 robux, no big deal",
            false to "how old are you btw?",
            true to "12",
            false to "do your parents check your phone?",
            true to "no they never check",
            false to "you're so mature for your age, only i understand you",
            false to "don't tell anyone we talk ok? this is our little secret",
            true to "ok i won't",
            false to "add me on snap, it's more private",
            false to "are you home alone this weekend?",
            false to "send me a pic of you, just for me",
            true to "why do you want that?",
            false to "let's meet at the mall, don't tell your mom",
        )
    }
}
