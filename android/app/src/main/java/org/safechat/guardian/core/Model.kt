package org.safechat.guardian.core

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/** One chat message extracted from a notification (before hashing). Never leaves the phone as-is. */
data class ParsedMessage(
    val packageName: String,
    val conversation: String,
    val sender: String,
    val text: String,
    val timestampMs: Long,
    val fromSelf: Boolean,
)

/** Wire format of a MessageEvent, matching the backend's `MessageEvent` model. */
@Serializable
data class WireEvent(
    val platform: String,
    @SerialName("conversation_id") val conversationId: String,
    @SerialName("sender_hash") val senderHash: String,
    @SerialName("is_child") val isChild: Boolean,
    val text: String,
    val timestamp: String,
    @SerialName("event_id") val eventId: String,
)

const val SELF_SENDER = "__self__"
const val MAX_TEXT_CHARS = 4000

/** Notification channel used by the debug-only "Simulate chat" tool. */
const val SIM_CHANNEL_ID = "debug_simulated_chat"
const val SIM_PLATFORM_LABEL = "Simulator (debug)"
