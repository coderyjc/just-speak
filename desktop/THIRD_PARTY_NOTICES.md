# Third-party notices

JustSpeak 源码采用 MIT License。项目依赖的第三方组件保留各自的许可证、版权和商标权利。

## 主要直接依赖

| 组件 | 用途 | 许可证 |
| --- | --- | --- |
| [PySide6 / Qt for Python](https://doc.qt.io/qtforpython-6/) | Windows 桌面界面 | LGPL-3.0 / GPL-3.0 / 商业许可，具体以所用 Qt 模块为准 |
| [DashScope Python SDK](https://github.com/aliyun/dashscope-sdk-python) | 阿里云百炼 API 客户端 | Apache-2.0 |
| [sounddevice](https://python-sounddevice.readthedocs.io/) | PortAudio 录音绑定 | MIT |
| [NumPy](https://numpy.org/) | 音频数据与音量计算 | BSD-3-Clause |
| [keyring](https://github.com/jaraco/keyring) | Windows 凭据存储 | MIT |
| [PyInstaller](https://pyinstaller.org/) | Windows 便携版构建 | GPL-2.0-or-later with Bootloader Exception |
| [pytest / pytest-cov](https://pytest.org/) | 自动化测试与覆盖率 | MIT |

## FFmpeg

源码仓库不包含 FFmpeg 二进制文件。用户可以自行安装 FFmpeg，或将 `ffmpeg.exe` 与 `ffprobe.exe` 放入项目 `bin` 目录。

`build_exe.ps1` 会把构建者本机选择的 FFmpeg 复制到便携包。生成目录同时包含该 FFmpeg 构建附带的 `LICENSE` 和 `README.txt`，位置为 `licenses/ffmpeg`。FFmpeg 的许可证取决于构建时启用的组件；公开发布便携包前，应检查所选构建及其许可证是否允许当前分发方式。

## 发布说明

PyInstaller 会将运行依赖复制到 `dist/JustSpeak/_internal`。发布者应保留其中随依赖提供的许可证与元数据，并在升级依赖或替换 FFmpeg 构建后重新核对本文件。
