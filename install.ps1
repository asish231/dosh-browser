# DOSH Windows Installer
# Usage:
#   irm https://raw.githubusercontent.com/asish231/dosh-browser/main/install.ps1 | iex

$ErrorActionPreference = 'Stop'

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "  DOSH — The Agent-Native Browser (Windows Installer)" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

$Repo = "asish231/dosh-browser"
$Target = "x86_64-pc-windows-msvc"
$InstallDir = Join-Path $HOME ".dosh\bin"
$ExePath = Join-Path $InstallDir "dosh.exe"

# 1. Determine Target
$Arch = $env:PROCESSOR_ARCHITECTURE
if ($Arch -ne "AMD64" -and $Arch -ne "ARM64") {
    Write-Warning "Unsupported Windows architecture: $Arch. Defaulting to x86_64."
}

# 2. Get latest release asset
Write-Host "[-] Locating latest release from GitHub..." -ForegroundColor Yellow
$ReleaseUrl = "https://api.github.com/repos/$Repo/releases/latest"
try {
    $Release = Invoke-RestMethod -Uri $ReleaseUrl -Headers @{ "User-Agent" = "dosh-installer" }
    $Tag = $Release.tag_name
    $Asset = $Release.assets | Where-Object { $_.name -like "*$Target.zip" } | Select-Object -First 1
    if (-not $Asset) {
        throw "Could not find $Target.zip asset in release $Tag"
    }
    $DownloadUrl = $Asset.browser_download_url
    Write-Host "[+] Found release $Tag" -ForegroundColor Green
} catch {
    Write-Warning "Failed to query GitHub API: $_"
    Write-Host "[-] Falling back to latest release direct URL..." -ForegroundColor Yellow
    $DownloadUrl = "https://github.com/$Repo/releases/latest/download/dosh-$Target.zip"
}

# 3. Create install directory
if (-not (Test-Path $InstallDir)) {
    New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
}

# 4. Download and extract
$TempZip = Join-Path $env:TEMP "dosh-$Target.zip"
$TempExtract = Join-Path $env:TEMP "dosh-extract-$([System.Guid]::NewGuid().ToString('N'))"

Write-Host "[-] Downloading dosh ($Target)..." -ForegroundColor Yellow
Invoke-WebRequest -Uri $DownloadUrl -OutFile $TempZip -UseBasicParsing

Write-Host "[-] Extracting binary..." -ForegroundColor Yellow
Expand-Archive -Path $TempZip -DestinationPath $TempExtract -Force
$ExtractedExe = Join-Path $TempExtract "dosh.exe"
if (-not (Test-Path $ExtractedExe)) {
    $Found = Get-ChildItem -Path $TempExtract -Filter "dosh.exe" -Recurse | Select-Object -First 1
    if ($Found) {
        $ExtractedExe = $Found.FullName
    } else {
        throw "dosh.exe was not found inside the downloaded archive."
    }
}

Copy-Item -Path $ExtractedExe -Destination $ExePath -Force
Remove-Item -Path $TempZip -Force -ErrorAction SilentlyContinue
Remove-Item -Path $TempExtract -Recurse -Force -ErrorAction SilentlyContinue

# 5. Add to PATH if not already present
$UserPath = [Environment]::GetEnvironmentVariable("Path", [EnvironmentVariableTarget]::User)
if ($UserPath -notlike "*$InstallDir*") {
    Write-Host "[-] Adding $InstallDir to user PATH..." -ForegroundColor Yellow
    [Environment]::SetEnvironmentVariable("Path", "$UserPath;$InstallDir", [EnvironmentVariableTarget]::User)
    $env:Path = "$env:Path;$InstallDir"
    Write-Host "[+] Added to PATH (restart terminal if dosh is not recognized in current session)" -ForegroundColor Green
}

# 6. Verify Installation
Write-Host "==========================================================" -ForegroundColor Green
Write-Host "  Installation Successful!" -ForegroundColor Green
Write-Host "  Binary located at: $ExePath" -ForegroundColor Green
Write-Host "==========================================================" -ForegroundColor Green

try {
    & $ExePath product
} catch {
    Write-Host "Run 'dosh product' to inspect browser lanes and engine status."
}
