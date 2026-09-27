# DataLab installer for Windows.
#
#   powershell -ExecutionPolicy Bypass -File install.ps1 -Package <datalab .whl file or URL> [-Settings <lab settings file>]
#       [-Constraints <constraints.txt>] [-Practice] [-NoGitHub]
#
# constraints.txt holds the exact tested dependency versions; it's found
# automatically if it sits next to a local package file.
#
# What it does:
#   1. Checks that Docker Desktop is installed and running.
#   2. Installs uv (a Python installer) for you, if it isn't there already.
#   3. Installs DataLab, with its own Python, in your user account (no admin rights).
#      Each version gets its own folder, so an update installs beside the one in use
#      and the previous version is kept (docs/DISTRIBUTION.md).
#   4. Downloads the pinned container images.
#   5. Saves the lab's settings and asks for your U-M GPT key and database password,
#      which go into Windows Credential Manager.
#   6. Offers the GitHub sign-in for the lab's knowledge base and pipelines, then
#      downloads both. Skipped for the practice profile.
#   7. Adds DataLab to the Start menu.
#
# UNTESTED on a real Windows machine since versions went side by side (steps 3,
# 6 and the Start menu entry); CI only checks that it parses.
param(
    [Parameter(Mandatory = $true)][string]$Package,
    [string]$Settings = "",
    [string]$Constraints = "",
    [switch]$Practice,
    [switch]$NoGitHub
)
$ErrorActionPreference = "Stop"
# (Not $Profile: PowerShell uses that name for its own profile script.)
$DataLabProfile = if ($Practice) { "practice" } else { "real" }
$UvVersion = "0.12.19"

function Step($text) { Write-Host "`n$text" -ForegroundColor Cyan }

Step "1/7 Docker Desktop"
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Host "Docker Desktop isn't installed. Install it from the page that's opening,"
    Write-Host "start it once, then run this installer again."
    Start-Process "https://www.docker.com/products/docker-desktop/"
    exit 1
}
docker info *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Starting Docker Desktop..."
    $desktop = Join-Path $Env:ProgramFiles "Docker\Docker\Docker Desktop.exe"
    if (Test-Path $desktop) { Start-Process $desktop }
    $waited = 0
    do {
        Start-Sleep -Seconds 2; $waited += 2
        docker info *> $null
    } until ($LASTEXITCODE -eq 0 -or $waited -ge 180)
    if ($LASTEXITCODE -ne 0) { Write-Host "Docker Desktop didn't start. Start it, then run this again."; exit 1 }
}
Write-Host "Docker Desktop is running."

Step "2/7 uv"
$LocalBin = Join-Path $Env:USERPROFILE ".local\bin"
$Env:Path = "$LocalBin;$Env:Path"
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    powershell -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/$UvVersion/install.ps1 | iex"
}
uv --version

