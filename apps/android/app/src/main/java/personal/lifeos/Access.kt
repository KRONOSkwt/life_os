package personal.lifeos

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.AtomicFile
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.io.File
import java.io.IOException
import java.security.KeyStore
import java.security.MessageDigest
import java.security.SecureRandom
import java.security.cert.CertificateException
import java.security.cert.X509Certificate
import java.util.concurrent.TimeUnit
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec
import javax.net.ssl.SSLContext
import javax.net.ssl.X509TrustManager

data class Credentials(val url: String, val fingerprint: String, val serverId: String, val deviceId: String, val token: String,
                       val tlsMode: String = "pinned")

class CredentialVault(context: Context) {
    private val file = AtomicFile(File(context.noBackupFilesDir, "device.enc"))
    private fun key(): SecretKey {
        val store = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (store.getKey("life-os-device", null) as? SecretKey)?.let { return it }
        return KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore").apply {
            init(KeyGenParameterSpec.Builder("life-os-device", KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM).setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE).build())
        }.generateKey()
    }
    @Synchronized fun read(): Credentials? {
        if (!file.baseFile.exists()) return null
        val bytes = file.readFully()
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(128, bytes.copyOfRange(0, 12)))
        val json = JSONObject(String(cipher.doFinal(bytes.copyOfRange(12, bytes.size)), Charsets.UTF_8))
        return Credentials(json.getString("url"), json.getString("fingerprint"), json.getString("serverId"), json.getString("deviceId"), json.getString("token"), json.optString("tlsMode", "pinned"))
    }
    @Synchronized fun save(value: Credentials) {
        val json = JSONObject().put("url", value.url).put("fingerprint", value.fingerprint)
            .put("serverId", value.serverId).put("deviceId", value.deviceId).put("token", value.token).put("tlsMode", value.tlsMode)
        val cipher = Cipher.getInstance("AES/GCM/NoPadding").apply { init(Cipher.ENCRYPT_MODE, key()) }
        val output = file.startWrite()
        try {
            output.write(cipher.iv + cipher.doFinal(json.toString().toByteArray(Charsets.UTF_8)))
            file.finishWrite(output)
        } catch (error: Exception) { file.failWrite(output); throw error }
    }
}

// Trust is scoped to this connection and the independently supplied certificate DER hash.
// OkHttp retains its default SAN/hostname verifier, including for IP addresses.
fun pinnedClient(fingerprint: String): OkHttpClient {
    require(Regex("[0-9a-f]{64}").matches(fingerprint)) { "La huella debe tener 64 caracteres hexadecimales" }
    val trust = object : X509TrustManager {
        override fun getAcceptedIssuers(): Array<X509Certificate> = emptyArray()
        override fun checkClientTrusted(chain: Array<X509Certificate>, authType: String) { throw CertificateException("Client certificates unsupported") }
        override fun checkServerTrusted(chain: Array<X509Certificate>, authType: String) {
            val leaf = chain.firstOrNull() ?: throw CertificateException("Missing certificate")
            leaf.checkValidity()
            val actual = MessageDigest.getInstance("SHA-256").digest(leaf.encoded).joinToString("") { "%02x".format(it) }
            if (!MessageDigest.isEqual(actual.toByteArray(), fingerprint.toByteArray())) throw CertificateException("Certificate fingerprint mismatch")
        }
    }
    val tls = SSLContext.getInstance("TLS").apply { init(null, arrayOf(trust), SecureRandom()) }
    return OkHttpClient.Builder().sslSocketFactory(tls.socketFactory, trust)
        .followRedirects(false).followSslRedirects(false).retryOnConnectionFailure(false)
        .connectTimeout(10, TimeUnit.SECONDS).readTimeout(15, TimeUnit.SECONDS)
        .callTimeout(30, TimeUnit.SECONDS).build()
}

class AccessRejected : IOException("Acceso rechazado. Revisa la vinculación; los datos locales siguen guardados.")

