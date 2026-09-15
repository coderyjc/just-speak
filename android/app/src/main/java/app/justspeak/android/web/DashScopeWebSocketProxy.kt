package app.justspeak.android.web

import android.util.Base64
import androidx.core.net.toUri
import app.justspeak.android.BuildConfig
import app.justspeak.android.data.CredentialStore
import java.util.UUID
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.TimeUnit
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString.Companion.toByteString
import org.json.JSONObject

class DashScopeWebSocketProxy(
    private val credentialStore: CredentialStore,
    private val emit: (String, JSONObject) -> Unit,
) {
    private val client = OkHttpClient.Builder()
        .connectTimeout(15, TimeUnit.SECONDS)
        .pingInterval(15, TimeUnit.SECONDS)
        .build()
    private val sessions = ConcurrentHashMap<String, WebSocket>()

    fun openSession(payload: String): String = runCatching {
        val options = JSONObject(payload)
        val endpoint = options.getString("endpoint").trim()
        val uri = endpoint.toUri()
        require(uri.scheme == "wss" && !uri.host.isNullOrBlank()) {
            "ASR 地址必须是有效的 wss:// 地址"
        }
        val apiKey = credentialStore.readApiKey()
            ?.takeIf(String::isNotBlank)
            ?: error("请先保存百炼 API Key")
        val sessionId = UUID.randomUUID().toString()
        val request = Request.Builder()
            .url(endpoint)
            .header("Authorization", "Bearer $apiKey")
            .header("User-Agent", "JustSpeak-Android/${BuildConfig.VERSION_NAME}")
            .apply {
                options.optString("workspaceId").trim().takeIf(String::isNotEmpty)?.let {
                    header("X-DashScope-WorkSpace", it)
                }
            }
            .build()
        val socket = client.newWebSocket(request, listener(sessionId))
        sessions[sessionId] = socket
        JSONObject().put("sessionId", sessionId).toString()
    }.getOrElse { error ->
        JSONObject().put("error", error.message ?: "无法建立识别连接").toString()
    }

    fun sendText(sessionId: String, message: String): Boolean =
        sessions[sessionId]?.send(message) == true

    fun sendAudio(sessionId: String, encodedAudio: String): Boolean = runCatching {
        val bytes = Base64.decode(encodedAudio, Base64.NO_WRAP)
        sessions[sessionId]?.send(bytes.toByteString()) == true
    }.getOrDefault(false)

    fun closeSession(sessionId: String) {
        sessions.remove(sessionId)?.close(NORMAL_CLOSE_CODE, "client-complete")
    }

    fun closeAll() {
        sessions.values.forEach { it.close(NORMAL_CLOSE_CODE, "activity-destroyed") }
        sessions.clear()
        client.dispatcher.executorService.shutdown()
        client.connectionPool.evictAll()
    }

    private fun listener(sessionId: String) = object : WebSocketListener() {
        override fun onOpen(webSocket: WebSocket, response: Response) {
            emit("asrSocketOpen", JSONObject().put("sessionId", sessionId))
        }

        override fun onMessage(webSocket: WebSocket, text: String) {
            emit(
                "asrMessage",
                JSONObject()
                    .put("sessionId", sessionId)
                    .put("message", text),
            )
        }

        override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
            webSocket.close(code, reason)
        }

        override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
            sessions.remove(sessionId)
            emit(
                "asrSocketClosed",
                JSONObject()
                    .put("sessionId", sessionId)
                    .put("code", code)
                    .put("reason", reason),
            )
        }

        override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
            sessions.remove(sessionId)
            val status = response?.code?.let { "HTTP $it · " }.orEmpty()
            emit(
                "asrSocketFailure",
                JSONObject()
                    .put("sessionId", sessionId)
                    .put("message", status + (t.message ?: "识别连接意外中断")),
            )
        }
    }

    private companion object {
        const val NORMAL_CLOSE_CODE = 1000
    }
}
