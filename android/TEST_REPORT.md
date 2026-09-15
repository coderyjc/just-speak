# Android 测试记录

更新日期：2026-09-15

## 构建与静态检查

- `npm install`：成功，依赖审计 0 个漏洞。
- `npm run check`：JavaScript 语法、6 个 Node 单元测试和 Vite 生产构建通过。
- `testDebugUnitTest`：通过。
- `lintDebug`：通过，最终目标为 0 error、0 warning。
- `assembleDebug`：通过。
- `processReleaseMainManifest`：通过；Release 保持 `usesCleartextTraffic=false`。
- APK 资源检查覆盖 `assets/index.html`、`assets/styles.css`、`assets/app.js` 和 `assets/web-core.js`。

Web 单元测试覆盖：

1. 北京与新加坡 Workspace 端点生成。
2. DashScope `run-task` / `finish-task` 双工协议字段。
3. 临时句与稳定句服务事件解析。
4. 48 kHz 到 16 kHz PCM16 重采样及小端编码。
5. 单声道 16 kHz PCM WAV 文件头与文稿预览。
6. WebView 麦克风权限、设备占用与音频源启动错误的中文指引。

## 浏览器交互验收

移动端视口完成以下流程：

- 主页与五项底部导航。
- 实时录音模拟：开始、转写中、暂停、继续、结束、历史归档。
- 文件流程：可见选择入口、文件状态、模拟转写进度、结果详情。
- 结果操作：复制、分享、TXT、WAV 入口。
- 设置表单与深色主题即时切换。
- 活跃页面唯一，Console 0 error、0 warning。

浏览器预览使用模拟云端，验证目标是界面和状态机。Android WebSocket 代理及真实百炼响应需要设备上的有效 API Key。

## 设备测试状态

`adb devices -l` 当前没有发现设备，因此以下项目等待用户安装后验证：

- Android 13–17 的安装、启动和系统栏表现。
- 首次允许/拒绝麦克风权限。
- 真机 WebView 麦克风采集、暂停/继续、结束和 WAV 导出。
- 系统文件选择器及设备媒体解码兼容性。
- 有效百炼 API Key 下的实时识别、断网与服务端错误提示。
- 前后台切换、锁屏和内存压力下的行为。

## 真机快速验收清单

1. 设置并保存 API Key，重启应用后确认仍显示“已保护”。
2. 录音 30 秒，至少暂停/继续两次，确认临时句与稳定句持续更新。
3. 结束后从历史复制文稿，分别导出 TXT 与 WAV。
4. 选择一段 AAC/MP4，确认解码、上传进度和最终文稿。
5. 切换深色主题并重启应用，确认主题保持。
6. 录音中锁屏或切到其他应用，记录系统是否冻结 WebView；本版本只保证前台工作。

最终调试 APK：`JustSpeak-0.2.1-webview-debug.apk`，8,621,245 bytes，SHA-256 `B52293538A06B4E4DE623802207CA219B532352BF7E9CC48D65C4E83A52832F1`。APK Signature Scheme v2 验证通过，签名证书为 Android Debug。
