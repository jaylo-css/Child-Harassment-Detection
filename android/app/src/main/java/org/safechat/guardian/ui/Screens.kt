package org.safechat.guardian.ui

import android.Manifest
import android.content.Context
import android.content.Intent
import android.os.Build
import android.text.format.DateUtils
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.Checkbox
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.delay
import org.safechat.guardian.Graph
import org.safechat.guardian.listener.StatusNotifier

@Composable
fun Page(title: String, onBack: (() -> Unit)? = null, content: @Composable () -> Unit) {
    Surface(Modifier.fillMaxSize()) {
        Column(
            Modifier.safeDrawingPadding().verticalScroll(rememberScrollState()).padding(20.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                if (onBack != null) TextButton(onClick = onBack) { Text("Back") }
                Text(title, style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.SemiBold)
            }
            content()
        }
    }
}

@Composable
private fun CheckRow(text: String, checked: Boolean, onChange: (Boolean) -> Unit) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        Checkbox(checked = checked, onCheckedChange = onChange)
        Text(text, Modifier.padding(start = 4.dp))
    }
}

// ------------------------------------------------------------------------------------------
// Onboarding part 1: the guardian sets it up and consents.
// ------------------------------------------------------------------------------------------
@Composable
fun GuardianSetupScreen(onAccept: () -> Unit) {
    var isGuardian by remember { mutableStateOf(false) }
    var willTell by remember { mutableStateOf(false) }
    var understands by remember { mutableStateOf(false) }
    Page("Set up SafeChat Guardian") {
        Text("For parents and legal guardians", fontWeight = FontWeight.SemiBold)
        Text(
            "SafeChat Guardian reads chat notifications from the apps you choose on this phone and sends " +
                "them to your SafeChat Guardian dashboard, which checks them for warning signs that someone may " +
                "be grooming your child. You get an alert with the warning signs and at most two short, redacted " +
                "excerpts, never the full conversation."
        )
        Text(
            "It only observes. It never replies to anyone, never opens chats, and never contacts the other " +
                "person. Reports to authorities or platforms are always your decision."
        )
        Text(
            "Only use this on the phone of a child you are legally responsible for, and tell your child " +
                "that protection is on. The next screen is a short explanation for your child."
        )
        CheckRow("I am this child's parent or legal guardian", isGuardian) { isGuardian = it }
        CheckRow("I will tell my child that SafeChat Guardian is on", willTell) { willTell = it }
        CheckRow("I understand this is a prototype and alerts can be wrong", understands) { understands = it }
        Button(onClick = onAccept, enabled = isGuardian && willTell && understands, modifier = Modifier.fillMaxWidth()) {
            Text("Continue")
        }
    }
}

// ------------------------------------------------------------------------------------------
// Onboarding part 2: age-appropriate notice for the child.
// ------------------------------------------------------------------------------------------
@Composable
fun ChildNoticeScreen(onDone: () -> Unit) {
    Page("Hi! Your phone has SafeChat Guardian") {
        val big = Modifier.padding(vertical = 2.dp)
        Text(
            "SafeChat Guardian looks at chat notifications for signs that someone is trying to trick you or hurt you.",
            fontSize = 18.sp, modifier = big,
        )
        Text(
            "For example: someone asking you to keep secrets, asking for photos, asking where you live, or asking " +
                "to meet you.",
            fontSize = 18.sp, modifier = big,
        )
        Text(
            "Your parent is told only about warning signs, not every message.",
            fontSize = 18.sp, fontWeight = FontWeight.SemiBold, modifier = big,
        )
        Text("It never sends messages to anyone for you.", fontSize = 18.sp, modifier = big)
        Text(
            "If someone online makes you feel weird, scared or pressured, you can always talk to your parent or " +
                "another adult you trust. You will not be in trouble.",
            fontSize = 18.sp, modifier = big,
        )
        Text(
            "You will see a small notification that says “SafeChat Guardian protection is on”.",
            fontSize = 16.sp,
        )
        Button(onClick = onDone, modifier = Modifier.fillMaxWidth()) { Text("Got it") }
    }
}

// ------------------------------------------------------------------------------------------
// Permission status checklist (home)
// ------------------------------------------------------------------------------------------
@Composable
private fun StatusItem(ok: Boolean, title: String, detail: String, action: String?, onAction: () -> Unit) {
    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Text(
                (if (ok) "✓  " else "✗  ") + title,
                fontWeight = FontWeight.SemiBold,
                color = if (ok) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.error,
            )
            Text(detail, style = MaterialTheme.typography.bodyMedium)
            if (!ok && action != null) OutlinedButton(onClick = onAction) { Text(action) }
        }
    }
}

