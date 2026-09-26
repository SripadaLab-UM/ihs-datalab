# DataLab installer for Windows.
#
#   powershell -ExecutionPolicy Bypass -File install.ps1 -Package <datalab .whl file or URL> [-Settings <lab settings file>]
#       [-Constraints <constraints.txt>]
#
# constraints.txt holds the exact tested dependency versions; it's found
# automatically if it sits next to a local package file.
#
# What it does:
#   1. Checks that Docker Desktop is installed and running.
#   2. Installs uv (a Python installer) for you, if it isn't there already.
#   3. Installs DataLab, with its own Python, in your user account (no admin rights).
#   4. Downloads the pinned container images.
#   5. Saves the lab's settings and asks for your U-M GPT key and database password,
#      which go into Windows Credential Manager.
#   6. Adds DataLab to the Start menu.
param(
    [Parameter(Mandatory = $true)][string]$Package,
    [string]$Settings = "",
    [string]$Constraints = ""
)
$ErrorActionPreference = "Stop"
$UvVersion = "0.12.19"

function Step($text) { Write-Host "`n$text" -ForegroundColor Cyan }

Step "1/6 Docker Desktop"
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

Step "2/6 uv"
$LocalBin = Join-Path $Env:USERPROFILE ".local\bin"
$Env:Path = "$LocalBin;$Env:Path"
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    powershell -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/$UvVersion/install.ps1 | iex"
}
uv --version

Step "3/6 DataLab"
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
uv tool install --force --python 3.13 --constraints $Constraints $Package
if ($LASTEXITCODE -ne 0) { exit 1 }
$DataLab = Join-Path (uv tool dir --bin) "datalab.exe"
& $DataLab --version

Step "4/6 Container images"
& $DataLab pull-images
if ($LASTEXITCODE -ne 0) { exit 1 }

Step "5/6 Settings and keys"
if ($Settings) { & $DataLab setup --settings $Settings } else { & $DataLab setup }

Step "6/6 Launcher"
# DataLab runs in a PowerShell window, so it's easy to see it's running and to
# quit (close the window or press Ctrl-C).
$StartMenu = Join-Path $Env:APPDATA "Microsoft\Windows\Start Menu\Programs"
$Shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $StartMenu "DataLab.lnk"))
$Shortcut.TargetPath = "powershell.exe"
$Shortcut.Arguments = "-NoExit -Command `"& '$DataLab' serve`""
$Shortcut.Description = "IHS DataLab"
$Shortcut.Save()
Write-Host "Added DataLab to the Start menu."

Step "Done"
Write-Host "Open DataLab from the Start menu (or run: `"$DataLab`" serve)."
