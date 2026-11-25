package org.safechat.guardian.core

import java.security.MessageDigest
import java.security.SecureRandom
import java.time.Instant
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec

/** Salted HMAC-SHA256 for sender names and conversation IDs; the salt is a per-install secret. */
class Hasher(private val salt: ByteArray) {
    init {
        require(salt.size >= 16) { "salt too short" }
    }

    fun hash(value: String): String {
        val mac = Mac.getInstance("HmacSHA256")
        mac.init(SecretKeySpec(salt, "HmacSHA256"))
        return mac.doFinal(value.trim().lowercase().toByteArray(Charsets.UTF_8)).toHex().take(32)
    }

    companion object {
        fun newSalt(): ByteArray = ByteArray(32).also { SecureRandom().nextBytes(it) }
    }
}

fun sha256Hex(value: String): String =
    MessageDigest.getInstance("SHA-256").digest(value.toByteArray(Charsets.UTF_8)).toHex()

fun ByteArray.toHex(): String = joinToString("") { "%02x".format(it) }

/**
 * Chat apps re-post the same notification (with the full message history) as new messages stack
 * up. Keep a short LRU of hashes of package + conversation + sender + text + timestamp.
 */
class Deduplicator(private val capacity: Int = 500) {
    private val seen = object : LinkedHashMap<String, Boolean>(capacity, 0.75f, true) {
        override fun removeEldestEntry(eldest: MutableMap.MutableEntry<String, Boolean>?): Boolean = size > capacity
    }

    @Synchronized
    fun firstTime(m: ParsedMessage): Boolean {
        val k = key(m)
        if (seen.containsKey(k)) {
            seen[k] // refresh recency
            return false
        }
        seen[k] = true
        return true
    }

    @Synchronized
    fun size(): Int = seen.size

    companion object {
        fun key(m: ParsedMessage): String =
            sha256Hex(listOf(m.packageName, m.conversation, m.sender, m.text, m.timestampMs.toString()).joinToString("\u0000"))
    }
}

/** Converts a parsed message to the wire event. Names and conversation IDs are hashed here,
 *  on the phone, before anything leaves it. */
class EventMapper(private val hasher: Hasher) {
    fun toWire(m: ParsedMessage, platform: String): WireEvent = WireEvent(
        platform = platform.take(64),
        conversationId = hasher.hash("${m.packageName}|${m.conversation}"),
        senderHash = hasher.hash("${m.packageName}|${m.sender}"),
        isChild = m.fromSelf,
        text = m.text.take(MAX_TEXT_CHARS),
        timestamp = Instant.ofEpochMilli(m.timestampMs).toString(),
        eventId = Deduplicator.key(m).take(32),
    )
}