@Composable
fun StatusScreen(
    graph: Graph,
    onPair: () -> Unit,
    onApps: () -> Unit,
    onAbout: () -> Unit,
    onChildNotice: () -> Unit,
    onDebug: (() -> Unit)?,
) {
    val context = LocalContext.current
    var tick by remember { mutableIntStateOf(0) }
    LaunchedEffect(Unit) {
        while (true) {
            delay(2000)
            tick++
        }
    }
    val notifPermission = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { tick++ }
    LaunchedEffect(Unit) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU && !SystemStatus.statusNotificationAllowed(context)) {
            notifPermission.launch(Manifest.permission.POST_NOTIFICATIONS)
        }
    }
    val queued by produceState(initialValue = 0, tick) { value = graph.queue.count() }

    // Recomputed every tick so the list updates after the guardian returns from Settings.
    val access = remember(tick) { SystemStatus.notificationAccess(context) }
    val battery = remember(tick) { SystemStatus.batteryUnrestricted(context) }
    val network = remember(tick) { SystemStatus.networkAvailable(context) }
    val statusNotif = remember(tick) { SystemStatus.statusNotificationAllowed(context) }
    val paired = remember(tick) { graph.store.isPaired }
    val lastSync = remember(tick) { graph.store.lastSyncMs }
    LaunchedEffect(access, statusNotif) { if (access) StatusNotifier.showProtectionOn(context) }

    Page("SafeChat Guardian") {
        Text(
            if (access && paired) "Protection is on." else "Finish the steps below to turn protection on.",
            style = MaterialTheme.typography.titleMedium,
        )
        StatusItem(
            access, "Notification access",
            "Needed to read chat notifications from the apps you choose.",
            "Open notification access",
        ) { SystemStatus.openNotificationAccess(context) }
        if (!access && Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            Card(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    Text("Toggle greyed out? (Android 13 and newer)", fontWeight = FontWeight.SemiBold)
                    Text(
                        "Apps installed outside the Play Store need one extra step: open App info, tap the ⋮ " +
                            "menu in the top corner, choose “Allow restricted settings”, confirm, then come " +
                            "back and turn on notification access."
                    )
                    OutlinedButton(onClick = { context.openSafely(SystemStatus.appInfoIntent(context)) }) {
                        Text("Open App info")
                    }
                }
            }
        }
        StatusItem(
            battery, "Battery optimization off",
            "Android may stop the app in the background otherwise. Choose “All apps”, find SafeChat " +
                "Guardian and select “Don't optimize” / “Unrestricted”.",
            "Open battery settings",
        ) { context.openSafely(SystemStatus.batteryIntent()) }
        StatusItem(
            statusNotif, "“Protection is on” notification",
            "Lets your child see that protection is on.",
            "Allow notifications",
        ) { context.openSafely(SystemStatus.appNotificationSettingsIntent(context)) }
        StatusItem(network, "Network", "Messages are sent when the phone is online; until then they wait in an encrypted queue.", null) {}
        StatusItem(
            paired, "Paired with your dashboard",
            if (paired) "Connected to ${graph.store.serverUrl}." else "Scan the QR code from the dashboard's Devices page.",
            "Pair now",
        ) { onPair() }
        Text(
            "Last sync: " + if (lastSync > 0) DateUtils.getRelativeTimeSpanString(lastSync).toString() else "never",
            style = MaterialTheme.typography.bodyMedium,
        )
        Text("Waiting to send: $queued", style = MaterialTheme.typography.bodyMedium)
        HorizontalDivider()
        OutlinedButton(onClick = onPair, Modifier.fillMaxWidth()) { Text(if (paired) "Re-pair" else "Pair with dashboard") }
        OutlinedButton(onClick = onApps, Modifier.fillMaxWidth()) { Text("Monitored apps") }
        OutlinedButton(onClick = onChildNotice, Modifier.fillMaxWidth()) { Text("Show the child notice again") }
        OutlinedButton(onClick = onAbout, Modifier.fillMaxWidth()) { Text("About & privacy") }
        if (onDebug != null) {
            OutlinedButton(onClick = onDebug, Modifier.fillMaxWidth()) { Text("Debug: Simulate chat") }
        }
    }
}

// ------------------------------------------------------------------------------------------
// Monitored apps
// ------------------------------------------------------------------------------------------
@Composable
fun AppsScreen(graph: Graph, onBack: () -> Unit) {
    var enabled by remember { mutableStateOf(graph.enabledApps()) }
    Page("Monitored apps", onBack) {
        Text("Only notifications from the apps switched on here are read. Everything else (banking, codes, email) is ignored.")
        graph.allowlist.apps.forEach { app ->
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text(app.label, fontWeight = FontWeight.SemiBold)
                    Text(app.packageName, style = MaterialTheme.typography.bodySmall)
                }
                Switch(checked = app.packageName in enabled, onCheckedChange = { on ->
                    enabled = if (on) enabled + app.packageName else enabled - app.packageName
                    graph.store.enabledApps = enabled
                })
            }
        }
        Spacer(Modifier.height(8.dp))
        Text(
            "Known limit: some games show in-game chat without notifications, and muted chats or chats open on " +
                "screen produce no notification, so they cannot be checked.",
            style = MaterialTheme.typography.bodySmall,
        )
    }
}

// ------------------------------------------------------------------------------------------
// About & privacy
// ------------------------------------------------------------------------------------------
@Composable
fun AboutScreen(onBack: () -> Unit) {
    Page("About & privacy", onBack) {
        Section("What it reads", "Chat notifications from the apps switched on in Monitored apps, and nothing else. It does not read the screen, other apps, photos, contacts or location.")
        Section("What leaves the phone", "For each new chat message: the app name, hashed (scrambled) IDs for the conversation and the sender, whether the message was sent by your child, the message text and its time. Names are hashed on this phone with a secret that never leaves it.")
        Section("Where it goes", "Only to the SafeChat Guardian dashboard you paired with, over an encrypted connection (HTTPS/WSS).")
        Section("How fast it is deleted", "Messages wait in an encrypted queue only until the dashboard confirms it received them, then they are deleted from the phone. The dashboard keeps raw text only for the last few messages it needs to score, and keeps alerts for the retention period you set (30 days by default).")
        Section("What it never does", "It never replies, never opens chats, never contacts the other person, never hides its icon, and never files reports automatically.")
        Section("Limits", "It only sees what appears in notifications: muted chats, chats open on screen and some in-game chats are missed, and long messages may be cut short. Alerts can be wrong in both directions.")
    }
}

@Composable
private fun Section(title: String, body: String) {
    Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
        Text(title, fontWeight = FontWeight.SemiBold)
        Text(body)
    }
}

fun Context.openSafely(intent: Intent) = runCatching { startActivity(intent) }
