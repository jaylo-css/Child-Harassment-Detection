package org.safechat.guardian.core

/**
 * Turns a chat notification into messages. Read-only: it never acts on the notification.
 *
 * 1. MessagingStyle first (EXTRA_MESSAGES, per-message sender, conversation title, timestamps).
 *    A message is from the child only when it is from the device's own user: no sender at all
 *    (the framework convention), or a sender matching the MessagingStyle user (key or name).
 * 2. Otherwise fall back to EXTRA_TITLE / EXTRA_BIG_TEXT / EXTRA_TEXT; such messages are always
 *    treated as from the other party.
 * Summary, group-summary, ongoing (calls, media) and "N new messages" notifications are ignored.
 */
class NotificationParser {

    fun parse(n: NotificationSnapshot): List<ParsedMessage> {
        if (n.isGroupSummary || n.isOngoing) return emptyList()
        if (n.category in IGNORED_CATEGORIES) return emptyList()
        val ex = n.extras
        val title = ex.text(Keys.TITLE)
        val conversationTitle = ex.text(Keys.CONVERSATION_TITLE)

        val styleMessages = ex.messages()
        if (!styleMessages.isNullOrEmpty()) {
            val selfName = ex.selfName()
            val selfKey = ex.selfKey()
            val conversation = conversationTitle ?: title ?: n.packageName
            return styleMessages.mapNotNull { m ->
                val text = m.text?.trim()
                if (text.isNullOrEmpty() || isNoise(text)) return@mapNotNull null
                val fromSelf = (m.sender == null && m.senderKey == null) ||
                    (selfKey != null && m.senderKey == selfKey) ||
                    (selfKey == null && selfName != null && m.sender == selfName)
                ParsedMessage(
                    packageName = n.packageName,
                    conversation = conversation,
                    sender = if (fromSelf) SELF_SENDER else m.sender ?: "unknown",
                    text = text.take(MAX_TEXT_CHARS),
                    timestampMs = if (m.time > 0) m.time else fallbackTime(n),
                    fromSelf = fromSelf,
                )
            }
        }

        val text = (ex.text(Keys.BIG_TEXT) ?: ex.text(Keys.TEXT))?.trim()
        if (text.isNullOrEmpty() || title.isNullOrBlank() || isNoise(text) || isNoise(title)) return emptyList()
        val (conversation, sender) = splitTitle(conversationTitle, title)
        return listOf(
            ParsedMessage(
                packageName = n.packageName,
                conversation = conversation,
                sender = sender,
                text = text.take(MAX_TEXT_CHARS),
                timestampMs = fallbackTime(n),
                fromSelf = false,
            )
        )
    }

    private fun fallbackTime(n: NotificationSnapshot) = if (n.whenTime > 0) n.whenTime else n.postTime

    companion object {
        private val IGNORED_CATEGORIES = setOf("call", "transport", "progress", "alarm", "service", "sys")

        private val NOISE = listOf(
            Regex("""^\d+\s+new\s+(messages?|notifications?|chats?)\b.*$""", RegexOption.IGNORE_CASE),
            Regex("""^\d+\s+messages?\s+from\s+\d+\s+(chats?|conversations?)$""", RegexOption.IGNORE_CASE),
            Regex("""^(you have )?new messages?$""", RegexOption.IGNORE_CASE),
            Regex("""^checking for new messages.*$""", RegexOption.IGNORE_CASE),
            Regex("""^(\d+\s+)?unread (messages?|chats?)$""", RegexOption.IGNORE_CASE),
        )

        fun isNoise(text: String): Boolean = NOISE.any { it.matches(text.trim()) }

        /** Plain-style titles: "Sender", "Sender @ Group" or "Group: Sender". */
        fun splitTitle(conversationTitle: String?, title: String): Pair<String, String> {
            if (title.contains(" @ ")) {
                val (sender, group) = title.split(" @ ", limit = 2)
                return group.trim() to sender.trim()
            }
            if (conversationTitle != null) return conversationTitle to title
            val colon = title.indexOf(": ")
            if (colon > 0) return title.substring(0, colon).trim() to title.substring(colon + 2).trim()
            return title to title
        }
    }
}
