# DataLab uninstaller for Windows.
#
#   powershell -ExecutionPolicy Bypass -File uninstall.ps1 [-DeleteData | -KeepData]
#
# Removes DataLab (every version installed side by side), its Start menu entry,
# its container images, and the keys it saved in Credential Manager. It asks
# before deleting DataLab's data folder (conversations and query results). It
# never touches your export folders, uv, or Docker Desktop.
#
# UNTESTED on a real Windows machine since versions went side by side.
param([switch]$DeleteData, [switch]$KeepData)
$ErrorActionPreference = "Stop"
$Env:Path = (Join-Path $Env:USERPROFILE ".local\bin") + ";$Env:Path"
$Root = if ($Env:DATALAB_INSTALL_DIR) { $Env:DATALAB_INSTALL_DIR } else { Join-Path $Env:LOCALAPPDATA "DataLab\app" }
$choice = @()
if ($DeleteData) { $choice = @("--delete-data") } elseif ($KeepData) { $choice = @("--keep-data") }

$Shim = Join-Path $Root "bin\datalab.cmd"
if ((Test-Path $Shim) -and (Test-Path (Join-Path $Root "current"))) {
    & $Shim uninstall @choice
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    Remove-Item -LiteralPath $Root -Recurse -Force
} elseif (Get-Command uv -ErrorAction SilentlyContinue) {
    # Installed by an installer from before versions went side by side.
    $DataLab = Join-Path (uv tool dir --bin) "datalab.exe"
    if (Test-Path $DataLab) {
        & $DataLab uninstall @choice
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }
}
if ((Get-Command uv -ErrorAction SilentlyContinue) -and ((uv tool list 2>$null) -match '^datalab ')) {
    uv tool uninstall datalab
}
$Link = Join-Path $Env:APPDATA "Microsoft\Windows\Start Menu\Programs\DataLab.lnk"
if (Test-Path $Link) { Remove-Item $Link }
Write-Host "DataLab has been removed."
