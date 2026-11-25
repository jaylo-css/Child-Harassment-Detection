package org.safechat.guardian.net

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.safechat.guardian.core.EventTransport
import org.safechat.guardian.core.Frames
import org.safechat.guardian.core.WireEvent
import java.io.IOException

/** Sends a batch over the authenticated WebSocket /ws/ingest and collects acks. */
class WebSocketTransport(
    private val client: OkHttpClient,
    private val wsUrl: String,
    private val token: String,
    private val timeoutMs: Long = 20_000,
) : EventTransport {

    override suspend fun sendAll(events: List<WireEvent>): Set<String> {
        if (events.isEmpty()) return emptySet()
        val incoming = Channel<String>(Channel.UNLIMITED)
        val request = Request.Builder().url(wsUrl).header("Authorization", "Bearer $token").build()
        val socket = client.newWebSocket(request, object : WebSocketListener() {
            override fun onMessage(webSocket: WebSocket, text: String) {
                incoming.trySend(text)
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                incoming.close()
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                incoming.close()
            }
        })
        val acked = mutableSetOf<String>()
        try {
            withTimeoutOrNull(timeoutMs) {
                var ready = false
                while (!ready) {
                    val f = Frames.parse(incoming.receiveCatching().getOrNull() ?: return@withTimeoutOrNull)
                    if (f.type == "error") return@withTimeoutOrNull
                    ready = f.type == "ready"
                }
                events.forEach { socket.send(Frames.event(it)) }
                var answered = 0
                while (answered < events.size) {
                    val f = Frames.parse(incoming.receiveCatching().getOrNull() ?: return@withTimeoutOrNull)
                    when (f.type) {
                        "ack" -> {
                            f.eventId?.let { acked += it }
                            answered++
                        }
                        "nack" -> answered++
                    }
                }
            }
        } finally {
            socket.close(1000, "batch done")
        }
        return acked
    }
}

class PairingClient(private val client: OkHttpClient) {
    @Serializable
    data class ClaimResponse(
        @SerialName("device_id") val deviceId: String,
        val token: String,
        @SerialName("ws_url") val wsUrl: String,
    )

    private val json = Json { ignoreUnknownKeys = true }

    suspend fun claim(server: String, code: String, deviceName: String): ClaimResponse = withContext(Dispatchers.IO) {
        val body = buildJsonObject {
            put("code", code)
            put("name", deviceName.take(64))
            put("platform", "android")
        }.toString().toRequestBody("application/json".toMediaType())
        val req = Request.Builder().url("$server/api/devices/claim").post(body).build()
        client.newCall(req).execute().use { r ->
            if (!r.isSuccessful) throw IOException(if (r.code == 400) "Code invalid or expired" else "Pairing failed (${r.code})")
            json.decodeFromString(ClaimResponse.serializer(), r.body?.string().orEmpty())
        }
    }

    suspend fun heartbeat(server: String, token: String): Boolean = withContext(Dispatchers.IO) {
        val req = Request.Builder()
            .url("$server/api/devices/heartbeat")
            .header("Authorization", "Bearer $token")
            .post(ByteArray(0).toRequestBody(null))
            .build()
        runCatching { client.newCall(req).execute().use { it.isSuccessful } }.getOrDefault(false)
    }
}
