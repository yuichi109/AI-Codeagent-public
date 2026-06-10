#requires -Version 5
<#
  install_prereqs.ps1 - Install required tools for AI Code Agent (Windows).
  Strategy per tool: try winget first; if winget fails (e.g. broken source on
  Windows Server: 0x8a15000f), fall back to downloading the official installer
  and installing it silently. Must run elevated (administrator).

  Usage:
    powershell -NoProfile -ExecutionPolicy Bypass -File install_prereqs.ps1 -Tools python,git,node
#>
param(
    [string]$Tools = "python,git,node"
)

$ErrorActionPreference = 'Continue'
try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch {}

# Pinned versions for the direct-download fallback (bump occasionally).
$PY_VER  = '3.12.10'
$GIT_PIN = 'https://github.com/git-for-windows/git/releases/download/v2.47.1.windows.1/Git-2.47.1-64-bit.exe'
$NODE_PIN_VER = 'v22.12.0'

$wantList = @($Tools -split '[,\s]+' | Where-Object { $_ })

function Have-Winget {
    return [bool](Get-Command winget -ErrorAction SilentlyContinue)
}

function Try-Winget([string]$id) {
    if (-not (Have-Winget)) { return $false }
    Write-Host "  [winget] $id"
    & winget install -e --id $id --source winget --silent --accept-package-agreements --accept-source-agreements 2>$null | Out-Null
    return ($LASTEXITCODE -eq 0)
}

function Download-File([string]$url, [string]$outFile) {
    Write-Host "  [download] $url"
    if (Test-Path $outFile) { Remove-Item $outFile -Force -ErrorAction SilentlyContinue }
    # Prefer BITS: shows a native progress bar and is fast (unlike IWR's slow bar).
    try {
        Import-Module BitsTransfer -ErrorAction Stop
        Start-BitsTransfer -Source $url -Destination $outFile -ErrorAction Stop -DisplayName 'AI Code Agent setup' -Description $url
        if (Test-Path $outFile) { return $true }
    } catch {
        Write-Host "  [download] BITS unavailable, falling back to direct download (no progress bar)..."
    }
    # Fallback: Invoke-WebRequest with progress bar suppressed (the bar makes IWR very slow).
    try {
        $old = $ProgressPreference
        $ProgressPreference = 'SilentlyContinue'
        Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $outFile -ErrorAction Stop
        $ProgressPreference = $old
        return (Test-Path $outFile)
    } catch {
        Write-Host "  [download] FAILED: $($_.Exception.Message)"
        return $false
    }
}

function Run-Wait([string]$file, [string[]]$argList) {
    $p = Start-Process -FilePath $file -ArgumentList $argList -Wait -PassThru
    return $p.ExitCode
}

function Install-Python {
    if (Try-Winget 'Python.Python.3.12') { return }
    Write-Host "  [fallback] downloading Python $PY_VER from python.org"
    $url = "https://www.python.org/ftp/python/$PY_VER/python-$PY_VER-amd64.exe"
    $out = Join-Path $env:TEMP "python-$PY_VER-amd64.exe"
    if (Download-File $url $out) {
        $rc = Run-Wait $out @('/quiet','InstallAllUsers=1','PrependPath=1','Include_test=0')
        Write-Host "  [python] installer exit=$rc"
    } else { Write-Host "  [python] download failed" }
}

function Install-Git {
    if (Try-Winget 'Git.Git') { return }
    Write-Host "  [fallback] downloading Git for Windows"
    $url = $GIT_PIN
    try {
        $rel = Invoke-RestMethod -UseBasicParsing -Uri 'https://api.github.com/repos/git-for-windows/git/releases/latest' -Headers @{ 'User-Agent' = 'aica-setup' }
        $asset = $rel.assets | Where-Object { $_.name -like '*-64-bit.exe' -and $_.name -notlike '*Portable*' } | Select-Object -First 1
        if ($asset) { $url = $asset.browser_download_url }
    } catch { Write-Host "  [git] GitHub API unavailable, using pinned version" }
    $out = Join-Path $env:TEMP 'git-setup-64.exe'
    if (Download-File $url $out) {
        $rc = Run-Wait $out @('/VERYSILENT','/NORESTART','/SUPPRESSMSGBOXES','/NOCANCEL','/SP-')
        Write-Host "  [git] installer exit=$rc"
    } else { Write-Host "  [git] download failed" }
}

function Install-Node {
    if (Try-Winget 'OpenJS.NodeJS.LTS') { return }
    Write-Host "  [fallback] downloading Node.js LTS"
    $ver = $NODE_PIN_VER
    try {
        $idx = Invoke-RestMethod -UseBasicParsing -Uri 'https://nodejs.org/dist/index.json'
        $lts = $idx | Where-Object { $_.lts } | Select-Object -First 1
        if ($lts) { $ver = $lts.version }
    } catch { Write-Host "  [node] version index unavailable, using pinned version" }
    $url = "https://nodejs.org/dist/$ver/node-$ver-x64.msi"
    $out = Join-Path $env:TEMP "node-$ver-x64.msi"
    if (Download-File $url $out) {
        $rc = Run-Wait 'msiexec.exe' @('/i', $out, '/quiet', '/norestart')
        Write-Host "  [node] installer exit=$rc"
    } else { Write-Host "  [node] download failed" }
}

Write-Host '============================================================'
Write-Host ' AI Code Agent - installing required tools'
Write-Host '============================================================'

# Best-effort winget source repair (harmless if winget is healthy / absent).
if (Have-Winget) {
    & winget source reset --force 2>$null | Out-Null
    & winget source update 2>$null | Out-Null
}

foreach ($t in $wantList) {
    switch ($t.ToLower()) {
        'python' { Write-Host "[install] Python";  Install-Python }
        'git'    { Write-Host "[install] Git";     Install-Git }
        'node'   { Write-Host "[install] Node.js"; Install-Node }
        default  { Write-Host "[skip] unknown tool: $t" }
    }
}

Write-Host ''
Write-Host 'Done. You can close this window.'
Read-Host 'Press Enter to close'
