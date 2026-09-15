param(
    [string]$FFmpegDir = ""
)

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonExe = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $pythonExe -PathType Leaf)) {
    Write-Host "Project Python environment not found. Run setup.ps1 first."
    exit 1
}

if (-not $FFmpegDir) {
    $projectBin = Join-Path $projectRoot "bin"
    if (Test-Path -LiteralPath (Join-Path $projectBin "ffmpeg.exe")) {
        $FFmpegDir = $projectBin
    }
}
if (-not $FFmpegDir) {
    $localConfig = Join-Path ([Environment]::GetFolderPath("LocalApplicationData")) "JustSpeak\config.json"
    if (Test-Path -LiteralPath $localConfig -PathType Leaf) {
        try {
            $configuredPath = (Get-Content -Raw -LiteralPath $localConfig | ConvertFrom-Json).ffmpeg_path
            if ($configuredPath) {
                $configuredItem = Get-Item -LiteralPath $configuredPath -ErrorAction SilentlyContinue
                if ($configuredItem) {
                    $FFmpegDir = if ($configuredItem.PSIsContainer) {
                        $configuredItem.FullName
                    } else {
                        $configuredItem.DirectoryName
                    }
                }
            }
        } catch {
            Write-Host "Unable to read the current FFmpeg setting. Checking PATH."
        }
    }
}
if (-not $FFmpegDir) {
    $ffmpegCommand = Get-Command ffmpeg.exe -ErrorAction SilentlyContinue
    if ($ffmpegCommand) {
        $FFmpegDir = Split-Path -Parent $ffmpegCommand.Source
    }
}
if (-not $FFmpegDir) {
    Write-Host "FFmpeg was not found. Pass -FFmpegDir with ffmpeg.exe and ffprobe.exe."
    exit 1
}

$resolvedFFmpegDir = (Resolve-Path -LiteralPath $FFmpegDir).Path
foreach ($toolName in ("ffmpeg.exe", "ffprobe.exe")) {
    if (-not (Test-Path -LiteralPath (Join-Path $resolvedFFmpegDir $toolName) -PathType Leaf)) {
        Write-Host "FFmpeg directory is missing ${toolName}: $resolvedFFmpegDir"
        exit 1
    }
}

Set-Location -LiteralPath $projectRoot
& $pythonExe -c "import PyInstaller" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Installing packaging dependencies..."
    & $pythonExe -m pip install -e ".[packaging]"
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Unable to install packaging dependencies."
        exit $LASTEXITCODE
    }
}

& $pythonExe scripts\generate_icon.py src\asr_client\ui\assets\justspeak.svg build\justspeak.ico
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

$env:JUSTSPEAK_FFMPEG_DIR = $resolvedFFmpegDir
Write-Host "Building JustSpeak.exe..."
& $pythonExe -m PyInstaller --noconfirm --clean JustSpeak.spec
if ($LASTEXITCODE -ne 0) {
    Write-Host "Build failed. Review the output above."
    exit $LASTEXITCODE
}

$outputExe = Join-Path $projectRoot "dist\JustSpeak\JustSpeak.exe"
Write-Host ""
Write-Host "Build completed: $outputExe"
Write-Host "Copy the entire dist\JustSpeak directory when distributing the app."
