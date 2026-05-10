<#
.SYNOPSIS
    Sync this repo onto the Jetson Nano over SSH (one-way: Windows -> Jetson).
.DESCRIPTION
    rsync if available (native or via WSL), else pack a tarball with the built-in
    tar.exe + scp + extract on the device. Result: jetson:~/jetson-vision-desk/
    mirrors this folder (minus .git, venvs, caches). Runtime state lives separately
    in ~/jetson-vision-desk-data/ and is never touched.
.PARAMETER Run    After syncing, launch  python3 -m src.main  on the Jetson.
.PARAMETER Clean  Delete ~/jetson-vision-desk on the Jetson before copying.
.PARAMETER RemoteHost  SSH host alias (default: jetson).
.EXAMPLE  ./deploy.ps1 -Run
#>
[CmdletBinding()]
param([switch]$Run, [switch]$Clean, [string]$RemoteHost = "jetson")

$ErrorActionPreference = "Stop"
$Repo      = $PSScriptRoot
$RemoteDir = "~/jetson-vision-desk"
$DataDir   = "~/jetson-vision-desk-data"
$Excludes  = @(".git", "__pycache__", "*.pyc", ".venv", "venv", "env", ".vscode", ".idea", ".claude", "data", "snapshots", "*.log")

Write-Host "==> Deploying $Repo  ->  ${RemoteHost}:$RemoteDir" -ForegroundColor Cyan

$rsyncCmd = $null
if (Get-Command rsync -ErrorAction SilentlyContinue) { $rsyncCmd = "rsync" }
elseif (Get-Command wsl.exe -ErrorAction SilentlyContinue) {
    & wsl.exe -e sh -c "command -v rsync" *> $null
    if ($LASTEXITCODE -eq 0) { $rsyncCmd = "wsl-rsync" }
}

if ($rsyncCmd) {
    $exArgs = $Excludes | ForEach-Object { "--exclude=$_" }
    $delete = if ($Clean) { @("--delete") } else { @() }
    if ($rsyncCmd -eq "wsl-rsync") {
        $wslPath = (& wsl.exe -e wslpath -a "$Repo").Trim()
        & wsl.exe -e rsync -az @delete @exArgs "$wslPath/" "${RemoteHost}:$RemoteDir/"
    } else {
        & rsync -az @delete @exArgs "$Repo/" "${RemoteHost}:$RemoteDir/"
    }
    if ($LASTEXITCODE -ne 0) { throw "rsync failed ($LASTEXITCODE)" }
} else {
    Write-Host "    (no rsync — using tar+scp; always a clean copy)" -ForegroundColor DarkGray
    $tar = Join-Path $env:SystemRoot "System32\tar.exe"
    if (-not (Test-Path $tar)) { throw "Need rsync or tar.exe; found neither." }
    $tmp = Join-Path $env:TEMP ("vision-deploy-{0}.tgz" -f ([guid]::NewGuid().ToString("N")))
    $exArgs = $Excludes | ForEach-Object { "--exclude=$_" }
    & $tar -czf $tmp -C $Repo @exArgs .
    if ($LASTEXITCODE -ne 0) { Remove-Item $tmp -ErrorAction SilentlyContinue; throw "tar failed ($LASTEXITCODE)" }
    try {
        & ssh $RemoteHost "rm -rf $RemoteDir && mkdir -p $RemoteDir $DataDir"
        & scp -q $tmp "${RemoteHost}:/tmp/vision-deploy.tgz"
        & ssh $RemoteHost "tar -xzf /tmp/vision-deploy.tgz -C $RemoteDir && rm -f /tmp/vision-deploy.tgz"
    } finally { Remove-Item $tmp -ErrorAction SilentlyContinue }
}

& ssh $RemoteHost "mkdir -p $DataDir"
Write-Host "==> Synced." -ForegroundColor Green

if ($Run) {
    Write-Host "==> Running  python3 -m src.main  on $RemoteHost (Ctrl+C to stop)" -ForegroundColor Cyan
    & ssh -t $RemoteHost "cd $RemoteDir && python3 -m src.main"
}
