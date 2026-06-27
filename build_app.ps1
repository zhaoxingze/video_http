$ErrorActionPreference = "Stop"

Set-Location -LiteralPath $PSScriptRoot

python -m PyInstaller `
  --noconfirm `
  --onefile `
  --windowed `
  --name VideoDownloaderApp `
  --icon ".\assets\app.ico" `
  --add-data ".\assets;assets" `
  --collect-all yt_dlp `
  --collect-binaries imageio_ffmpeg `
  --hidden-import imageio_ffmpeg `
  app_gui.py

if ($LASTEXITCODE -ne 0) {
  throw "PyInstaller failed with exit code $LASTEXITCODE."
}

Write-Host ""
Write-Host "Built app:"
Write-Host (Resolve-Path ".\dist\VideoDownloaderApp.exe")
