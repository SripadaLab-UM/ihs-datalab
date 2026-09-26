# DataLab uninstaller for Windows.
#
#   powershell -ExecutionPolicy Bypass -File uninstall.ps1 [-DeleteData | -KeepData]
#
# Removes DataLab, its Start menu entry, its container images, and the keys it
# saved in Credential Manager. It asks before deleting DataLab's data folder
# (conversations and query results). It never touches your export folders, uv,
# or Docker Desktop.
param([switch]$DeleteData, [switch]$KeepData)
$ErrorActionPreference = "Stop"
$Env:Path = (Join-Path $Env:USERPROFILE ".local\bin") + ";$Env:Path"

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
