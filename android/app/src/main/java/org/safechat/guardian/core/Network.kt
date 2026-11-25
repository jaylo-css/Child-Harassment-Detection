package org.safechat.guardian.core

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import java.net.URI

/** HTTPS/WSS only. Debug builds may additionally use plain HTTP/WS to a private LAN address
 *  (or the emulator host 10.0.2.2) for development. */
object UrlPolicy {
    fun normalizeServer(raw: String, allowLanCleartext: Boolean): String {
        val uri = try {
            URI(raw.trim().trimEnd('/'))
        } catch (e: Exception) {
            throw IllegalArgumentException("Not a valid server address")
        }
        val scheme = uri.scheme?.lowercase() ?: throw IllegalArgumentException("Server address needs https://")
        val host = uri.host ?: throw IllegalArgumentException("Server address has no host")
        when (scheme) {
            "https", "wss" -> Unit
            "http", "ws" -> require(allowLanCleartext && isPrivateHost(host)) {
                "Only https:// servers are allowed" + if (allowLanCleartext) " (http:// only for LAN addresses)" else ""
            }
            else -> throw IllegalArgumentException("Unsupported scheme: $scheme")
        }
        return raw.trim().trimEnd('/')
    }

    fun webSocketUrl(server: String): String {
        val base = server.trimEnd('/')
        val ws = when {
            base.startsWith("https://") -> "wss://" + base.removePrefix("https://")
            base.startsWith("http://") -> "ws://" + base.removePrefix("http://")
            else -> base
        }
        return "$ws/ws/ingest"
    }

    fun isPrivateHost(host: String): Boolean {
        val h = host.lowercase()
        if (h == "localhost" || h == "10.0.2.2" || h.endsWith(".local")) return true
        val parts = h.split(".").mapNotNull { it.toIntOrNull() }
        if (parts.size != 4) return false
        return parts[0] == 10 ||
            (parts[0] == 192 && parts[1] == 168) ||
            (parts[0] == 172 && parts[1] in 16..31) ||
            parts[0] == 127
    }
}

@Serializable
data class PairingPayload(val v: Int = 1, val server: String, val code: String) {
    companion object {
        private val json = Json { ignoreUnknownKeys = true }
        private val CODE = Regex("""^[A-Z2-9]{4}-[A-Z2-9]{4}$""")

        fun parse(raw: String): PairingPayload {
            val p = try {
                json.decodeFromString(serializer(), raw)
            } catch (e: Exception) {
                throw IllegalArgumentException("This QR code is not a SafeChat Guardian pairing code")
            }
            require(p.v == 1) { "Unsupported pairing version" }
            return p.copy(code = normalizeCode(p.code))
        }

        fun normalizeCode(code: String): String {
            val c = code.trim().uppercase().replace(" ", "")
            val withDash = if (c.length == 8 && !c.contains('-')) c.substring(0, 4) + "-" + c.substring(4) else c
            require(CODE.matches(withDash)) { "Pairing codes look like ABCD-EF23" }
            return withDash
        }
    }
}

/** WebSocket frames for /ws/ingest (see backend app/main.py). */
object Frames {
    private val json = Json { ignoreUnknownKeys = true; encodeDefaults = true }

    data class Incoming(val type: String?, val eventId: String?, val retry: Boolean)

    fun event(e: WireEvent): String = buildJsonObject {
        put("type", "event")
        put("event", json.encodeToJsonElement(WireEvent.serializer(), e))
    }.toString()

    fun ping(): String = """{"type":"ping"}"""

    fun parse(text: String): Incoming = try {
        val o: JsonObject = json.parseToJsonElement(text).jsonObject
        Incoming(
            type = o["type"]?.jsonPrimitive?.contentOrNull,
            eventId = o["event_id"]?.jsonPrimitive?.contentOrNull,
            retry = o["retry"]?.jsonPrimitive?.contentOrNull == "true",
        )
    } catch (e: Exception) {
        Incoming(null, null, false)
    }
}
