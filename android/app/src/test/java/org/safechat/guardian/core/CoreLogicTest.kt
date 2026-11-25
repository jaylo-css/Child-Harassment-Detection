package org.safechat.guardian.core

import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

class DedupTest {
    private fun m(text: String, ts: Long = 1L) = ParsedMessage("com.discord", "chat", "Stranger", text, ts, false)

    @Test
    fun repostedMessagesAreDeduplicated() {
        val d = Deduplicator(capacity = 3)
        assertTrue(d.firstTime(m("a")))
        assertFalse(d.firstTime(m("a")))
        assertTrue(d.firstTime(m("a", ts = 2L))) // same text, new message
        assertTrue(d.firstTime(m("b")))
    }

    @Test
    fun lruEvictsOldest() {
        val d = Deduplicator(capacity = 2)
        d.firstTime(m("a")); d.firstTime(m("b")); d.firstTime(m("c"))
        assertEquals(2, d.size())
        assertTrue(d.firstTime(m("a"))) // evicted, so seen again as new
    }
}

class AllowlistTest {
    private val json = """
        {"version":1,"apps":[
          {"packageName":"com.discord","label":"Discord"},
          {"packageName":"com.whatsapp","label":"WhatsApp","enabledByDefault":false},
          {"packageName":"com.discord","label":"dup"}
        ],"extra":"ignored"}
    """.trimIndent()

    @Test
    fun filtersByPackageAndToggles() {
        val a = Allowlist.parse(json)
        assertEquals(2, a.apps.size)
        assertTrue(a.isAllowed("com.discord"))
        assertFalse(a.isAllowed("com.whatsapp")) // disabled by default
        assertTrue(a.isAllowed("com.whatsapp", setOf("com.whatsapp")))
        assertFalse(a.isAllowed("com.discord", setOf("com.whatsapp")))
        assertFalse(a.isAllowed("com.mybank.app"))
        assertFalse(a.isAllowed("com.google.android.gm"))
        assertEquals("Discord", a.label("com.discord"))
        assertEquals(setOf("com.discord"), a.defaults())
    }

    @Test
    fun rejectsInvalidPackages() {
        assertThrows(IllegalArgumentException::class.java) {
            Allowlist.parse("""{"apps":[{"packageName":"not a package","label":"x"}]}""")
        }
    }
}

class HashingTest {
    private val salt = ByteArray(32) { it.toByte() }

    @Test
    fun saltedStableAndNormalized() {
        val h = Hasher(salt)
        assertEquals(h.hash("Stranger"), h.hash("  stranger "))
        assertEquals(32, h.hash("x").length)
        assertNotEquals(h.hash("Stranger"), Hasher(ByteArray(32) { 7 }).hash("Stranger"))
        assertFalse(h.hash("Stranger").contains("tranger"))
    }

    @Test
    fun mapperHashesIdsAndMarksChild() {
        val mapper = EventMapper(Hasher(salt))
        val e = mapper.toWire(ParsedMessage("com.discord", "Squad", SELF_SENDER, "hi", 1_700_000_000_000L, true), "Discord")
        assertTrue(e.isChild)
        assertFalse(e.conversationId.contains("Squad"))
        assertEquals("2023-11-14T22:13:20Z", e.timestamp)
        assertEquals(32, e.eventId.length)
        assertThrows(IllegalArgumentException::class.java) { Hasher(ByteArray(4)) }
    }
}

class OfflineQueueTest {
    private fun ev(id: String) = WireEvent("Discord", "c", "s", false, "t", "2024-01-01T00:00:00Z", id)

    @Test
    fun deletesOnlyAcknowledgedEvents() = runTest {
        val q = InMemoryEventQueue()
        (1..5).forEach { q.enqueue(ev("e$it")) }
        q.enqueue(ev("e1")) // duplicate id ignored
        val flusher = QueueFlusher(q, { batch -> batch.map { it.eventId }.filter { it != "e3" }.toSet() }, batchSize = 10)
        val r = flusher.flush()
        assertEquals(4, r.sent)
        assertEquals(1, q.count())
        assertEquals("e3", q.peek(10).single().eventId)
    }

    @Test
    fun offlineKeepsEverythingAndReportsStall() = runTest {
        val q = InMemoryEventQueue()
        (1..3).forEach { q.enqueue(ev("e$it")) }
        val r = QueueFlusher(q, { throw java.io.IOException("offline") }).flush()
        assertEquals(0, r.sent)
        assertTrue(r.stalled)
        assertEquals(3, q.count())
    }

    @Test
    fun drainsInBatches() = runTest {
        val q = InMemoryEventQueue()
        (1..250).forEach { q.enqueue(ev("e$it")) }
        var calls = 0
        val r = QueueFlusher(q, { batch -> calls++; batch.map { it.eventId }.toSet() }, batchSize = 100).flush()
        assertEquals(250, r.sent)
        assertEquals(0, r.remaining)
        assertEquals(3, calls)
    }

    @Test
    fun queueIsCapped() = runTest {
        val q = InMemoryEventQueue(maxSize = 3)
        (1..5).forEach { q.enqueue(ev("e$it")) }
        assertEquals(listOf("e3", "e4", "e5"), q.peek(10).map { it.eventId })
    }
}

class NetworkPolicyTest {
    @Test
    fun httpsAlwaysOkCleartextOnlyForLanInDebug() {
        assertEquals("https://guardian.example.org", UrlPolicy.normalizeServer("https://guardian.example.org/", false))
        assertThrows(IllegalArgumentException::class.java) { UrlPolicy.normalizeServer("http://192.168.1.20:8000", false) }
        assertEquals("http://192.168.1.20:8000", UrlPolicy.normalizeServer("http://192.168.1.20:8000", true))
        assertThrows(IllegalArgumentException::class.java) { UrlPolicy.normalizeServer("http://example.org", true) }
        assertThrows(IllegalArgumentException::class.java) { UrlPolicy.normalizeServer("ftp://x.org", true) }
        assertEquals("wss://a.org/ws/ingest", UrlPolicy.webSocketUrl("https://a.org"))
        assertEquals("ws://10.0.2.2:8000/ws/ingest", UrlPolicy.webSocketUrl("http://10.0.2.2:8000/"))
        assertTrue(UrlPolicy.isPrivateHost("172.20.1.1"))
        assertFalse(UrlPolicy.isPrivateHost("172.40.1.1"))
    }

    @Test
    fun pairingPayloadParsing() {
        val p = PairingPayload.parse("""{"v":1,"server":"https://a.org","code":"abcd-ef23","x":1}""")
        assertEquals("ABCD-EF23", p.code)
        assertEquals("ABCD-EF23", PairingPayload.normalizeCode("abcdef23"))
        assertThrows(IllegalArgumentException::class.java) { PairingPayload.parse("hello") }
        assertThrows(IllegalArgumentException::class.java) { PairingPayload.normalizeCode("ABCD-EF21") } // 0/1 are never used in codes
    }

    @Test
    fun framesRoundTrip() {
        val frame = Frames.event(WireEvent("Discord", "c", "s", true, "hi", "2024-01-01T00:00:00Z", "e1"))
        assertTrue(frame.contains("\"type\":\"event\"") && frame.contains("\"is_child\":true") && frame.contains("\"event_id\":\"e1\""))
        val ack = Frames.parse("""{"type":"ack","event_id":"e1","conversation_key":"k"}""")
        assertEquals("ack", ack.type)
        assertEquals("e1", ack.eventId)
        assertEquals(null, Frames.parse("not json").type)
    }
}
