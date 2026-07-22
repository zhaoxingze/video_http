$ErrorActionPreference = "Stop"

Set-Location -LiteralPath $PSScriptRoot

python -m PyInstaller `
  --noconfirm `
  --clean `
  --onefile `
  --windowed `
  --name VideoDownloaderApp `
  --icon ".\assets\app.ico" `
  --add-data ".\assets;assets" `
  --collect-all yt_dlp `
  --collect-data webview `
  --collect-binaries webview `
  --hidden-import webview.platforms.winforms `
  --hidden-import webview.platforms.edgechromium `
  --collect-binaries imageio_ffmpeg `
  --hidden-import imageio_ffmpeg `
  --exclude-module numpy `
  --exclude-module mkl `
  --exclude-module scipy `
  --exclude-module pandas `
  --exclude-module qtpy `
  --exclude-module PyQt5 `
  --exclude-module PySide6 `
  --exclude-module webview.platforms.qt `
  --exclude-module webview.platforms.gtk `
  --exclude-module webview.platforms.cocoa `
  --exclude-module webview.platforms.android `
  --exclude-module webview.platforms.cef `
  app_gui.py

if ($LASTEXITCODE -ne 0) {
  throw "PyInstaller failed with exit code $LASTEXITCODE."
}

Write-Host ""
Write-Host "Built app:"
Write-Host (Resolve-Path ".\dist\VideoDownloaderApp.exe")
