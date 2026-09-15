# Android 实施记录

更新日期：2026-09-15

## 0.2.1 麦克风兼容修复

- JavaScript 调用 `getUserMedia` 前，通过异步 Bridge 完成 Android `RECORD_AUDIO` 权限预检，避免权限弹窗期间音频源提前启动。
- Manifest 增加普通权限 `MODIFY_AUDIO_SETTINGS`，供 Chromium/WebRTC 配置 Android 音频路径。
- 首次采集改用 `{ audio: true }` 最小约束；获得音轨后再以 `ideal` 约束启用单声道、回声消除、降噪和自动增益。
- `NotReadableError` 等音频服务冷启动异常会等待 500 ms 自动重试一次。
- 权限拒绝、无设备、设备占用和约束不兼容均转换为可执行的中文指引。
- 启动失败后点击“开始录音”会复用原任务，避免历史记录出现重复失败项。

## 0.2.0 WebView 版本

本轮按用户要求完成全量 WebView 迁移。HTML/CSS/JavaScript 承载全部可见 UI、任务状态、音频采集与处理、文件解码、历史、文稿和 PCM 本地存储。项目已移除 Compose、Room、DataStore、原生 `AudioRecord` 与前台录音服务，目录中不再保留对应实现和依赖。

| 层级 | 职责 |
| --- | --- |
| WebView | 五页界面、交互动画、主题、录音/文件音频处理、DashScope 协议状态机、历史与导出数据 |
| Kotlin 宿主 | WebView 安全加载、麦克风权限、系统文件选择、Keystore、WebSocket 鉴权代理、剪贴板/分享/TXT |
| Android 系统 | WebView/Chromium 媒体能力、SAF、Keystore、应用沙箱 |

## 实时识别链路

1. WebView 通过 `getUserMedia` 获取麦克风流，Web Audio 输出浮点采样。
2. `Pcm16Resampler` 保持跨回调状态，将设备采样率转换为 16 kHz 单声道 PCM16。
3. JavaScript 构造 DashScope `run-task`，等待 `task-started` 后按 100 ms 发送二进制 PCM。
4. Kotlin 使用 OkHttp 建立带 `Authorization: Bearer …` 的 `wss://` 连接，并把服务事件原样回传 WebView。
5. WebView 区分临时句和稳定句；结束时发送 `finish-task`，等待 `task-finished` 后归档。
6. 原始 PCM 分段保存在 IndexedDB，历史文稿与设置保存在 `localStorage`。

文件转写使用系统文件选择器。WebView 读取 Blob，经 `decodeAudioData` 解码、降混、重采样，再复用同一实时协议发送。可解码格式取决于设备 WebView/Chromium 的媒体支持。

## 安全实现

- API Key 由 Keystore AES-GCM 加密存储，JavaScript 无法读取明文。
- WebSocket 代理只接受合法 `wss://` 端点。
- Release WebView 只允许 APK 资产域，关闭文件访问、混合内容和明文流量。
- Debug 热更新地址限制在 `localhost`、`127.0.0.1` 与 `10.0.2.2`。
- `WebViewClient.onRenderProcessGone` 会重建 Activity，避免渲染进程退出时直接崩溃。
- 应用数据不参与系统备份和设备迁移。

## UI/UX 落地

- 主页、录音、文件、历史、设置保持单手触达的底部主导航。
- 卡片、列表、录音主按钮和结果弹层采用短促按压反馈与 120–300 ms 状态过渡。
- 录音页实时显示云端状态、时长、音量、稳定文稿和临时文稿。
- 文件页展示选择、解码、云端识别、本地归档的连续进度。
- 深色主题即时生效并同步 Android 系统栏；支持横向滑动换页和减少动态效果偏好。

## 工具链

| 组件 | 版本 |
| --- | --- |
| Android Gradle Plugin | 9.4.0 |
| Gradle | 9.6.0 |
| Kotlin | 2.4.20 |
| AndroidX Core / Activity / WebKit | 1.19.0 / 1.13.0 / 1.17.0 |
| OkHttp | 5.3.0 |
| Vite（开发依赖） | 8.3.0 |

## 已知边界

- 完整 WebView 录音依赖页面活跃状态，后台切换或锁屏可能冻结 JavaScript 与音频回调。
- 当前开发机没有已连接设备或模拟器，麦克风授权、系统文件选择和真实百炼连接仍需在用户设备执行集成测试。
- 自动测试环境没有百炼 API Key，因此没有发送真实音频。
- Debug APK 供安装验证；应用商店发布仍需正式签名、真机回归和隐私文案确认。
