package com.ojsentinel.app

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.content.SharedPreferences
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.os.Bundle
import android.text.InputType
import android.view.Gravity
import android.view.ViewGroup
import android.webkit.WebChromeClient
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Button
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.HorizontalScrollView
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import org.json.JSONArray
import org.json.JSONObject
import java.io.IOException
import java.net.CookieHandler
import java.net.CookieManager as JavaCookieManager
import java.net.CookiePolicy
import java.net.HttpCookie
import java.net.HttpURLConnection
import java.net.URI
import java.net.URL
import android.webkit.CookieManager as WebKitCookieManager

/**
 * OJ Sentinel Android client.
 *
 * Thin WebView shell around the OJ Sentinel server that runs on a computer:
 *
 *  1. "服务器" settings screen stores the server URL (and non-secret Hydro
 *     connection fields), probes `GET /api/health`.
 *  2. "登录并打开" posts `{base_url, domain, username, password}` to
 *     `POST /api/session`, keeps the `ojs_session` cookie in a java.net
 *     CookieManager, and copies Set-Cookie values into the WebView cookie jar.
 *  3. The WebView then renders the same-origin SPA; the tab buttons map to the
 *     SPA sections 查重 / 异常 / 评分 (hash + nav-label best effort) and the AI
 *     tab detects/launches the dsh-android companion.
 *
 * Passwords are never persisted on the device. The app never talks to a
 * third-party service: all requests go to the user-configured server.
 */
class MainActivity : Activity() {

    companion object {
        private const val PREFS = "ojs_settings"
        private const val KEY_SERVER = "server_url"
        private const val KEY_OJ = "oj_url"
        private const val KEY_DOMAIN = "domain"
        private const val KEY_USERNAME = "username"

        private const val DEFAULT_SERVER = "http://127.0.0.1:8765"

        /** dsh-android 0.3.0 package verified with:
         *  aapt dump badging ~/dsh-android/dist/dsh-android-0.3.0-arm64-debug.apk
         *  => package: name='com.dsh.android' / activity com.dsh.android.MainActivity
         *  com.deepseek.dsh is kept as a forward-compatible alias.
         */
        private val DSH_PACKAGES = listOf("com.dsh.android", "com.deepseek.dsh")

        // Colours (framework-only palette; matches the Web GUI's slate theme).
        private val COLOR_BG = Color.parseColor("#0F172A")
        private val COLOR_HEADER = Color.parseColor("#020617")
        private val COLOR_TAB_BAR = Color.parseColor("#111827")
        private val COLOR_SURFACE = Color.parseColor("#1E293B")
        private val COLOR_TEXT = Color.parseColor("#E2E8F0")
        private val COLOR_MUTED = Color.parseColor("#94A3B8")
        private val COLOR_ACCENT = Color.parseColor("#2563EB")
        private val COLOR_STATUS = Color.parseColor("#0B1220")
    }

    private lateinit var prefs: SharedPreferences
    private val cookieManager = JavaCookieManager(null, CookiePolicy.ACCEPT_ALL)

    private lateinit var content: FrameLayout
    private lateinit var status: TextView
    private lateinit var tabBar: LinearLayout

    private var webView: WebView? = null
    private var pageFinished = false
    private var pendingSection: String? = null
    private var serverUrl = DEFAULT_SERVER

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        prefs = getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        serverUrl = normalizeServer(prefs.getString(KEY_SERVER, DEFAULT_SERVER).orEmpty())
        // All native HTTP calls (health/login) share cookies through this jar.
        CookieHandler.setDefault(cookieManager)

