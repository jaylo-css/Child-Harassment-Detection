package org.safechat.guardian.core

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json

@Serializable
data class AllowedApp(val packageName: String, val label: String, val enabledByDefault: Boolean = true)

@Serializable
data class AllowlistFile(val version: Int = 1, val apps: List<AllowedApp>)

/**
 * Only chat apps on this list are ever read (config file: assets/allowlist.json). Everything else
 * - banking, OTP codes, email - is ignored before any parsing happens.
 */
class Allowlist(val apps: List<AllowedApp>) {
    private val byPackage = apps.associateBy { it.packageName }

    /** `enabled` = the guardian's toggles; null means "use enabledByDefault". */
    fun isAllowed(packageName: String, enabled: Set<String>? = null): Boolean {
        val app = byPackage[packageName] ?: return false
        return enabled?.contains(packageName) ?: app.enabledByDefault
    }

    fun label(packageName: String): String? = byPackage[packageName]?.label

    fun defaults(): Set<String> = apps.filter { it.enabledByDefault }.map { it.packageName }.toSet()

    companion object {
        private val json = Json { ignoreUnknownKeys = true }
        private val PACKAGE = Regex("""^[a-zA-Z][\w]*(\.[a-zA-Z][\w]*)+$""")

        fun parse(text: String): Allowlist {
            val file = json.decodeFromString(AllowlistFile.serializer(), text)
            file.apps.forEach { require(PACKAGE.matches(it.packageName)) { "invalid package: ${it.packageName}" } }
            return Allowlist(file.apps.distinctBy { it.packageName })
        }
    }
}
