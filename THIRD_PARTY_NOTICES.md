# 第三方组件说明

本项目源码采用 MIT License。安装时会从 PyPI 获取下列运行依赖：

| 组件 | 用途 | 许可证 |
| --- | --- | --- |
| PySide6 / Qt for Python | 桌面界面 | LGPLv3 / GPLv3 / 商业许可（Qt 模块各自声明为准） |
| DashScope Python SDK | 百炼 API 客户端 | Apache-2.0 |
| sounddevice | PortAudio 录音绑定 | MIT |
| NumPy | 音量计算 | BSD-3-Clause |
| keyring | Windows 凭据存储 | MIT |
| pytest / pytest-cov | 开发测试 | MIT |

FFmpeg 由用户独立安装或放入项目 `bin` 目录。其许可证取决于所用构建中启用的组件，分发时应同时检查对应构建的许可证信息。本仓库未捆绑 FFmpeg 二进制文件。

