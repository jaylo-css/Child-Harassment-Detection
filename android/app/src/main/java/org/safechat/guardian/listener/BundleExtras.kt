package org.safechat.guardian.listener

import android.app.Person
import android.os.Build
import android.os.Bundle
import android.os.Parcelable
import androidx.core.os.BundleCompat
import org.safechat.guardian.core.Keys
import org.safechat.guardian.core.MapExtras

/** Copies the framework notification extras into [MapExtras] with the same keys and nesting. */
object BundleExtras {
    private val TEXT_KEYS = listOf(
        Keys.TITLE, Keys.TEXT, Keys.BIG_TEXT, Keys.SUMMARY_TEXT, Keys.TEMPLATE, Keys.CONVERSATION_TITLE, Keys.SELF_NAME,
    )

    fun from(extras: Bundle?): MapExtras {
        if (extras == null) return MapExtras(emptyMap())
        val map = mutableMapOf<String, Any?>()
        for (key in TEXT_KEYS) {
            extras.getCharSequence(key)?.let { map[key] = it.toString() }
        }
        map[Keys.IS_GROUP] = extras.getBoolean(Keys.IS_GROUP, false)
        person(extras, Keys.MESSAGING_USER)?.let { map[Keys.MESSAGING_USER] = it }
        val messages: Array<Parcelable>? = BundleCompat.getParcelableArray(extras, Keys.MESSAGES, Bundle::class.java)
        if (messages != null) {
            map[Keys.MESSAGES] = messages.mapNotNull { (it as? Bundle)?.let(::message) }
        }
        return MapExtras(map)
    }

    private fun message(b: Bundle): Map<String, Any?> = mapOf(
        Keys.MSG_TEXT to b.getCharSequence(Keys.MSG_TEXT)?.toString(),
        Keys.MSG_TIME to b.getLong(Keys.MSG_TIME),
        Keys.MSG_SENDER to b.getCharSequence(Keys.MSG_SENDER)?.toString(),
        Keys.MSG_SENDER_PERSON to person(b, Keys.MSG_SENDER_PERSON),
    )

    private fun person(b: Bundle, key: String): Map<String, String?>? {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.P) return null
        val p = BundleCompat.getParcelable(b, key, Person::class.java) ?: return null
        return mapOf(Keys.PERSON_NAME to p.name?.toString(), Keys.PERSON_KEY to p.key)
    }
}