        buildChrome()
        showSettings()
    }

    // ------------------------------------------------------------------ chrome

    private fun buildChrome() {
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundColor(COLOR_BG)
            fitsSystemWindows = true
        }

        val header = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setPadding(dp(16), dp(14), dp(16), dp(14))
            setBackgroundColor(COLOR_HEADER)
        }
        header.addView(TextView(this).apply {
            text = "OJ Sentinel"
            setTextColor(Color.WHITE)
            textSize = 20f
            setTypeface(typeface, Typeface.BOLD)
        })
        header.addView(TextView(this).apply {
            text = "  Android 客户端"
            setTextColor(COLOR_MUTED)
            textSize = 12f
        })
        root.addView(header)

        tabBar = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            setBackgroundColor(COLOR_TAB_BAR)
            setPadding(dp(4), dp(4), dp(4), dp(4))
        }
        root.addView(HorizontalScrollView(this).apply {
            isHorizontalScrollBarEnabled = false
            addView(tabBar)
        })

        content = FrameLayout(this).apply {
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f
            )
        }
        root.addView(content)

        status = TextView(this).apply {
            text = "就绪"
            setTextColor(Color.parseColor("#CBD5E1"))
            setBackgroundColor(COLOR_STATUS)
            textSize = 12f
            setPadding(dp(12), dp(6), dp(12), dp(6))
        }
        root.addView(status)

        setContentView(root)

        addTab("服务器") { showSettings() }
        addTab("查重") { navigateTo("pairs") }
        addTab("异常") { navigateTo("anomalies") }
        addTab("评分") { navigateTo("scores") }
        addTab("AI") { showAiPanel() }
    }

    private fun addTab(label: String, onClick: () -> Unit) {
        tabBar.addView(Button(this).apply {
            text = label
            isAllCaps = false
            textSize = 13f
            setTextColor(COLOR_TEXT)
            setBackgroundColor(Color.TRANSPARENT)
            setPadding(dp(16), dp(8), dp(16), dp(8))
            setOnClickListener { onClick() }
        })
    }

    // ---------------------------------------------------------------- settings

    private fun showSettings() {
        val scroll = ScrollView(this)
        val box = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(20), dp(12), dp(20), dp(28))
        }
        scroll.addView(box)

        box.addView(sectionTitle("连接设置"))
        box.addView(hint(
            "OJ Sentinel 的 Python 服务运行在电脑上（`ojs serve` 或桌面应用）。" +
                "此 App 只是它的远程 WebView 客户端：请填写电脑的局域网地址，例如 " +
                "http://192.0.2.10:8765。"
        ))

        val serverField = addField(
            box, "服务器地址（OJ Sentinel）", serverUrl,
            "http://192.0.2.10:8765"
        )
        val ojField = addField(
            box, "Hydro OJ 地址", prefs.getString(KEY_OJ, "").orEmpty(),
            "http://YOUR_OJ_HOST"
        )
        val domainField = addField(
            box, "OJ 域名", prefs.getString(KEY_DOMAIN, "").orEmpty(),
            "YOUR_DOMAIN"
        )
        val userField = addField(
            box, "用户名", prefs.getString(KEY_USERNAME, "").orEmpty(), "学号 / 教师账号"
        )
        val passwordField = addField(box, "密码（仅本次登录使用，不保存）", "", "OJ 密码", password = true)

        val row = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
        row.addView(actionButton("测试连接", primary = false) {
            testConnection(serverField.text.toString())
        }, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply {
            marginEnd = dp(8)
        })
        row.addView(actionButton("登录并打开", primary = true) {
            loginAndOpen(serverField, ojField, domainField, userField, passwordField)
        }, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
        box.addView(row, LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT
        ).apply { topMargin = dp(18) })

        box.addView(actionButton("打开 Web GUI（无需重新登录）", primary = false) {
            openWeb(serverField.text.toString())
        }, LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT
        ).apply { topMargin = dp(10) })

        box.addView(hint(
            "隐私：密码只在内存中使用；服务器地址、OJ 地址、域名、用户名保存在本机 " +
                "SharedPreferences。学生代码与报告始终留在电脑的 data/ 目录，App 不上传任何数据。"
        ))
        box.addView(hint(
            "提示：手机与电脑需在同一网络；电脑防火墙需放行服务端口；" +
                "若用模拟器，请把地址改成 http://10.0.2.2:8765。"
        ))

        content.removeAllViews()
        content.addView(scroll)
        status.text = "未连接 · 服务器 $serverUrl"
    }

    private fun addField(
        parent: LinearLayout,
        label: String,
        value: String,
        hintText: String,
        password: Boolean = false
    ): EditText {
        parent.addView(TextView(this).apply {
            text = label
            setTextColor(COLOR_MUTED)
            textSize = 13f
            setPadding(0, dp(14), 0, dp(4))
        })
        val edit = EditText(this).apply {
            setText(value)
            hint = hintText
            setHintTextColor(Color.parseColor("#64748B"))
            setTextColor(COLOR_TEXT)
            textSize = 14f
            setSingleLine(true)
            if (password) {
                inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD
            }
        }
        parent.addView(edit, LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT
        ))
        return edit
    }

    // ------------------------------------------------------------ WebView flow

    private fun ensureWebView() {
        if (webView != null) return
        val view = WebView(this)
        WebView.setWebContentsDebuggingEnabled(true)
        view.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            allowFileAccess = false
            allowContentAccess = false
            mixedContentMode = WebSettings.MIXED_CONTENT_COMPATIBILITY_MODE
            cacheMode = WebSettings.LOAD_DEFAULT
            loadWithOverviewMode = true
            useWideViewPort = true
            builtInZoomControls = true
            displayZoomControls = false
            mediaPlaybackRequiresUserGesture = true
        }
        view.webViewClient = object : WebViewClient() {
            override fun onPageFinished(view: WebView?, url: String?) {
                pageFinished = true
                status.text = "已连接 $serverUrl"
                pendingSection?.let { section ->
                    pendingSection = null
                    navigateNow(section)
                }
            }
        }
        view.webChromeClient = object : WebChromeClient() {
            override fun onProgressChanged(view: WebView?, newProgress: Int) {
                if (newProgress < 100) status.text = "加载中 $newProgress%"
            }
        }
        webView = view
    }

    private fun attachWebView() {
        ensureWebView()
        val view = webView ?: return
        (view.parent as? ViewGroup)?.removeView(view)
        content.removeAllViews()
        content.addView(view, FrameLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT
        ))
    }

    private fun openWeb(serverRaw: String) {
        val server = normalizeServer(serverRaw)
        serverUrl = server
        prefs.edit().putString(KEY_SERVER, server).apply()
        attachWebView()
        pageFinished = false
        pendingSection = null
        webView?.loadUrl("$server/")
        status.text = "正在打开 $server ..."
    }

    private fun navigateTo(section: String) {
        val view = webView
        if (view == null || view.parent == null) {
            toast("请先在「服务器」页登录并打开 Web GUI")
            showSettings()
            return
        }
        if (!pageFinished) {
            pendingSection = section
            view.loadUrl("$serverUrl/#/$section")
            return
        }
        navigateNow(section)
    }

    private fun navigateNow(section: String) {
        val view = webView ?: return
        view.evaluateJavascript(navScript(section), null)
    }

    /**
     * Best-effort SPA navigation. The Web GUI is built in parallel and its exact
     * routes are not part of docs/INTERFACES.md, so we try, in order:
     *   1. an optional `window.ojsNavigate(section)` bridge,
     *   2. clicking a nav element whose visible text contains the Chinese or
     *      English section label,
     *   3. updating `location.hash` (`#/pairs`, `#/anomalies`, `#/scores`).
     * A SPA that ignores all three still leaves the user on the working root view.
     */
    private fun navScript(section: String): String {
        // The Web GUI (src/oj_sentinel/server/static/index.html) exposes tabs as
        // <button data-tab="overview|acquire|pairs|anomalies|final|project|judge">.
        val tab = when (section) {
            "pairs" -> "pairs"
            "anomalies" -> "anomalies"
            "scores" -> "final"
            "ai" -> "project"
            else -> "overview"
        }
        val labels = when (section) {
            "pairs" -> listOf("查重", "查重排行", "重复对", "Plagiarism")
            "anomalies" -> listOf("异常", "异常行为", "Anomalies")
            "scores" -> listOf("评分", "成绩", "Scores")
            "ai" -> listOf("作品评测", "AI 评测", "AI 分析", "AI")
            else -> listOf("首页", "概览", "Home", "Dashboard")
        }
        val labelJson = JSONArray(labels).toString()
        return """
            (function () {
              var section = ${JSONObject.quote(section)};
              var tab = ${JSONObject.quote(tab)};
              var labels = $labelJson;
              try {
                if (typeof window.ojsNavigate === 'function') {
                  window.ojsNavigate(section);
                  return 'bridge';
                }
              } catch (ignored) {}
              var tabEl = document.querySelector('[data-tab="' + tab + '"]');
              if (tabEl) {
                tabEl.click();
                return 'tab';
              }
              var nodes = document.querySelectorAll(
                'a,button,[role=tab],[role=menuitem],[data-section],[data-tab],nav *'
              );
              for (var i = 0; i < nodes.length; i++) {
                var el = nodes[i];
                if (!el || el.offsetParent === null) continue;
                var text = (el.textContent || '').trim();
                for (var j = 0; j < labels.length; j++) {
                  if (text === labels[j] || text.indexOf(labels[j]) !== -1) {
                    el.click();
                    return 'click';
                  }
                }
              }
              if (location.hash !== '#/' + section) location.hash = '#/' + section;
              return 'hash';
            })();
        """.trimIndent()
    }

    // ------------------------------------------------------------------- AI tab

    private fun showAiPanel() {
        val scroll = ScrollView(this)
        val box = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(20), dp(12), dp(20), dp(28))
        }
        scroll.addView(box)

        box.addView(sectionTitle("AI 智能分析"))
        box.addView(hint(
            "AI 复核由 dsh-android（DeepSeek Harness for Android，ARM64）提供。" +
                "OJ Sentinel 不会把学生代码上传到第三方；AI 只在你明确触发时处理选中的片段。"
        ))

        val dshPackage = findDshPackage()
        if (dshPackage != null) {
            box.addView(hint("已检测到 dsh-android：$dshPackage"))
            box.addView(actionButton("打开 dsh-android", primary = true) {
                launchPackage(dshPackage)
            }, LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT
            ).apply { topMargin = dp(12) })
            box.addView(hint(
                "在 dsh-android 中把工作目录指向 OJ Sentinel 的 data/ 导出内容，或让它通过 " +
                    "HTTP provider 连接电脑上的模型服务。桌面端的 AI 分析仍由电脑上的 dsh CLI 执行。"
            ))
        } else {
            box.addView(hint(
                "未检测到 dsh-android（包名 com.dsh.android）。\n\n" +
                    "安装方式一（推荐，电脑上执行，手机开启 USB 调试）：\n" +
                    "  adb install -r ~/dsh-android/dist/dsh-android-0.3.0-arm64-debug.apk\n\n" +
                    "安装方式二：从 dsh-android 项目的 GitHub Release 下载 arm64 debug APK " +
                    "后手动安装（需要允许安装未知来源应用）。\n\n" +
                    "注意：dsh-android 目前只提供 arm64-v8a 构建；x86 模拟器无法安装。"
            ))
            box.addView(actionButton("重新检测", primary = false) { showAiPanel() })
            box.addView(actionButton("打开 dsh-android 项目页", primary = false) {
                openExternal("https://github.com/tzhazuma/dsh-android")
            })
        }

        box.addView(hint(
            "也可以直接在电脑端 Web GUI 使用 AI：桌面/服务器进程会调用本机 dsh CLI，" +
                "模型默认为 opencode-go-2 / deepseek-v4.1-flash。"
        ))
        box.addView(actionButton("在 Web GUI 中打开 AI 区域", primary = false) {
            navigateTo("ai")
        })

        content.removeAllViews()
        content.addView(scroll)
        status.text = if (dshPackage != null) {
            "AI：已检测到 $dshPackage"
        } else {
            "AI：未检测到 dsh-android（可手动安装）"
        }
    }

    private fun findDshPackage(): String? {
        for (pkg in DSH_PACKAGES) {
            try {
                if (packageManager.getLaunchIntentForPackage(pkg) != null) return pkg
            } catch (_: Exception) {
                // package manager unavailable / package missing
            }
        }
        return null
    }

    private fun launchPackage(pkg: String) {
        val intent = packageManager.getLaunchIntentForPackage(pkg)
        if (intent == null) {
            toast("无法启动 $pkg")
            return
        }
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        try {
            startActivity(intent)
        } catch (exc: Exception) {
            toast("启动失败: ${exc.message}")
        }
    }

    private fun openExternal(url: String) {
        try {
            startActivity(Intent(Intent.ACTION_VIEW, android.net.Uri.parse(url)))
        } catch (exc: Exception) {
            toast("没有可打开链接的应用")
        }
    }

    // ------------------------------------------------------------ login / HTTP

    private fun testConnection(serverRaw: String) {
        val server = normalizeServer(serverRaw)
        status.text = "正在连接 $server ..."
        Thread {
            try {
                val response = httpGet("$server/api/health")
                runOnUiThread {
                    if (response.code == 200) {
                        status.text = "服务器在线 · ${response.body.take(160)}"
                        toast("连接成功")
                    } else {
                        status.text = "服务器返回 HTTP ${response.code}"
                        toast("服务器返回 HTTP ${response.code}")
                    }
                }
            } catch (exc: Exception) {
                runOnUiThread {
                    status.text = "连接失败: ${exc.message}"
                    toast("连接失败: ${exc.message}")
                }
            }
        }.start()
    }

    private fun loginAndOpen(
        serverField: EditText,
        ojField: EditText,
        domainField: EditText,
        userField: EditText,
        passwordField: EditText
    ) {
        val server = normalizeServer(serverField.text.toString())
        val oj = ojField.text.toString().trim()
        val domain = domainField.text.toString().trim()
        val username = userField.text.toString().trim()
        val password = passwordField.text.toString()
        if (oj.isEmpty() || domain.isEmpty() || username.isEmpty() || password.isEmpty()) {
            toast("请填写 OJ 地址、域名、用户名和密码")
            return
        }

        serverUrl = server
        prefs.edit()
            .putString(KEY_SERVER, server)
            .putString(KEY_OJ, oj)
            .putString(KEY_DOMAIN, domain)
            .putString(KEY_USERNAME, username)
            .apply()
        status.text = "正在连接服务器并登录 ..."

        Thread {
            try {
                val health = httpGet("$server/api/health")
                if (health.code != 200) {
                    throw IOException("服务器 /api/health 返回 HTTP ${health.code}")
                }
                val payload = JSONObject().apply {
                    put("base_url", oj)
                    put("domain", domain)
                    put("username", username)
                    put("password", password)
                }
                val response = httpPostJson("$server/api/session", payload)
                if (response.code !in 200..299) {
                    throw IOException("登录失败 HTTP ${response.code}: ${response.body.take(300)}")
                }
                val synced = syncCookies(server, response.setCookieHeaders)
                runOnUiThread {
                    passwordField.setText("")
                    status.text = if (synced > 0) {
                        "登录成功，已同步 $synced 个会话 cookie"
                    } else {
                        "登录成功（服务器未返回 cookie，Web GUI 可能需要再次登录）"
                    }
                    toast("登录成功")
                    openWeb(server)
                }
            } catch (exc: Exception) {
                runOnUiThread {
                    status.text = "登录失败: ${exc.message}"
                    toast("登录失败: ${exc.message}")
                }
            }
        }.start()
    }

    /**
     * Copy the session cookie to the WebView's own cookie jar so the SPA loads
     * authenticated. We use the raw Set-Cookie headers when available and fall
     * back to the shared java.net CookieManager store.
     */
    private fun syncCookies(server: String, setCookieHeaders: List<String>): Int {
        val web = WebKitCookieManager.getInstance()
        web.setAcceptCookie(true)
        val origin = originOf(server)
        var count = 0

        for (header in setCookieHeaders) {
            val pair = header.substringBefore(';').trim()
            if (pair.contains('=')) {
                val cookie = if (header.contains("Path=", ignoreCase = true)) {
                    header
                } else {
                    "$header; Path=/"
                }
                web.setCookie(origin, cookie)
                count++
            }
        }
        if (count == 0) {
            for (cookie in cookieManager.cookieStore.get(URI(server))) {
                val builder = StringBuilder("${cookie.name}=${cookie.value}")
                builder.append("; Path=").append(cookie.path ?: "/")
                if (!cookie.domain.isNullOrEmpty()) builder.append("; Domain=").append(cookie.domain)
                if (cookie.secure) builder.append("; Secure")
                web.setCookie(origin, builder.toString())
                count++
            }
        }
        web.flush()
        return count
    }

    private fun httpGet(url: String): HttpResponse {
        val conn = URL(url).openConnection() as HttpURLConnection
        conn.requestMethod = "GET"
        conn.connectTimeout = 8000
        conn.readTimeout = 15000
        conn.setRequestProperty("Accept", "application/json")
        return readResponse(conn)
    }

    private fun httpPostJson(url: String, payload: JSONObject): HttpResponse {
        val body = payload.toString().toByteArray(Charsets.UTF_8)
        val conn = URL(url).openConnection() as HttpURLConnection
        conn.requestMethod = "POST"
        conn.connectTimeout = 8000
        conn.readTimeout = 30000
        conn.doOutput = true
        conn.setRequestProperty("Content-Type", "application/json; charset=utf-8")
        conn.setRequestProperty("Accept", "application/json")
        conn.setFixedLengthStreamingMode(body.size)
        conn.outputStream.use { it.write(body) }
        return readResponse(conn)
    }

    private fun readResponse(conn: HttpURLConnection): HttpResponse {
        try {
            val code = conn.responseCode
            val stream = if (code in 200..299) conn.inputStream else conn.errorStream
            val body = stream?.bufferedReader(Charsets.UTF_8)?.use { it.readText() }.orEmpty()
            val setCookies = conn.headerFields.entries
                .filter { it.key?.equals("Set-Cookie", ignoreCase = true) == true }
                .flatMap { it.value ?: emptyList() }
            return HttpResponse(code, body, setCookies)
        } finally {
            conn.disconnect()
        }
    }

    private data class HttpResponse(
        val code: Int,
        val body: String,
        val setCookieHeaders: List<String> = emptyList()
    )

    // ------------------------------------------------------------------ helpers

    private fun normalizeServer(raw: String): String {
        var value = raw.trim()
        if (value.isEmpty()) return DEFAULT_SERVER
        if (!value.startsWith("http://") && !value.startsWith("https://")) {
            value = "http://$value"
        }
        return value.trimEnd('/')
    }

    private fun originOf(server: String): String {
        return try {
            val uri = URI(server)
            val port = if (uri.port > 0) ":${uri.port}" else ""
            "${uri.scheme}://${uri.host}$port"
        } catch (_: Exception) {
            server
        }
    }

    private fun sectionTitle(text: String): TextView = TextView(this).apply {
        this.text = text
        setTextColor(Color.WHITE)
        textSize = 20f
        setTypeface(typeface, Typeface.BOLD)
        setPadding(0, dp(8), 0, dp(6))
    }

    private fun hint(text: String): TextView = TextView(this).apply {
        this.text = text
        setTextColor(COLOR_MUTED)
        textSize = 13f
        setLineSpacing(dp(3).toFloat(), 1f)
        setPadding(0, dp(8), 0, dp(8))
    }

    private fun actionButton(label: String, primary: Boolean, onClick: () -> Unit): Button {
        val color = if (primary) COLOR_ACCENT else COLOR_SURFACE
        return Button(this).apply {
            text = label
            isAllCaps = false
            textSize = 14f
            setTextColor(Color.WHITE)
            background = GradientDrawable().apply {
                cornerRadius = dp(10).toFloat()
                setColor(color)
            }
            setPadding(dp(14), dp(10), dp(14), dp(10))
            setOnClickListener { onClick() }
        }
    }

    private fun dp(value: Int): Int = (value * resources.displayMetrics.density).toInt()

    private fun toast(message: String) {
        Toast.makeText(this, message, Toast.LENGTH_LONG).show()
    }

    // ------------------------------------------------------------ back handling

    @Suppress("DEPRECATION", "OVERRIDE_DEPRECATION")
    override fun onBackPressed() {
        val view = webView
        if (view != null && view.parent != null) {
            if (view.canGoBack()) {
                view.goBack()
                return
            }
            showSettings()
            return
        }
        @Suppress("DEPRECATION")
        super.onBackPressed()
    }

    override fun onDestroy() {
        webView?.apply {
            stopLoading()
            (parent as? ViewGroup)?.removeView(this)
            destroy()
        }
        webView = null
        super.onDestroy()
    }
}
