package org.safechat.guardian.data

import android.content.Context
import android.content.SharedPreferences
import android.util.Base64
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey
import org.safechat.guardian.core.Hasher

/** Secrets and settings in EncryptedSharedPreferences (keys protected by the Android Keystore):
 *  device token, server URL, per-install hashing salt, database passphrase, consent flags. */
class SecureStore(context: Context) {
    private val prefs: SharedPreferences = EncryptedSharedPreferences.create(
        context,
        "guardian_secure_prefs",
        MasterKey.Builder(context).setKeyScheme(MasterKey.KeyScheme.AES256_GCM).build(),
        EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
        EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
    )

    var deviceToken: String?
        get() = prefs.getString(K_TOKEN, null)
        set(v) = prefs.edit().putString(K_TOKEN, v).apply()

    var deviceId: String?
        get() = prefs.getString(K_DEVICE_ID, null)
        set(v) = prefs.edit().putString(K_DEVICE_ID, v).apply()

    var serverUrl: String?
        get() = prefs.getString(K_SERVER, null)
        set(v) = prefs.edit().putString(K_SERVER, v).apply()

    var wsUrl: String?
        get() = prefs.getString(K_WS, null)
        set(v) = prefs.edit().putString(K_WS, v).apply()

    var guardianConsent: Boolean
        get() = prefs.getBoolean(K_CONSENT, false)
        set(v) = prefs.edit().putBoolean(K_CONSENT, v).apply()

    var childNoticeShown: Boolean
        get() = prefs.getBoolean(K_CHILD_NOTICE, false)
        set(v) = prefs.edit().putBoolean(K_CHILD_NOTICE, v).apply()

    var lastSyncMs: Long
        get() = prefs.getLong(K_LAST_SYNC, 0L)
        set(v) = prefs.edit().putLong(K_LAST_SYNC, v).apply()

    /** null = use the allowlist defaults. */
    var enabledApps: Set<String>?
        get() = prefs.getStringSet(K_APPS, null)?.toSet()
        set(v) = prefs.edit().putStringSet(K_APPS, v).apply()

    val isPaired: Boolean get() = deviceToken != null && wsUrl != null

    fun clearPairing() = prefs.edit().remove(K_TOKEN).remove(K_DEVICE_ID).remove(K_SERVER).remove(K_WS).apply()

    @Synchronized
    fun salt(): ByteArray = secretBytes(K_SALT)

    @Synchronized
    fun dbPassphrase(): ByteArray = secretBytes(K_DB_KEY)

    private fun secretBytes(key: String): ByteArray {
        prefs.getString(key, null)?.let { return Base64.decode(it, Base64.NO_WRAP) }
        val fresh = Hasher.newSalt()
        prefs.edit().putString(key, Base64.encodeToString(fresh, Base64.NO_WRAP)).commit()
        return fresh
    }

    private companion object {
        const val K_TOKEN = "device_token"
        const val K_DEVICE_ID = "device_id"
        const val K_SERVER = "server_url"
        const val K_WS = "ws_url"
        const val K_CONSENT = "guardian_consent"
        const val K_CHILD_NOTICE = "child_notice_shown"
        const val K_LAST_SYNC = "last_sync_ms"
        const val K_APPS = "enabled_apps"
        const val K_SALT = "install_salt"
        const val K_DB_KEY = "db_passphrase"
    }
}
