# JustSpeak

![JustSpeak Windows 客户端的主页、实时录音、文件转写、历史记录和设置界面](./desktop/assets/main.png)

JustSpeak 是一款开源语音转文字应用，可将麦克风录音及音视频文件转换为文字。项目使用用户自行配置的阿里云百炼账号完成语音识别，Windows 端还支持屏幕文字增强与大语言模型文本处理。

仓库包含 Windows 桌面客户端和 Android 客户端。两端各自保存配置与文稿，没有项目自建的音频中转服务。

## 平台与状态

| 平台 | 状态 | 技术栈 | 主要能力 |
| --- | --- | --- | --- |
| Windows 10/11（64 位） | 可用 | Python 3.12、PySide6 | 实时录音、音视频文件转写、屏幕 OCR 上下文、文本清洗与修复、任务恢复、历史统计 |
| Android 13+ | 开发测试中 | Kotlin、WebView、HTML/CSS/JavaScript | 实时录音、设备文件转写、暂停与继续、历史记录、TXT/WAV 导出、系统分享 |

两端均支持北京与新加坡地域，并通过本机安全存储保存用户选择持久化的 API Key。云端模型的可用性、费用与识别效果取决于用户账号、地域、模型和网络环境。

## 功能概览

- 实时显示识别结果，支持暂停、继续和结束。
- 转写本地音频或视频文件，保留历史文稿并提供导出能力。
- 使用术语、人名和产品名等上下文提高识别准确率。
- API Key 由 Windows 凭据管理器或 Android Keystore 保护。
- Windows 端可读取一次选定屏幕的 OCR 文字，作为本次实时识别上下文。
- Windows 端可在识别完成后执行可配置的文本清洗与定向修复。

## 仓库结构

```text
speakdown/
├─ android/                 # Android 工程与 WebView 前端
│  ├─ app/                  # Kotlin 薄壳及随 APK 分发的 Web 资源
│  ├─ gradle/               # Gradle Wrapper 与版本目录
│  └─ web/                  # Web UI 开发和自动检查工具
├─ desktop/                 # Windows 桌面客户端
│  ├─ src/asr_client/       # Python 应用源码
│  ├─ tests/                # 自动化测试
│  ├─ scripts/              # 启动、图标与长任务模拟脚本
│  └─ packaging/            # Windows 打包元数据
├─ LICENSE
└─ README.md
```

## 快速开始

### Windows 桌面端

准备 64 位 Python 3.12，以及可用的 FFmpeg 和 ffprobe。首次安装会创建 `desktop/.venv` 并安装开发依赖：

```powershell
cd .\desktop
powershell -ExecutionPolicy Bypass -File .\setup.ps1
.\start.bat
```

FFmpeg 未加入 `PATH` 时，可在应用设置页手动选择 `ffmpeg.exe`。完整配置、使用方式和便携版构建说明见 [Windows 桌面端文档](./desktop/README.md)。

### Android 端

准备 JDK 17 或 21，以及包含 Android SDK Platform 37 的 Android SDK。在 `android` 目录执行检查与调试包构建：

```powershell
cd .\android
.\gradlew.bat testDebugUnitTest lintDebug assembleDebug
```

生成的 APK 位于 `android/app/build/outputs/apk/debug/app-debug.apk`。Web UI 也可以单独在浏览器中调试：

```powershell
cd .\android\web
npm.cmd install
npm.cmd run dev
```

浏览器访问 `http://127.0.0.1:5173/`；该预览使用模拟云端。设备安装、WebView 调试和真机验收步骤见 [Android 客户端文档](./android/README_ANDROID.md)。

## 配置与数据

首次运行后，在“设置”中选择百炼服务地域、语言和模型，并填写对应地域可用的 API Key。Windows 端的 FFmpeg 路径、数据目录、模型链和 Prompt 也在此处配置。

- Windows 文稿、任务音频和 SQLite 数据默认保存在 `D:\ASR-Client\data`；该目录不可用时回退到 `%LOCALAPPDATA%\JustSpeak\data`。
- Android 文稿和 PCM 录音片段保存在应用私有的 WebView 数据目录，卸载应用会清除这些内容。
- 音频会发送到用户配置的百炼服务。启用 Windows 屏幕信息增强时，选定屏幕的 OCR 图像也会发送到用户配置的 OCR 接口。
- Windows 屏幕截图仅在内存中处理；Android Release 包关闭明文网络流量。
- 仓库不应包含 API Key、签名文件、运行数据库、任务音频或本机 SDK 路径；相关文件已由 `.gitignore` 排除。

## 开发与验证

Windows：

```powershell
cd .\desktop
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m compileall -q src tests scripts
```

Android 与 Web UI：

```powershell
cd .\android\web
npm.cmd run check

cd ..
.\gradlew.bat testDebugUnitTest lintDebug assembleDebug
```

`connectedDebugAndroidTest` 需要已连接的 Android 13+ 真机或模拟器。真实百炼识别还需要有效 API Key。

## 项目文档

| 文档 | 内容 |
| --- | --- |
| [Windows 桌面端说明](./desktop/README.md) | 安装、云端配置、页面功能、数据目录、测试与便携版构建 |
| [Android 客户端说明](./android/README_ANDROID.md) | APK 安装、本地构建、Web UI 调试、安全边界与使用限制 |
| [Android 实现说明](./android/IMPLEMENTATION_NOTES.md) | WebView 与 Kotlin 的职责划分、桥接接口和实现细节 |
| [Android 测试记录](./android/TEST_REPORT.md) | 自动检查、浏览器验收结果与待完成的真机验证 |

## 当前限制

- 当前云端识别仅接入阿里云百炼。
- Android 客户端需要保持前台运行，后台录音和文件转写暂未提供稳定性承诺。
- Windows 客户端暂不支持系统声音采集、说话人分离、翻译、SRT 字幕编辑和全局输入法注入。

## 许可证

项目源码采用 [MIT License](./LICENSE)。第三方组件与分发说明见 [Windows 第三方声明](./desktop/THIRD_PARTY_NOTICES.md) 和 [Android 第三方声明](./android/THIRD_PARTY_NOTICES_ANDROID.md)。
