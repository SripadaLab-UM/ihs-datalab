# DataLab uninstaller for Windows.
#
#   powershell -ExecutionPolicy Bypass -File uninstall.ps1 [-DeleteData | -KeepData]
#
# Removes DataLab, its Start menu entry, its container images, and the keys it
# saved in Credential Manager, and what the installer left behind (its
# after-restart task or shortcut, and its progress files). It asks before
# deleting DataLab's data folder (conversations and query results). It never
# touches your export folders, uv, WSL or Docker Desktop.
param([switch]$DeleteData, [switch]$KeepData)
$ErrorActionPreference = "Stop"
$Env:Path = (Join-Path $Env:USERPROFILE ".local\bin") + ";$Env:Path"

# The installer's leftovers, first, so an install waiting for a restart can't
# start again after this. The names match install.ps1.
Unregister-ScheduledTask -TaskName "DataLab setup (continue after restart)" -Confirm:$false -ErrorAction SilentlyContinue
Remove-Item (Join-Path ([Environment]::GetFolderPath("Startup")) "DataLab setup.lnk") -ErrorAction SilentlyContinue
foreach ($file in "installer-resume.json", "constraints.txt") {
    Remove-Item (Join-Path $Env:LOCALAPPDATA "DataLab\$file") -ErrorAction SilentlyContinue
}
# The administrator part's result and log, which it left for this account to
# remove, and the copy of the installer it read.
Get-ChildItem -LiteralPath $Env:ProgramData -Directory -Filter "DataLab-setup-*" -ErrorAction SilentlyContinue |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item (Join-Path $Env:TEMP "DataLab-setup-*.ps1") -ErrorAction SilentlyContinue

if (Get-Command uv -ErrorAction SilentlyContinue) {
    $DataLab = Join-Path (uv tool dir --bin) "datalab.exe"
    if (Test-Path $DataLab) {
        $choice = @()
        if ($DeleteData) { $choice = @("--delete-data") } elseif ($KeepData) { $choice = @("--keep-data") }
        & $DataLab uninstall @choice
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        uv tool uninstall datalab
    }
}
$Link = Join-Path $Env:APPDATA "Microsoft\Windows\Start Menu\Programs\DataLab.lnk"
if (Test-Path $Link) { Remove-Item $Link }
Write-Host "DataLab has been removed."
