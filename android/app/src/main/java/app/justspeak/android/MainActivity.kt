package app.justspeak.android

import android.Manifest
import android.annotation.SuppressLint
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Intent
import android.content.pm.ApplicationInfo
import android.content.pm.PackageManager
import android.graphics.Color
import android.net.Uri
import android.os.Bundle
import android.os.Message
import android.view.HapticFeedbackConstants
import android.view.ViewGroup
import android.webkit.JavascriptInterface
import android.webkit.PermissionRequest
import android.webkit.RenderProcessGoneDetail
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.activity.ComponentActivity
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.content.ContextCompat
import androidx.core.net.toUri
import androidx.core.view.WindowCompat
import androidx.webkit.WebViewAssetLoader
import app.justspeak.android.data.CredentialStore
import app.justspeak.android.web.DashScopeWebSocketProxy
import org.json.JSONObject

class MainActivity : ComponentActivity() {
    private lateinit var webView: WebView
    private lateinit var credentialStore: CredentialStore
    private lateinit var webSocketProxy: DashScopeWebSocketProxy
    private var pageReady = false
    private var pendingWebPermission: PermissionRequest? = null
    private var pendingMicrophoneRequestId: String? = null
    private var fileChooserCallback: ValueCallback<Array<Uri>>? = null
    private var pendingTextExport: PendingTextExport? = null

    private val microphonePermission = registerForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { granted ->
        val request = pendingWebPermission.also { pendingWebPermission = null }
        val requestId = pendingMicrophoneRequestId.also { pendingMicrophoneRequestId = null }
        if (granted) {
            request?.grant(arrayOf(PermissionRequest.RESOURCE_AUDIO_CAPTURE))
        } else {
            request?.deny()
            if (request != null) sendToast("需要麦克风权限才能开始录音")
        }
        if (requestId != null) {
            sendEvent(
                "microphonePermissionResult",
                JSONObject()
                    .put("requestId", requestId)
                    .put("granted", granted),
            )
        }
    }

    private val filePicker = registerForActivityResult(
        ActivityResultContracts.StartActivityForResult(),
    ) { result ->
        val callback = fileChooserCallback.also { fileChooserCallback = null }
        val uri = result.data?.data
        callback?.onReceiveValue(uri?.let { arrayOf(it) })
    }

    private val textExporter = registerForActivityResult(
        ActivityResultContracts.CreateDocument("text/plain"),
    ) { uri ->
        val export = pendingTextExport.also { pendingTextExport = null }
        if (uri == null || export == null) return@registerForActivityResult
        runCatching {
            contentResolver.openOutputStream(uri)?.bufferedWriter(Charsets.UTF_8)?.use {
                it.write(export.content)
            } ?: error("无法打开目标文件")
        }.onSuccess {
            sendToast("TXT 已保存")
        }.onFailure {
            sendToast(it.message ?: "TXT 保存失败")
        }
    }

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        credentialStore = CredentialStore(this)
        webSocketProxy = DashScopeWebSocketProxy(credentialStore, ::sendEvent)
        WebView.setWebContentsDebuggingEnabled(isDebuggable())

        val assetLoader = WebViewAssetLoader.Builder()
            .addPathHandler("/assets/", WebViewAssetLoader.AssetsPathHandler(this))
            .build()