Step "3/7 DataLab"
$IsUrl = $Package -match '^[a-zA-Z][a-zA-Z0-9+.-]*://'
if (-not $IsUrl) {
    # A bare file name ("datalab-....whl") has no parent folder of its own, so
    # resolve it first: constraints.txt is looked for next to the real file.
    if (-not (Test-Path -LiteralPath $Package -PathType Leaf)) { Write-Host "Package file not found: $Package"; exit 2 }
    $Package = (Resolve-Path -LiteralPath $Package).ProviderPath
}
if (-not $Constraints -and -not $IsUrl) {
    $Beside = Join-Path ([System.IO.Path]::GetDirectoryName($Package)) "constraints.txt"
    if (Test-Path -LiteralPath $Beside) { $Constraints = $Beside }
}
if (-not $Constraints) { Write-Host "constraints.txt (the tested dependency versions) wasn't found. Pass -Constraints."; exit 2 }
# The package's file name carries its version: datalab-<version>-py3-none-any.whl
$FileName = [System.IO.Path]::GetFileName(($Package -split '\?')[0])
if ($FileName -notmatch '^datalab-([A-Za-z0-9.+!]+)-py3-none-any\.whl$') {
    Write-Host "The package must be a datalab-<version>-py3-none-any.whl file."; exit 2
}
$Version = $Matches[1]
# Beside the data folders: versions\<version>\, current, previous, bin\datalab.cmd.
$Root = if ($Env:DATALAB_INSTALL_DIR) { $Env:DATALAB_INSTALL_DIR } else { Join-Path $Env:LOCALAPPDATA "DataLab\app" }
$Target = Join-Path $Root "versions\$Version"
if (Test-Path -LiteralPath (Join-Path $Target ".complete")) {
    Write-Host "DataLab $Version is installed already."
} else {
    # A folder without .complete is an install that was cut off: start it again.
    if (Test-Path -LiteralPath $Target) { Remove-Item -LiteralPath $Target -Recurse -Force }
    New-Item -ItemType Directory -Force -Path (Join-Path $Root "versions") | Out-Null
    uv venv -q --python 3.13 $Target
    if ($LASTEXITCODE -ne 0) { exit 1 }
    uv pip install -q --python (Join-Path $Target "Scripts\python.exe") --constraints $Constraints $Package
    if ($LASTEXITCODE -ne 0) { Remove-Item -LiteralPath $Target -Recurse -Force; exit 1 }
    $Said = & (Join-Path $Target "Scripts\datalab.exe") --version
    if ($Said -ne "datalab $Version") {
        Write-Host "The installed DataLab says '$Said', not $Version."
        Remove-Item -LiteralPath $Target -Recurse -Force; exit 1
    }
    $Stamp = Get-Date -Format "yyyy-MM-ddTHH:mm:sszzz"
    Set-Content -LiteralPath (Join-Path $Target ".complete") -Value "{`"version`": `"$Version`", `"installed_at`": `"$Stamp`"}" -Encoding ASCII
}
# The launcher's command runs whichever version `current` names.
$Bin = Join-Path $Root "bin"
New-Item -ItemType Directory -Force -Path $Bin | Out-Null
$Shim = @'
@echo off
rem Runs the DataLab version the launcher opens (named in ..\current).
setlocal
set /p DATALAB_VERSION=<"%~dp0..\current"
"%~dp0..\versions\%DATALAB_VERSION%\Scripts\datalab.exe" %*
exit /b %ERRORLEVEL%
'@
Set-Content -LiteralPath (Join-Path $Bin "datalab.cmd") -Value $Shim -Encoding ASCII
$CurrentFile = Join-Path $Root "current"
$Old = if (Test-Path -LiteralPath $CurrentFile) { (Get-Content -LiteralPath $CurrentFile -TotalCount 1).Trim() } else { "" }
if ($Old -and $Old -ne $Version) { Set-Content -LiteralPath (Join-Path $Root "previous") -Value $Old -Encoding ASCII }
Set-Content -LiteralPath $CurrentFile -Value $Version -Encoding ASCII
$DataLab = Join-Path $Bin "datalab.cmd"
& $DataLab --version

Step "4/7 Container images"
& $DataLab --profile $DataLabProfile pull-images
if ($LASTEXITCODE -ne 0) { exit 1 }

Step "5/7 Settings and keys"
if ($Settings) { & $DataLab --profile $DataLabProfile setup --settings $Settings } else { & $DataLab --profile $DataLabProfile setup }

Step "6/7 The lab's knowledge base and pipelines"
if ($Practice) {
    Write-Host "Skipped: practice DataLab doesn't use the lab's repositories."
} elseif ($NoGitHub) {
    Write-Host "Skipped. Sign in later in DataLab, under Settings > GitHub."
} else {
    $Answer = Read-Host "Sign in to GitHub now, to download them? [Y/n]"
    if ($Answer -match '^[nN]') {
        Write-Host "Skipped. Sign in later in DataLab, under Settings > GitHub."
    } else {
        # 2: not set up here (the lab's settings don't name the repos); it says so.
        # 1: not signed in, or no access to a repo; it says whom to ask.
        & $DataLab --profile $DataLabProfile github sign-in
        if ($LASTEXITCODE -ne 2) {
            & $DataLab --profile $DataLabProfile repos sync
            if ($LASTEXITCODE -ne 0) { Write-Host "You can sync again later in DataLab (Knowledge and Pipelines)." }
        }
    }
}

Step "7/7 Launcher"
# DataLab runs in a PowerShell window, so it's easy to see it's running and to
# quit (close the window or press Ctrl-C). It opens the version `current`
# names, which is what an update switches.
$StartMenu = Join-Path $Env:APPDATA "Microsoft\Windows\Start Menu\Programs"
$Shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $StartMenu "DataLab.lnk"))
$Shortcut.TargetPath = "powershell.exe"
$Shortcut.Arguments = "-NoExit -Command `"& '$DataLab' --profile $DataLabProfile serve`""
$Shortcut.Description = "IHS DataLab"
$Shortcut.Save()
Write-Host "Added DataLab to the Start menu."

Step "Done"
Write-Host "Open DataLab from the Start menu (or run: `"$DataLab`" serve)."
