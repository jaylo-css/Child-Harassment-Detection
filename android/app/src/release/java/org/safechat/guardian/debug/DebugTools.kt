package org.safechat.guardian.debug

import androidx.compose.runtime.Composable

/** Release builds ship no debug tools: no simulator, and the listener ignores the app's own package. */
object DebugTools {
    const val ENABLED = false

    @Composable
    fun SimulateChatScreen(onBack: () -> Unit) {
        onBack()
    }
}
