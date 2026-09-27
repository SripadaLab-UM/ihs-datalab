# DataLab uninstaller for Windows.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File uninstall.ps1 [-DeleteData | -KeepData]
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
# start again after this. The names and checks match install.ps1.
$MySid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
foreach ($task in "DataLab setup for $MySid (continue after restart)", "DataLab setup (continue after restart)") {
    Unregister-ScheduledTask -TaskName $task -Confirm:$false -ErrorAction SilentlyContinue
}
Remove-Item (Join-Path ([Environment]::GetFolderPath("Startup")) "DataLab setup.lnk") -ErrorAction SilentlyContinue
foreach ($file in "installer-resume.json", "constraints.txt") {
    Remove-Item (Join-Path $Env:LOCALAPPDATA "DataLab\$file") -ErrorAction SilentlyContinue
}
Get-ChildItem -LiteralPath $Env:TEMP -File -Filter "DataLab-setup-*.ps1" -Force -ErrorAction SilentlyContinue |
    Where-Object { -not ($_.Attributes -band [System.IO.FileAttributes]::ReparsePoint) } |
    ForEach-Object { Remove-Item -LiteralPath $_.FullName -Force -ErrorAction SilentlyContinue }

# The administrator part's result and log, which it left for this account to
# remove. Copies of install.ps1's Remove-Tree and Test-AdminFolder: anyone can
# create folders in ProgramData, so only real folders (not links) that the
# administrator part made for this account are removed, without following links.
$AdminBase = [Environment]::GetFolderPath("CommonApplicationData")
$AdminFolderPrefix = "DataLab-setup-"
$AdminFolderPattern = "^" + [regex]::Escape((Join-Path $AdminBase $AdminFolderPrefix)) + "[0-9a-f]{32}$"
$SystemSid = "S-1-5-18"
$AdminsSid = "S-1-5-32-544"

function Remove-Tree($path) {
    foreach ($child in @(Get-ChildItem -LiteralPath $path -Force -ErrorAction SilentlyContinue)) {
        $isLink = [bool]($child.Attributes -band [System.IO.FileAttributes]::ReparsePoint)
        if ($child.PSIsContainer -and -not $isLink) { Remove-Tree $child.FullName }
        try {
            if ($child.PSIsContainer) { [System.IO.Directory]::Delete($child.FullName, $false) }
            else { [System.IO.File]::Delete($child.FullName) }
        } catch { Write-Verbose "Couldn't remove $($child.FullName): $_" }
    }
    try { [System.IO.Directory]::Delete($path, $false) } catch { Write-Verbose "Couldn't remove ${path}: $_" }
}

function Test-AdminFolder($item, $sid) {
    if (-not $item.PSIsContainer) { return $false }
    if ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) { return $false }
    if ($item.FullName -notmatch $AdminFolderPattern) { return $false }
    try { $acl = Get-Acl -LiteralPath $item.FullName } catch { return $false }
    $owner = $acl.GetOwner([System.Security.Principal.SecurityIdentifier]).Value
    if ($owner -notin @($SystemSid, $AdminsSid)) { return $false }
    $mine = @($acl.GetAccessRules($true, $false, [System.Security.Principal.SecurityIdentifier]) |
        Where-Object { $_.IdentityReference.Value -eq $sid })
    return $mine.Count -gt 0
}

foreach ($item in @(Get-ChildItem -LiteralPath $AdminBase -Force -Filter "$AdminFolderPrefix*" -ErrorAction SilentlyContinue)) {
    if (Test-AdminFolder $item $MySid) { Remove-Tree $item.FullName }
}

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
