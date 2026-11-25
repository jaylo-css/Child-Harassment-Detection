package org.safechat.guardian.core

/**
 * Notification extras as plain data.
 *
 * The Android adapter (`listener/BundleExtras.kt`) copies the framework `Bundle` into a map
 * using the SAME keys and nesting the framework uses:
 *  - "android.messages": list of message maps with "text", "time", "sender", "sender_person"
 *  - "sender_person" / "android.messagingUser": person maps with "name" and "key"
 * so JVM unit tests can build "fake Bundle extras" with identical structure.
 */
object Keys {
    const val TITLE = "android.title"
    const val TEXT = "android.text"
    const val BIG_TEXT = "android.bigText"
    const val SUMMARY_TEXT = "android.summaryText"
    const val TEMPLATE = "android.template"
    const val CONVERSATION_TITLE = "android.conversationTitle"
    const val IS_GROUP = "android.isGroupConversation"
    const val MESSAGES = "android.messages"
    const val SELF_NAME = "android.selfDisplayName"
    const val MESSAGING_USER = "android.messagingUser"

    // keys inside each message bundle
    const val MSG_TEXT = "text"
    const val MSG_TIME = "time"
    const val MSG_SENDER = "sender"
    const val MSG_SENDER_PERSON = "sender_person"

    // keys inside a person map
    const val PERSON_NAME = "name"
    const val PERSON_KEY = "key"
}

data class StyleMessage(val text: String?, val time: Long, val sender: String?, val senderKey: String?)

data class NotificationSnapshot(
    val packageName: String,
    val postTime: Long,
    val whenTime: Long,
    val isGroupSummary: Boolean,
    val isOngoing: Boolean,
    val category: String?,
    val channelId: String?,
    val extras: MapExtras,
)

class MapExtras(private val map: Map<String, Any?>) {
    fun text(key: String): String? = (map[key] as? CharSequence)?.toString()?.takeIf { it.isNotBlank() }

    fun bool(key: String): Boolean = map[key] as? Boolean ?: false

    fun messages(): List<StyleMessage>? {
        val raw = map[Keys.MESSAGES] as? List<*> ?: return null
        return raw.mapNotNull { item ->
            val m = item as? Map<*, *> ?: return@mapNotNull null
            val person = m[Keys.MSG_SENDER_PERSON] as? Map<*, *>
            StyleMessage(
                text = (m[Keys.MSG_TEXT] as? CharSequence)?.toString(),
                time = (m[Keys.MSG_TIME] as? Number)?.toLong() ?: 0L,
                sender = (person?.get(Keys.PERSON_NAME) as? CharSequence)?.toString()
                    ?: (m[Keys.MSG_SENDER] as? CharSequence)?.toString(),
                senderKey = person?.get(Keys.PERSON_KEY) as? String,
            )
        }
    }

    fun selfName(): String? =
        ((map[Keys.MESSAGING_USER] as? Map<*, *>)?.get(Keys.PERSON_NAME) as? CharSequence)?.toString()
            ?: text(Keys.SELF_NAME)

    fun selfKey(): String? = (map[Keys.MESSAGING_USER] as? Map<*, *>)?.get(Keys.PERSON_KEY) as? String
}
