from __future__ import annotations

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files


project_root = Path(SPEC).resolve().parent
ffmpeg_dir = Path(os.environ.get("JUSTSPEAK_FFMPEG_DIR", ""))
ffmpeg = ffmpeg_dir / "ffmpeg.exe"
ffprobe = ffmpeg_dir / "ffprobe.exe"
if not ffmpeg.is_file() or not ffprobe.is_file():
    raise SystemExit(
        "JUSTSPEAK_FFMPEG_DIR must contain ffmpeg.exe and ffprobe.exe"
    )

datas = collect_data_files("asr_client")
binaries = [(str(ffmpeg), "bin"), (str(ffprobe), "bin")]
hiddenimports = []
for package in ("dashscope", "keyring"):
    package_datas, package_binaries, package_hiddenimports = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hiddenimports

for filename in ("README.md", "LICENSE", "THIRD_PARTY_NOTICES.md"):
    datas.append((str(project_root / filename), "."))
ffmpeg_license = ffmpeg_dir.parent / "LICENSE"
ffmpeg_readme = ffmpeg_dir.parent / "README.txt"
if ffmpeg_license.is_file():
    datas.append((str(ffmpeg_license), "licenses/ffmpeg"))
if ffmpeg_readme.is_file():
    datas.append((str(ffmpeg_readme), "licenses/ffmpeg"))

analysis = Analysis(
    [str(project_root / "scripts" / "launch.py")],
    pathex=[str(project_root / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "pytest_cov"],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="JustSpeak",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(project_root / "build" / "justspeak.ico"),
    version=str(project_root / "packaging" / "version_info.txt"),
)

bundle = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="JustSpeak",
)
