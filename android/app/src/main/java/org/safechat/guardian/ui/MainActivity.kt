package org.safechat.guardian.ui

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.BackHandler
import androidx.activity.compose.setContent
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.graphics.Color
import org.safechat.guardian.Graph
import org.safechat.guardian.debug.DebugTools

enum class Screen { GUARDIAN_SETUP, CHILD_NOTICE, STATUS, PAIRING, APPS, ABOUT, DEBUG_SIMULATE }

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val graph = Graph.get(this)
        setContent { GuardianTheme { App(graph) } }
    }
}

@Composable
fun GuardianTheme(content: @Composable () -> Unit) {
    val dark = isSystemInDarkTheme()
    val scheme = if (dark) {
        darkColorScheme(primary = Color(0xFF6AA8D6), secondary = Color(0xFF9AA8B8))
    } else {
        lightColorScheme(primary = Color(0xFF2F6F9F), secondary = Color(0xFF5D6B7C))
    }
    MaterialTheme(colorScheme = scheme, content = content)
}

@Composable
fun App(graph: Graph) {
    val start = when {
        !graph.store.guardianConsent -> Screen.GUARDIAN_SETUP
        !graph.store.childNoticeShown -> Screen.CHILD_NOTICE
        else -> Screen.STATUS
    }
    var screen by rememberSaveable { mutableStateOf(start) }
    val back = { screen = Screen.STATUS }
    BackHandler(enabled = screen in setOf(Screen.PAIRING, Screen.APPS, Screen.ABOUT, Screen.DEBUG_SIMULATE)) { back() }

    when (screen) {
        Screen.GUARDIAN_SETUP -> GuardianSetupScreen(onAccept = {
            graph.store.guardianConsent = true
            screen = Screen.CHILD_NOTICE
        })
        Screen.CHILD_NOTICE -> ChildNoticeScreen(onDone = {
            graph.store.childNoticeShown = true
            screen = Screen.STATUS
        })
        Screen.STATUS -> StatusScreen(
            graph = graph,
            onPair = { screen = Screen.PAIRING },
            onApps = { screen = Screen.APPS },
            onAbout = { screen = Screen.ABOUT },
            onChildNotice = { screen = Screen.CHILD_NOTICE },
            onDebug = if (DebugTools.ENABLED) ({ screen = Screen.DEBUG_SIMULATE }) else null,
        )
        Screen.PAIRING -> PairingScreen(graph, onBack = back)
        Screen.APPS -> AppsScreen(graph, onBack = back)
        Screen.ABOUT -> AboutScreen(onBack = back)
        Screen.DEBUG_SIMULATE -> DebugTools.SimulateChatScreen(onBack = back)
    }
}
