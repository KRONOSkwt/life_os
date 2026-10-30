package personal.lifeos

import okhttp3.Request
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.tls.HandshakeCertificates
import okhttp3.tls.HeldCertificate
import org.junit.Test
import org.junit.Assert.*
import java.security.MessageDigest
import java.util.concurrent.TimeUnit
import javax.net.ssl.SSLException

class TlsTest {
    @Test fun publicTrustDoesNotAcceptSelfSignedCertificate() = withServer("localhost") { server, _ ->
        try {
            serverClient("system", "").newCall(Request.Builder().url(server.url("/")).build()).execute().close()
            fail("Public trust accepted a self-signed certificate")
        } catch (_: SSLException) { }
    }
    @Test fun publicTrustRejectsLocalPinsAndUnknownModes() {
        for ((mode, fingerprint) in listOf("system" to "0".repeat(64), "unknown" to "")) {
            try { serverClient(mode, fingerprint); fail("Invalid HTTPS mode accepted") }
            catch (_: IllegalArgumentException) { }
        }
        assertFalse(serverClient("system", "").followRedirects)
        assertFalse(serverClient("system", "").followSslRedirects)
    }
    private fun fingerprint(cert: HeldCertificate) = MessageDigest.getInstance("SHA-256")
        .digest(cert.certificate.encoded).joinToString("") { "%02x".format(it) }
    private fun withServer(host: String, test: (MockWebServer, HeldCertificate) -> Unit) {
        val cert = HeldCertificate.Builder().commonName("Life OS test").addSubjectAlternativeName(host).build()
        val tls = HandshakeCertificates.Builder().heldCertificate(cert).build()
        MockWebServer().use { server ->
            server.useHttps(tls.sslSocketFactory(), false)
            server.enqueue(MockResponse().setBody("OK"))
            server.start()
            test(server, cert)
        }
    }
    @Test fun independentlyPinnedSelfSignedCertificateAccepted() = withServer("localhost") { server, cert ->
        pinnedClient(fingerprint(cert)).newCall(Request.Builder().url(server.url("/").newBuilder().host("localhost").build()).build()).execute().use {
            assertEquals("OK", it.body!!.string())
        }
    }
    @Test fun wrongFingerprintRejectedBeforeHttp() = withServer("localhost") { server, _ ->
        try {
            pinnedClient("0".repeat(64)).newCall(Request.Builder().url(server.url("/").newBuilder().host("localhost").build()).build()).execute().close()
            fail("Wrong fingerprint accepted")
        } catch (_: SSLException) { }
        assertNull(server.takeRequest(100, TimeUnit.MILLISECONDS))
    }
    @Test fun correctFingerprintDoesNotBypassHostname() = withServer("different.invalid") { server, cert ->
        try {
            pinnedClient(fingerprint(cert)).newCall(Request.Builder().url(server.url("/").newBuilder().host("localhost").build()).build()).execute().close()
            fail("Wrong hostname accepted")
        } catch (_: SSLException) { }
        assertNull(server.takeRequest(100, TimeUnit.MILLISECONDS))
    }
}
