# Minification is off for the prototype. If enabled, keep serialization and SQLCipher classes:
-keepattributes *Annotation*, InnerClasses
-keep class net.zetetic.database.** { *; }
-keepclassmembers class org.safechat.guardian.** { *** Companion; kotlinx.serialization.KSerializer serializer(...); }