fun serverClient(mode: String, fingerprint: String): OkHttpClient = when (mode) {
    "pinned" -> pinnedClient(fingerprint)
    "system" -> {
        require(fingerprint.isEmpty()) { "La confianza pública no utiliza una huella local" }
        // Keep Android's CA validation and default hostname verifier; certificates can rotate.
        OkHttpClient.Builder().followRedirects(false).followSslRedirects(false).retryOnConnectionFailure(false)
            .connectTimeout(10, TimeUnit.SECONDS).readTimeout(15, TimeUnit.SECONDS)
            .callTimeout(30, TimeUnit.SECONDS).build()
    }
    else -> throw IllegalArgumentException("Modo HTTPS incompatible")
}

interface SyncApi {
    fun push(operation: String): JSONObject
    fun pull(cursor: String?): JSONObject
}

class ServerApi(private val credentials: Credentials) : SyncApi {
    private val client = serverClient(credentials.tlsMode, credentials.fingerprint)
    override fun push(operation: String): JSONObject = request("/api/v1/sync/push",
        "{\"protocol_version\":1,\"operations\":[$operation]}")
    override fun pull(cursor: String?): JSONObject {
        val url = (credentials.url + "/api/v1/sync/pull").toHttpUrl().newBuilder().addQueryParameter("limit", "1")
        cursor?.let { url.addQueryParameter("cursor", it) }
        return execute(client, Request.Builder().url(url.build()).header("Authorization", "Bearer ${credentials.token}").build())
    }
    private fun request(path: String, body: String) = execute(client, Request.Builder().url(credentials.url + path)
        .header("Authorization", "Bearer ${credentials.token}").post(body.toRequestBody(JSON)).build())

    companion object {
        private val JSON = "application/json; charset=utf-8".toMediaType()
        fun pair(invitation: String, deviceName: String): Credentials {
            val json = JSONObject(invitation)
            require(json.getInt("protocol_version") == 1) { "Versión de invitación incompatible" }
            val origin = json.getString("server_url").toHttpUrl()
            require(origin.isHttps && origin.username.isEmpty() && origin.password.isEmpty() &&
                origin.encodedPath == "/" && origin.query == null && origin.fragment == null) { "Se requiere un origen HTTPS" }
            require(deviceName.isNotBlank() && deviceName.length <= 80) { "Nombre del dispositivo inválido" }
            require(json.getLong("expires_at") > java.time.Instant.now().epochSecond) { "La invitación caducó; genera otra en el PC" }
            val mode = json.optString("tls_mode", "pinned")
            val fingerprint = if (mode == "system") {
                require(!json.has("certificate_sha256")) { "La invitación pública no debe incluir una huella local" }
                ""
            } else json.getString("certificate_sha256")
            val body = JSONObject().put("pairing_code", json.getString("pairing_code")).put("device_name", deviceName)
            val response = execute(serverClient(mode, fingerprint), Request.Builder().url(origin.newBuilder()
                .encodedPath("/api/v1/pairing/exchange").build()).post(body.toString().toRequestBody(JSON)).build())
            require(response.getString("server_id") == json.getString("server_id") && response.getString("token_type") == "Bearer") { "Identidad del servidor inesperada" }
            return Credentials(origin.toString().trimEnd('/'), fingerprint, response.getString("server_id"), response.getString("device_id"), response.getString("access_token"), mode)
        }
        private fun execute(client: OkHttpClient, request: Request): JSONObject = client.newCall(request).execute().use { response ->
            if (response.code == 401 || response.code == 403) throw AccessRejected()
            if (!response.isSuccessful) throw IOException("El servidor respondió ${response.code}; los cambios locales se conservan")
            val body = response.body ?: throw IOException("Respuesta vacía")
            val source = body.source()
            if (source.request(4L * 1024 * 1024 + 1)) throw IOException("Respuesta demasiado grande")
            val json = JSONObject(source.readUtf8())
            require(json.getInt("protocol_version") == 1) { "Versión de protocolo incompatible" }
            json
        }
    }
}
