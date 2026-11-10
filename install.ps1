# Install Pagewise on Windows 10/11.
#
#   irm https://raw.githubusercontent.com/Kernel-Hunter/rag-langgraph-lab/main/install.ps1 | iex
#
# A file downloaded by PowerShell carries no "downloaded from the internet" mark
# (Mark-of-the-Web), so SmartScreen does not stop the installer. It installs for
# the current user only (no admin rights) and adds Start menu and desktop shortcuts.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$api = 'https://api.github.com/repos/Kernel-Hunter/rag-langgraph-lab/releases/latest'
$release = Invoke-WebRequest -Uri $api -UseBasicParsing -Headers @{ Accept = 'application/vnd.github+json' } |
  Select-Object -ExpandProperty Content | ConvertFrom-Json
$asset = $release.assets | Where-Object { $_.name -like 'Pagewise-*-win-*.exe' } | Select-Object -First 1
if (-not $asset) { throw 'Could not find the Windows installer in the latest release.' }

$setup = Join-Path $env:TEMP $asset.name
Write-Host "==> Downloading Pagewise $($release.tag_name)..." -ForegroundColor Magenta
Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $setup -UseBasicParsing

# Check the installer against SHA256SUMS.txt from the same release.
$sumsAsset = $release.assets | Where-Object { $_.name -eq 'SHA256SUMS.txt' } | Select-Object -First 1
if (-not $sumsAsset) { Remove-Item $setup -Force; throw 'The release has no SHA256SUMS.txt; refusing to install an unchecked file.' }
$sums = (Invoke-WebRequest -Uri $sumsAsset.browser_download_url -UseBasicParsing).Content
if ($sums -is [byte[]]) { $sums = [System.Text.Encoding]::UTF8.GetString($sums) }
$line = ($sums -split "`n") | Where-Object { $_ -match ('\s\*?' + [regex]::Escape($asset.name) + '\s*$') } | Select-Object -First 1
$want = if ($line) { ($line -split '\s+')[0].ToLower() } else { '' }
$got = (Get-FileHash -Algorithm SHA256 -Path $setup).Hash.ToLower()
if (-not $want -or $want -ne $got) { Remove-Item $setup -Force; throw "Checksum mismatch for $($asset.name). Nothing was installed." }
Write-Host '==> Checksum OK' -ForegroundColor Magenta

Write-Host '==> Installing...' -ForegroundColor Magenta
# /S = NSIS silent install, per user, into %LOCALAPPDATA%\Programs\Pagewise.
$p = Start-Process -FilePath $setup -ArgumentList '/S' -Wait -PassThru
Remove-Item $setup -Force
if ($p.ExitCode -ne 0) { throw "The installer failed (exit code $($p.ExitCode))." }

$exe = Join-Path $env:LOCALAPPDATA 'Programs\Pagewise\Pagewise.exe'
Write-Host '==> Done. Starting Pagewise (also in the Start menu).' -ForegroundColor Magenta
Start-Process $exe
