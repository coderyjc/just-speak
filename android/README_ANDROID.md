# JustSpeak Android

JustSpeak Android 是面向 Android 13 及以上版本的中文语音转文字客户端。全部可见界面、录音处理、文件解码、任务历史和本地文稿均运行在 WebView 中；精简 Kotlin 层负责 Android 权限、Keystore 凭据、带鉴权头的 WebSocket、系统文件选择、剪贴板、分享和 TXT 导出。

## 当前能力

- 主页、实时录音、文件转写、历史、设置五个 Web 页面，支持点击与横向滑动切换。
- Telegram 风格的轻量层级、底部导航、即时按压反馈、状态过渡、浅色与深色主题。
- `getUserMedia` + Web Audio 采集麦克风，流式重采样为 16 kHz 单声道 PCM16。
- 100 ms 音频包通过 Kotlin WebSocket 代理发送到百炼实时 ASR；支持临时句、稳定句、暂停、继续和结束。
- 音视频文件通过系统选择器进入 WebView，由 `decodeAudioData` 解码、降混并按实时节奏发送。
- 任务和设置保存到 `localStorage`，PCM 录音分段保存到 IndexedDB；支持文稿复制、系统分享、TXT 与 WAV 导出。
- API Key 通过 Android Keystore 的 AES-GCM 密钥加密，每次写入使用独立随机 IV。
- 浏览器预览自动启用模拟云端，绝大多数 UI/UX 可以脱离模拟器调试。
- Release 固定加载 APK 内置资源并关闭明文流量；Debug 可连接本机 Vite 服务。
- 设备数据已从 Android 云备份和设备迁移中排除。

## 安装 APK

已构建的调试安装包位于 `JustSpeak-0.2.1-webview-debug.apk`。连接手机、开启 USB 调试后，在本目录运行：

```powershell
adb install -r .\JustSpeak-0.2.1-webview-debug.apk
```

也可以将 APK 发送到手机后点按安装。若系统提示来源限制，请只为当前文件管理器临时开启“安装未知应用”。调试包使用 Android 默认调试证书，适合当前安装测试。

首次使用：

1. 打开“设置”，选择服务地域和识别语言。
2. 填写百炼 API Key；Workspace ID 通常可以留空。
3. 保存设置，进入“录音”，授权麦克风后开始说话。
4. 结束后从“历史”查看、复制、分享或导出结果。
5. “文件”页可选择设备支持解码的音频或视频文件并提交转写。

当前完整 WebView 方案依赖前台页面持续运行。录音或文件转写期间请保持应用位于前台，并避免锁屏。Android 可能在后台冻结 WebView，此版本未承诺后台持续录音。

### 麦克风启动排查

0.2.1 会先完成 Android 麦克风权限，再启动网页音频源；采集使用最小约束，语音增强按设备能力启用，音频服务冷启动失败时自动重试一次。

若仍提示麦克风启动失败：

1. 在“系统设置 → 应用 → JustSpeak → 权限”中允许麦克风。
2. 打开 Android 快捷设置中的系统麦克风总开关。
3. 结束通话、系统录音机、会议软件或其他正在使用麦克风的应用。
4. 更新 Android System WebView 与 Chrome，然后重启 JustSpeak。

## 本地构建

工具链：Android SDK Platform 37、JDK 17 或 21（需要包含 `jlink`）、AGP 9.4.0、Gradle 9.6.0、Kotlin 2.4.20。

```powershell
.\gradlew.bat testDebugUnitTest lintDebug assembleDebug
```

Gradle 默认输出：`app\build\outputs\apk\debug\app-debug.apk`。

连接 Android 13+ 设备或模拟器后，可执行：

```powershell
.\gradlew.bat connectedDebugAndroidTest
```

## Web UI 调试

Web 源码位于 `app/src/main/assets/`：

```powershell
cd .\web
npm.cmd install
npm.cmd run dev
```

浏览器打开 `http://127.0.0.1:5173/`。模拟器内的 Debug WebView 可连接同一服务：

```powershell
adb shell am force-stop app.justspeak.android
adb shell am start -n app.justspeak.android/.MainActivity --es web_url http://10.0.2.2:5173/
```

USB 真机可先执行 `adb reverse tcp:5173 tcp:5173`，再把启动地址改为 `http://127.0.0.1:5173/`。Chrome 的 `chrome://inspect/#devices` 可检查 WebView DOM、样式、Console 和网络请求。

Web 自动检查：

```powershell
cd .\web
npm.cmd run check
```

## 安全与数据边界

- API Key 不进入 JavaScript 存储，WebView 只能获知“已保存”状态。
- WebSocket 连接仅接受 `wss://` 地址，Authorization 头由 Kotlin 代理注入。
- 正式包只允许 `appassets.androidplatform.net` 资产域；调试包额外允许 `localhost`、`127.0.0.1` 和 `10.0.2.2`。
- 文稿与 PCM 片段保存在 WebView 应用数据中，卸载应用会一并清除。
- 音频会发送到用户配置的百炼地域，应用未接入统计 SDK，也未内置开发者 API Key。

实现边界见 [`IMPLEMENTATION_NOTES.md`](./IMPLEMENTATION_NOTES.md)，验证结果见 [`TEST_REPORT.md`](./TEST_REPORT.md)，原始产品要求见 [`JustSpeak_安卓版开发说明书.md`](./JustSpeak_安卓版开发说明书.md)。
