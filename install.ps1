# Install RAG Help Desk on Windows 10/11.
#
#   irm https://raw.githubusercontent.com/Kernel-Hunter/rag-langgraph-lab/main/install.ps1 | iex
#
# A file downloaded by PowerShell carries no "downloaded from the internet" mark,
# so SmartScreen does not stop the installer. It installs for the current user only
# (no admin rights) and adds Start menu and desktop shortcuts.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$url = 'https://github.com/Kernel-Hunter/rag-langgraph-lab/releases/latest/download/RAG-Help-Desk-Setup.exe'
$setup = Join-Path $env:TEMP 'RAG-Help-Desk-Setup.exe'

Write-Host '==> Downloading RAG Help Desk...' -ForegroundColor Magenta
Invoke-WebRequest -Uri $url -OutFile $setup -UseBasicParsing
Unblock-File $setup

Write-Host '==> Installing...' -ForegroundColor Magenta
Start-Process -FilePath $setup -ArgumentList '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/TASKS=desktopicon' -Wait
Remove-Item $setup -Force

$exe = Join-Path $env:LOCALAPPDATA 'Programs\RAG Help Desk\RAG-Help-Desk.exe'
Write-Host '==> Done. Starting RAG Help Desk (also in the Start menu).' -ForegroundColor Magenta
Start-Process $exe
