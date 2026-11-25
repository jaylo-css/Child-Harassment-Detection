package org.safechat.guardian.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** Fake extras mirror the framework Bundle layout (see core/Extras.kt). */
class NotificationParserTest {
    private val parser = NotificationParser()

    private fun person(name: String, key: String? = null) = mapOf(Keys.PERSON_NAME to name, Keys.PERSON_KEY to key)

    private fun msg(text: String, time: Long, sender: String? = null, person: Map<String, String?>? = null) =
        mapOf(Keys.MSG_TEXT to text, Keys.MSG_TIME to time, Keys.MSG_SENDER to sender, Keys.MSG_SENDER_PERSON to person)

    private fun snapshot(
        extras: Map<String, Any?>,
        pkg: String = "com.discord",
        groupSummary: Boolean = false,
        ongoing: Boolean = false,
        category: String? = "msg",
    ) = NotificationSnapshot(pkg, 2000L, 1500L, groupSummary, ongoing, category, "chat", MapExtras(extras))

    @Test
    fun messagingStyleWithPersons() {
        val extras = mapOf(
            Keys.TEMPLATE to "android.app.Notification\$MessagingStyle",
            Keys.MESSAGING_USER to person("Me", "me"),
            Keys.CONVERSATION_TITLE to "Bedwars squad",
            Keys.MESSAGES to listOf(
                msg("hey wanna play", 100L, person = person("Stranger", "s1")),
                msg("sure", 200L, person = null), // null sender = the device user (child)
                msg("add me on snap", 300L, person = person("Stranger", "s1")),
            ),
        )
        val out = parser.parse(snapshot(extras))
        assertEquals(3, out.size)
        assertEquals(listOf(false, true, false), out.map { it.fromSelf })
        assertEquals("Bedwars squad", out[0].conversation)
        assertEquals("Stranger", out[0].sender)
        assertEquals(SELF_SENDER, out[1].sender)
        assertEquals(300L, out[2].timestampMs)
    }

    @Test
    fun selfDetectedByUserKeyOrLegacySelfName() {
        val byKey = mapOf(
            Keys.MESSAGING_USER to person("Kid", "me"),
            Keys.MESSAGES to listOf(msg("hi", 1L, person = person("Kid", "me"))),
        )
        assertTrue(parser.parse(snapshot(byKey)).single().fromSelf)

        val legacy = mapOf(
            Keys.SELF_NAME to "Kid",
            Keys.TITLE to "Stranger",
            Keys.MESSAGES to listOf(msg("hi", 1L, sender = "Kid"), msg("yo", 2L, sender = "Stranger")),
        )
        val out = parser.parse(snapshot(legacy))
        assertEquals(listOf(true, false), out.map { it.fromSelf })
        assertEquals("Stranger", out[0].conversation) // falls back to title
    }

    @Test
    fun plainStyleFallbackPrefersBigText() {
        val extras = mapOf(Keys.TITLE to "Stranger", Keys.TEXT to "short…", Keys.BIG_TEXT to "the full message text")
        val m = parser.parse(snapshot(extras)).single()
        assertEquals("the full message text", m.text)
        assertEquals("Stranger", m.sender)
        assertFalse(m.fromSelf)
        assertEquals(1500L, m.timestampMs) // notification `when`
    }

    @Test
    fun plainStyleGroupTitles() {
        assertEquals("Squad" to "Stranger", NotificationParser.splitTitle(null, "Stranger @ Squad"))
        assertEquals("Squad" to "Stranger", NotificationParser.splitTitle(null, "Squad: Stranger"))
        assertEquals("Chat" to "Stranger", NotificationParser.splitTitle("Chat", "Stranger"))
    }

    @Test
    fun ignoresSummariesOngoingAndNoise() {
        val extras = mapOf(Keys.TITLE to "Stranger", Keys.TEXT to "hello")
        assertTrue(parser.parse(snapshot(extras, groupSummary = true)).isEmpty())
        assertTrue(parser.parse(snapshot(extras, ongoing = true)).isEmpty())
        assertTrue(parser.parse(snapshot(extras, category = "call")).isEmpty())
        for (noise in listOf("5 new messages", "3 messages from 2 chats", "New messages", "Checking for new messages")) {
            assertTrue(noise, parser.parse(snapshot(mapOf(Keys.TITLE to "Discord", Keys.TEXT to noise))).isEmpty())
        }
        assertTrue(parser.parse(snapshot(mapOf(Keys.TITLE to "Stranger"))).isEmpty()) // no text
        val styleNoise = mapOf(Keys.MESSAGES to listOf(msg("2 new messages", 1L, sender = "X"), msg("  ", 2L, sender = "X")))
        assertTrue(parser.parse(snapshot(styleNoise)).isEmpty())
    }

    @Test
    fun truncatesLongMessages() {
        val long = "a".repeat(MAX_TEXT_CHARS + 50)
        val m = parser.parse(snapshot(mapOf(Keys.TITLE to "S", Keys.TEXT to long))).single()
        assertEquals(MAX_TEXT_CHARS, m.text.length)
    }
}