        webView = WebView(this).apply {
            id = R.id.web_app
            layoutParams = ViewGroup.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT,
            )
            setBackgroundColor(Color.TRANSPARENT)
            settings.apply {
                javaScriptEnabled = true
                domStorageEnabled = true
                cacheMode = WebSettings.LOAD_DEFAULT
                allowContentAccess = true
                allowFileAccess = false
                mixedContentMode = WebSettings.MIXED_CONTENT_NEVER_ALLOW
                mediaPlaybackRequiresUserGesture = true
                setSupportMultipleWindows(false)
            }
            addJavascriptInterface(WebBridge(), BRIDGE_NAME)
            webViewClient = webClient(assetLoader)
            webChromeClient = chromeClient()
        }
        setContentView(webView)
        webView.loadUrl(resolveWebUrl())
    }

    override fun onDestroy() {
        pendingWebPermission?.deny()
        pendingWebPermission = null
        pendingMicrophoneRequestId = null
        fileChooserCallback?.onReceiveValue(null)
        fileChooserCallback = null
        webSocketProxy.closeAll()
        webView.removeJavascriptInterface(BRIDGE_NAME)
        webView.stopLoading()
        webView.destroy()
        super.onDestroy()
    }

    @SuppressLint("MissingOnRenderProcessGone")
    private fun webClient(assetLoader: WebViewAssetLoader) = object : WebViewClient() {
        override fun shouldInterceptRequest(
            view: WebView,
            request: WebResourceRequest,
        ): WebResourceResponse? = assetLoader.shouldInterceptRequest(request.url)

        override fun onPageFinished(view: WebView, url: String) {
            pageReady = true
            sendEvent(
                "nativeReady",
                JSONObject().put("hasCredential", credentialStore.hasSavedApiKey()),
            )
        }

        override fun shouldOverrideUrlLoading(
            view: WebView,
            request: WebResourceRequest,
        ): Boolean = !isAllowedWebUrl(request.url)

        override fun onRenderProcessGone(view: WebView, detail: RenderProcessGoneDetail): Boolean {
            recreate()
            return true
        }
    }

    private fun chromeClient() = object : WebChromeClient() {
        override fun onPermissionRequest(request: PermissionRequest) {
            runOnUiThread { handleWebPermission(request) }
        }

        override fun onPermissionRequestCanceled(request: PermissionRequest) {
            if (pendingWebPermission === request) pendingWebPermission = null
        }

        override fun onShowFileChooser(
            webView: WebView,
            filePathCallback: ValueCallback<Array<Uri>>,
            fileChooserParams: WebChromeClient.FileChooserParams,
        ): Boolean {
            fileChooserCallback?.onReceiveValue(null)
            fileChooserCallback = filePathCallback
            val intent = Intent(Intent.ACTION_OPEN_DOCUMENT).apply {
                addCategory(Intent.CATEGORY_OPENABLE)
                type = "*/*"
                putExtra(
                    Intent.EXTRA_MIME_TYPES,
                    arrayOf("audio/*", "video/mp4", "video/webm", "video/quicktime"),
                )
            }
            filePicker.launch(intent)
            return true
        }

        override fun onCreateWindow(
            view: WebView,
            isDialog: Boolean,
            isUserGesture: Boolean,
            resultMsg: Message,
        ): Boolean = false
    }

    private fun handleWebPermission(request: PermissionRequest) {
        if (!isAllowedWebUrl(request.origin) || PermissionRequest.RESOURCE_AUDIO_CAPTURE !in request.resources) {
            request.deny()
            return
        }
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED) {
            request.grant(arrayOf(PermissionRequest.RESOURCE_AUDIO_CAPTURE))
            return
        }
        pendingWebPermission?.deny()
        pendingWebPermission = request
        microphonePermission.launch(Manifest.permission.RECORD_AUDIO)
    }

    private fun copyText(text: String) {
        getSystemService(ClipboardManager::class.java).setPrimaryClip(
            ClipData.newPlainText("JustSpeak 文稿", text),
        )
        sendToast("文稿已复制")
    }

    private fun shareText(title: String, text: String) {
        val intent = Intent(Intent.ACTION_SEND).apply {
            type = "text/plain"
            putExtra(Intent.EXTRA_SUBJECT, title)
            putExtra(Intent.EXTRA_TEXT, text)
        }
        startActivity(Intent.createChooser(intent, "分享文稿"))
    }

    private fun exportText(fileName: String, text: String) {
        pendingTextExport = PendingTextExport(text)
        textExporter.launch(fileName.sanitizedFileName())
    }

    private fun updateSystemBarTheme(dark: Boolean) {
        WindowCompat.getInsetsController(window, webView).apply {
            isAppearanceLightStatusBars = !dark
            isAppearanceLightNavigationBars = !dark
        }
    }

    private fun sendToast(message: String) {
        sendEvent("toast", JSONObject().put("message", message))
    }

    private fun sendEvent(name: String, payload: JSONObject) {
        if (!pageReady) return
        runOnUiThread {
            webView.evaluateJavascript(
                "window.JustSpeakNative?.receiveEvent(${JSONObject.quote(name)},${JSONObject.quote(payload.toString())})",
                null,
            )
        }
    }

    private fun resolveWebUrl(): String {
        val requested = intent.getStringExtra(EXTRA_WEB_URL)
        return if (isDebuggable() && requested != null && isAllowedDevUrl(requested.toUri())) {
            requested
        } else {
            LOCAL_WEB_URL
        }
    }

    private fun isAllowedWebUrl(uri: Uri): Boolean = when (uri.scheme) {
        "https" -> uri.host == LOCAL_WEB_HOST
        "http" -> isDebuggable() && isAllowedDevUrl(uri)
        else -> false
    }

    private fun isAllowedDevUrl(uri: Uri): Boolean =
        uri.scheme == "http" && uri.host in DEV_HOSTS

    private fun isDebuggable(): Boolean =
        applicationInfo.flags and ApplicationInfo.FLAG_DEBUGGABLE != 0

    private inner class WebBridge {
        @JavascriptInterface
        fun getInitialState(): String = JSONObject()
            .put("hasCredential", credentialStore.hasSavedApiKey())
            .put("appVersion", BuildConfig.VERSION_NAME)
            .put("platform", "android-webview")
            .toString()

        @JavascriptInterface
        fun saveApiKey(apiKey: String) = runOnUiThread {
            runCatching { credentialStore.saveApiKey(apiKey) }
                .onSuccess {
                    sendEvent("credentialChanged", JSONObject().put("hasCredential", true))
                    sendToast("API Key 已加密保存")
                }
                .onFailure { sendToast(it.message ?: "API Key 保存失败") }
        }

        @JavascriptInterface
        fun clearApiKey() = runOnUiThread {
            credentialStore.clearApiKey()
            sendEvent("credentialChanged", JSONObject().put("hasCredential", false))
            sendToast("设备凭据已清除")
        }

        @JavascriptInterface
        fun requestMicrophonePermission(requestId: String) = runOnUiThread {
            if (requestId.isBlank()) return@runOnUiThread
            if (
                ContextCompat.checkSelfPermission(
                    this@MainActivity,
                    Manifest.permission.RECORD_AUDIO,
                ) == PackageManager.PERMISSION_GRANTED
            ) {
                sendEvent(
                    "microphonePermissionResult",
                    JSONObject()
                        .put("requestId", requestId)
                        .put("granted", true),
                )
                return@runOnUiThread
            }
            pendingMicrophoneRequestId = requestId
            microphonePermission.launch(Manifest.permission.RECORD_AUDIO)
        }

        @JavascriptInterface
        fun openAsrSession(payload: String): String = webSocketProxy.openSession(payload)

        @JavascriptInterface
        fun sendAsrText(sessionId: String, message: String): Boolean =
            webSocketProxy.sendText(sessionId, message)

        @JavascriptInterface
        fun sendAsrAudio(sessionId: String, encodedAudio: String): Boolean =
            webSocketProxy.sendAudio(sessionId, encodedAudio)

        @JavascriptInterface
        fun closeAsrSession(sessionId: String) = webSocketProxy.closeSession(sessionId)

        @JavascriptInterface
        fun copyText(text: String) = runOnUiThread { this@MainActivity.copyText(text) }

        @JavascriptInterface
        fun shareText(title: String, text: String) = runOnUiThread {
            this@MainActivity.shareText(title, text)
        }

        @JavascriptInterface
        fun exportText(fileName: String, text: String) = runOnUiThread {
            this@MainActivity.exportText(fileName, text)
        }

        @JavascriptInterface
        fun haptic() = runOnUiThread {
            webView.performHapticFeedback(HapticFeedbackConstants.CONTEXT_CLICK)
        }

        @JavascriptInterface
        fun setSystemTheme(dark: Boolean) = runOnUiThread {
            updateSystemBarTheme(dark)
        }
    }

    private data class PendingTextExport(val content: String)

    private companion object {
        const val BRIDGE_NAME = "JustSpeakBridge"
        const val LOCAL_WEB_HOST = "appassets.androidplatform.net"
        const val LOCAL_WEB_URL = "https://$LOCAL_WEB_HOST/assets/index.html"
        const val EXTRA_WEB_URL = "web_url"
        val DEV_HOSTS = setOf("10.0.2.2", "127.0.0.1", "localhost")
    }
}

private fun String.sanitizedFileName(): String {
    val safe = replace(Regex("[\\\\/:*?\"<>|]"), "_").trim().ifEmpty { "JustSpeak-文稿" }
    return if (safe.endsWith(".txt", ignoreCase = true)) safe else "$safe.txt"
}
