package org.safechat.guardian.ui

import android.Manifest
import android.content.pm.PackageManager
import android.os.Build
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.annotation.OptIn
import androidx.camera.core.CameraSelector
import androidx.camera.core.ExperimentalGetImage
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.ImageProxy
import androidx.camera.core.Preview
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.core.content.ContextCompat
import androidx.lifecycle.compose.LocalLifecycleOwner
import com.google.mlkit.vision.barcode.BarcodeScannerOptions
import com.google.mlkit.vision.barcode.BarcodeScanning
import com.google.mlkit.vision.barcode.common.Barcode
import com.google.mlkit.vision.common.InputImage
import kotlinx.coroutines.launch
import org.safechat.guardian.BuildConfig
import org.safechat.guardian.Graph
import org.safechat.guardian.core.PairingPayload
import org.safechat.guardian.core.UrlPolicy
import org.safechat.guardian.listener.StatusNotifier
import org.safechat.guardian.net.FlushWorker
import java.util.concurrent.atomic.AtomicBoolean

@Composable
fun PairingScreen(graph: Graph, onBack: () -> Unit) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    var server by remember { mutableStateOf(graph.store.serverUrl ?: "") }
    var code by remember { mutableStateOf("") }
    var status by remember { mutableStateOf<String?>(null) }
    var busy by remember { mutableStateOf(false) }
    var scanning by remember { mutableStateOf(false) }
    var hasCamera by remember {
        mutableStateOf(ContextCompat.checkSelfPermission(context, Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED)
    }
    val cameraPermission = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        hasCamera = granted
        scanning = granted
    }

    fun pair(rawServer: String, rawCode: String) {
        busy = true
        status = "Pairing…"
        scope.launch {
            try {
                val base = UrlPolicy.normalizeServer(rawServer, allowLanCleartext = BuildConfig.DEBUG)
                val pairingCode = PairingPayload.normalizeCode(rawCode)
                val res = graph.pairing.claim(base, pairingCode, "${Build.MANUFACTURER} ${Build.MODEL}")
                UrlPolicy.normalizeServer(res.wsUrl.substringBefore("/ws/"), allowLanCleartext = BuildConfig.DEBUG)
                graph.store.serverUrl = base
                graph.store.wsUrl = res.wsUrl
                graph.store.deviceId = res.deviceId
                graph.store.deviceToken = res.token
                FlushWorker.schedulePeriodic(context)
                graph.scheduleFlush()
                StatusNotifier.showProtectionOn(context)
                status = "Paired. This phone now appears on the dashboard's Devices page."
            } catch (e: Exception) {
                status = "Could not pair: ${e.message ?: e.javaClass.simpleName}"
            } finally {
                busy = false
            }
        }
    }

    Page("Pair with dashboard", onBack) {
        Text("On the guardian dashboard open Devices → Pair a phone, then scan the QR code.")
        if (scanning && hasCamera) {
            QrScanner(onCode = { raw ->
                scanning = false
                try {
                    val p = PairingPayload.parse(raw)
                    server = p.server
                    code = p.code
                    pair(p.server, p.code)
                } catch (e: IllegalArgumentException) {
                    status = e.message
                }
            })
            OutlinedButton(onClick = { scanning = false }) { Text("Stop scanning") }
        } else {
            Button(
                onClick = { if (hasCamera) scanning = true else cameraPermission.launch(Manifest.permission.CAMERA) },
                enabled = !busy,
                modifier = Modifier.fillMaxWidth(),
            ) { Text("Scan QR code") }
        }
        Text("Or enter the details shown under the QR code:", style = MaterialTheme.typography.bodyMedium)
        OutlinedTextField(server, { server = it }, label = { Text("Server address (https://…)") }, singleLine = true,
            modifier = Modifier.fillMaxWidth())
        OutlinedTextField(code, { code = it }, label = { Text("Pairing code (ABCD-EF23)") }, singleLine = true,
            modifier = Modifier.fillMaxWidth())
        Button(onClick = { pair(server, code) }, enabled = !busy && server.isNotBlank() && code.isNotBlank(),
            modifier = Modifier.fillMaxWidth()) { Text("Pair") }
        if (BuildConfig.DEBUG) {
            Text("Debug build: http:// is allowed for LAN addresses (e.g. http://192.168.1.20:8000).",
                style = MaterialTheme.typography.bodySmall)
        }
        status?.let { Text(it) }
    }
}

@Composable
fun QrScanner(onCode: (String) -> Unit) {
    val context = LocalContext.current
    val lifecycleOwner = LocalLifecycleOwner.current
    val delivered = remember { AtomicBoolean(false) }
    val scanner = remember {
        BarcodeScanning.getClient(BarcodeScannerOptions.Builder().setBarcodeFormats(Barcode.FORMAT_QR_CODE).build())
    }
    val providerFuture = remember { ProcessCameraProvider.getInstance(context) }
    AndroidView(
        modifier = Modifier.fillMaxWidth().height(300.dp),
        factory = { ctx ->
            val previewView = PreviewView(ctx)
            providerFuture.addListener({
                val provider = providerFuture.get()
                val preview = Preview.Builder().build().also { it.setSurfaceProvider(previewView.surfaceProvider) }
                val analysis = ImageAnalysis.Builder()
                    .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
                    .build()
                analysis.setAnalyzer(ContextCompat.getMainExecutor(ctx), QrAnalyzer(scanner) { raw ->
                    if (delivered.compareAndSet(false, true)) onCode(raw)
                })
                provider.unbindAll()
                provider.bindToLifecycle(lifecycleOwner, CameraSelector.DEFAULT_BACK_CAMERA, preview, analysis)
            }, ContextCompat.getMainExecutor(ctx))
            previewView
        },
    )
    DisposableEffect(Unit) {
        onDispose {
            runCatching { providerFuture.get().unbindAll() }
            scanner.close()
        }
    }
}

private class QrAnalyzer(
    private val scanner: com.google.mlkit.vision.barcode.BarcodeScanner,
    private val onRaw: (String) -> Unit,
) : ImageAnalysis.Analyzer {
    @OptIn(ExperimentalGetImage::class)
    override fun analyze(proxy: ImageProxy) {
        val media = proxy.image
        if (media == null) {
            proxy.close()
            return
        }
        val input = InputImage.fromMediaImage(media, proxy.imageInfo.rotationDegrees)
        scanner.process(input)
            .addOnSuccessListener { codes -> codes.firstNotNullOfOrNull { it.rawValue }?.let(onRaw) }
            .addOnCompleteListener { proxy.close() }
    }
}
