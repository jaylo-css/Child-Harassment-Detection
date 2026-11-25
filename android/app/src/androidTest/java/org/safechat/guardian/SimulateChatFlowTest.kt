package org.safechat.guardian

import android.Manifest
import android.os.Build
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import kotlinx.coroutines.delay
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.safechat.guardian.core.SIM_PLATFORM_LABEL
import org.safechat.guardian.debug.ChatSimulator
import org.safechat.guardian.listener.GuardianNotificationListener

/**
 * Debug "Simulate chat" flow on a device/emulator: simulated MessagingStyle notifications are
 * read by the listener, deduplicated (each post re-sends the history), hashed and queued.
 * The device is not paired in this test, so events stay in the encrypted queue.
 */
@RunWith(AndroidJUnit4::class)
class SimulateChatFlowTest {

    @Test
    fun simulatedChatReachesEncryptedQueue() = runBlocking {
        val instr = InstrumentationRegistry.getInstrumentation()
        val ctx = instr.targetContext
        val ui = instr.uiAutomation
        val component = "${ctx.packageName}/${GuardianNotificationListener::class.java.name}"
        ui.executeShellCommand("cmd notification allow_listener $component").close()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            ui.grantRuntimePermission(ctx.packageName, Manifest.permission.POST_NOTIFICATIONS)
        }
        delay(1500) // let the listener bind

        val graph = Graph.get(ctx)
        graph.store.clearPairing()
        graph.queue.clear()
        val sim = ChatSimulator(ctx)
        sim.reset()
        val lines = ChatSimulator.SCENARIO.take(4)
        lines.forEach { (fromChild, text) ->
            assertTrue(sim.post(fromChild, text))
            delay(400)
        }

        var events = graph.queue.peek(100)
        repeat(40) {
            if (events.size >= lines.size) return@repeat
            delay(250)
            events = graph.queue.peek(100)
        }
        sim.reset()

        assertEquals("each message queued exactly once despite re-posts", lines.size, events.size)
        assertTrue(events.all { it.platform == SIM_PLATFORM_LABEL })
        assertEquals(lines.map { it.first }, events.map { it.isChild })
        assertFalse(events.any { it.senderHash.contains("SimStranger", ignoreCase = true) })
        assertEquals(lines.map { it.second }, events.map { it.text })
        graph.queue.clear()
    }
}
