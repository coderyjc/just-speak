$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$PythonLauncher = Get-Command py -ErrorAction SilentlyContinue

if (-not $PythonLauncher) {
    Write-Host "未检测到 Python Launcher。请先安装 64 位 Python 3.12："
    Write-Host "https://www.python.org/downloads/windows/"
    Write-Host "安装时请勾选 Install launcher for all users。"
    exit 1
}

Set-Location -LiteralPath $ProjectDir
Write-Host "正在创建 Python 3.12 独立环境..."
& py -3.12 -m venv .venv
if ($LASTEXITCODE -ne 0) {
    Write-Host "创建环境失败，请确认 Python 3.12 已安装。"
    exit $LASTEXITCODE
}

$VenvPython = Join-Path $ProjectDir ".venv\Scripts\python.exe"
& $VenvPython -m pip install --upgrade pip
& $VenvPython -m pip install -e ".[dev]"
if ($LASTEXITCODE -ne 0) {
    Write-Host "依赖安装失败，请检查网络后重新运行本脚本。"
    exit $LASTEXITCODE
}

Write-Host ""
Write-Host "安装完成。双击 start.bat 即可启动。"
Write-Host "媒体文件功能还需要 FFmpeg；启动后可在设置页选择 ffmpeg.exe。"

