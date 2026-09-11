# SpeakDown

SpeakDown 是一个面向 Windows 的免费开源桌面语音转文字客户端。录音、转码和任务数据留在本机，识别音频会发送到用户自己配置的阿里云百炼账号。客户端不包含本地 ASR 模型、PyTorch、CUDA 或模型权重。

## 最短使用步骤

1. 安装 64 位 [Python 3.12](https://www.python.org/downloads/windows/)，并安装 [FFmpeg](https://ffmpeg.org/download.html)。也可把 `ffmpeg.exe` 和 `ffprobe.exe` 放入项目 `bin` 目录。
2. 双击 `start.bat`。首次运行会自动调用 `setup.ps1` 创建环境并安装依赖。
3. 打开“设置”，填写百炼 API Key、Base URL 和 Model ID 后保存。
4. API Key、Base URL 所属 Workspace 及模型地域需要一致。
5. 实时使用时选择麦克风并点击“开始录音”；文件使用时拖入媒体、选择音轨并点击“开始转写”。

缺少 API Key 时，界面和本地流程仍可运行，识别结果带有“模拟转写”标记。设置页的“测试连接”会录制 4 秒并实际调用模型；请在安静环境下说一句话。

## 功能

- 麦克风连续录音、实时临时/最终文字、音量和时长显示，无 2 分钟或 60 分钟自动停止限制。
- 录音先持续写入标准 PCM，网络发送从已落盘位置读取；网络故障期间继续保存，并在停止后按缺口补转写。
- MP3、WAV、M4A、FLAC、MP4、MKV、MOV、WebM 等 FFmpeg 可解码媒体的音轨探测、选择和提取。
- 长文件转换为 16 kHz 单声道 16-bit PCM WAV，以 30～120 秒确定性分段串行提交；每块完成后立即写入 SQLite 和原始响应文件。
- 文件任务可暂停、继续、取消。应用异常退出后，处理中片段回到待处理状态，已完成片段不会重复提交。
- 历史文字可编辑、复制，并以 UTF-8 TXT 导出；原始识别段始终保留。

## 百炼配置

默认模型为 `qwen-audio-3.0-asr-flash-streaming`。语言可选中文 `zh`、英文 `en` 或自动。Base URL 留空时会根据地域与 Workspace ID 自动生成。填写完整 Base URL 后，只需再提供 API Key 和 Model ID。

实时模型使用以下 WebSocket 地址：

| 地域 | 公共地址 | Workspace 专属地址 |
| --- | --- | --- |
| 北京 | `wss://dashscope.aliyuncs.com/api-ws/v1/inference` | `wss://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/api-ws/v1/inference` |
| 新加坡 | `wss://dashscope-intl.aliyuncs.com/api-ws/v1/inference` | `wss://{WorkspaceId}.ap-southeast-1.maas.aliyuncs.com/api-ws/v1/inference` |

官方 SDK 的 `Recognition` 支持 `start()` / `send_audio_frame()` / `stop()` 流式入口和 `call(file)` 本地文件入口；约 100 ms 的音频包是官方建议。`heartbeat=true` 配合持续静音音频维持长静音连接。参考：[Python SDK](https://help.aliyun.com/zh/model-studio/fun-asr-realtime-python-sdk)、[客户端事件与参数](https://help.aliyun.com/zh/model-studio/fun-asr-client-events)、[模型列表](https://help.aliyun.com/zh/model-studio/asr-model/)。

`qwen-audio-3.0-asr-flash-streaming` 和 `fun-asr-realtime` 支持实时录音与本地文件。`qwen3-asr-flash` 和 `qwen-audio-3.0-asr-flash` 使用 HTTP，可用于本地文件转写；程序会自动切换调用协议。`fun-asr` 和带有 `filetrans` 的模型只接受公网音频 URL，当前本地文件流程会给出明确提示。

HTTP 模型的 Base URL 示例为 `https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/api/v1`。设置变更只影响新任务，任务启动时会保存配置快照。

## 数据与恢复

优先数据目录为 `D:\ASR-Client\data`。D 盘不可写时，本次启动回退到 `%LOCALAPPDATA%\SpeakDown\data` 并弹出提示，可在设置页重新选择。

```text
data/
  SpeakDown.sqlite3
  tasks/<会话 ID>/
    audio.wav / recording.pcm / recording.wav
    chunks/ 或 gaps/
    responses/
    manifest.json
    transcript.txt
```

日志位于 `%LOCALAPPDATA%\SpeakDown\logs\SpeakDown.log`。启动期异常会写入同目录的 `startup-error.log` 并弹窗显示日志位置。日志不记录 API Key、完整请求头或音频 Base64。API Key 勾选保存时进入 Windows 凭据存储，服务名为 `SpeakDown-ASR`；未勾选时只保留在当前进程内。

重启后在“历史记录”选择待继续的文件任务。导出的未完成文稿会保留 `[开始–结束 待转写]` 占位。远端超时后重试可能产生额外计费，应用会保存请求 ID 和尝试次数用于排查。

## 开发与测试

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m asr_client
.\.venv\Scripts\python.exe scripts\simulate_long_run.py --minutes 60
.\.venv\Scripts\python.exe scripts\simulate_long_file.py --minutes 60
```

依赖版本锁定在 `pyproject.toml`。升级依赖后需重新执行全部测试，并用短麦克风和短文件做真实 API 验证。详见 [TEST_REPORT.md](TEST_REPORT.md)。

## 当前限制

- 只接通阿里云百炼；不包含系统声音采集、说话人分离、翻译、润色、字幕编辑和 SRT 导出。
- 暂停在当前文件片段完成后生效。实时录音开始时，文件任务会请求暂停。
- 网络缺口在停止录音后补转写；鉴权、余额或模型配置错误会保留待处理状态。
- 云端识别速度、首字延迟和准确率由模型、账号地域和网络共同决定。
