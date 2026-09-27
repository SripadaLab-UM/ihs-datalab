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
# remove: only the exact folders the installer recorded, never a pattern
# (anyone can create folders in ProgramData). Names and checks match install.ps1.
$AdminBase = [Environment]::GetFolderPath("CommonApplicationData")
$AdminFolderPrefix = "DataLab-setup-"
$AdminFolderPattern = "^" + [regex]::Escape((Join-Path $AdminBase $AdminFolderPrefix)) + "[0-9a-f]{32}$"
$AdminRecord = Join-Path $Env:LOCALAPPDATA "DataLab\installer-admin-folder.txt"
$SystemSid = "S-1-5-18"
$AdminsSid = "S-1-5-32-544"
$OwnerRightsSid = "S-1-3-4"

# --- Shared by install.ps1 and uninstall.ps1 (keep both copies the same) ----
# Removing what an administrator part left for this account. ProgramData is
# shared by every account and anyone can create folders in it, so nothing
# there is removed by name or pattern: only the exact folders this account's
# own installer recorded in $AdminRecord (under %LOCALAPPDATA%, which other
# accounts can't write), and only after they check out.

# Deletes a folder and what's in it without following links: a link inside is
# removed as a link, never what it points to, and a folder that is itself a
# link is left alone. Whatever can't be deleted stays.
function Remove-Tree($path) {
    $top = Get-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue
    if (-not $top -or -not $top.PSIsContainer -or ($top.Attributes -band [System.IO.FileAttributes]::ReparsePoint)) { return }
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

# Whether $path is a real folder (read fresh, not a link) owned by SYSTEM or
# Administrators.
function Test-AdminOwned($path) {
    $item = Get-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue
    if (-not $item -or -not $item.PSIsContainer -or ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint)) { return $false }
    try { $owner = (Get-Acl -LiteralPath $path).GetOwner([System.Security.Principal.SecurityIdentifier]).Value } catch { return $false }
    return $owner -in @($SystemSid, $AdminsSid)
}

# Whether $path is exactly what an administrator part leaves for the account
# $sid: the installer's name for it, a real folder owned by SYSTEM or
# Administrators, inheritance off, and no permissions but the ones it sets
# (SYSTEM and Administrators: full control; OWNER RIGHTS: read permissions;
# $sid: list, read attributes, read permissions and delete, on the folder
# only). No Deny rules. A profile folder, say, fails this even if another
# account manages to put a link to it where the folder was.
function Test-AdminFolder($path, $sid) {
    if ($path -notmatch $AdminFolderPattern -or -not (Test-AdminOwned $path)) { return $false }
    try { $acl = Get-Acl -LiteralPath $path } catch { return $false }
    if (-not $acl.AreAccessRulesProtected) { return $false }
    $fsr = [System.Security.AccessControl.FileSystemRights]
    $sync = [int]$fsr::Synchronize  # Windows adds it to every Allow rule
    $expected = @{
        $SystemSid = [int]$fsr::FullControl
        $AdminsSid = [int]$fsr::FullControl
        $OwnerRightsSid = [int]$fsr::ReadPermissions
    }
    $personRights = [int]($fsr::ListDirectory -bor $fsr::ReadAttributes -bor $fsr::ReadPermissions -bor $fsr::Delete)
    $sawPerson = $false
    foreach ($rule in @($acl.GetAccessRules($true, $true, [System.Security.Principal.SecurityIdentifier]))) {
        if ($rule.IsInherited -or "$($rule.AccessControlType)" -ne "Allow") { return $false }
        $who = $rule.IdentityReference.Value
        $rights = [int]$rule.FileSystemRights -bor $sync
        if ($who -eq $sid) {
            if ($rights -ne ($personRights -bor $sync) -or "$($rule.InheritanceFlags)" -ne "None" -or
                "$($rule.PropagationFlags)" -ne "None") { return $false }
            $sawPerson = $true
        } elseif ($expected.ContainsKey($who)) {
            if ($rights -ne ($expected[$who] -bor $sync)) { return $false }
        } else { return $false }
    }
    return $sawPerson
}

# Removes the folders recorded in $AdminRecord that check out, and forgets
# them. One that doesn't check out is left alone (and forgotten); one that
# couldn't be removed is kept for the next run.
function Remove-RecordedAdminFolder($sid) {
    if (-not (Test-Path -LiteralPath $AdminRecord -PathType Leaf)) { return }
    $keep = @()
    foreach ($path in @(Get-Content -LiteralPath $AdminRecord -ErrorAction SilentlyContinue)) {
        $path = "$path".Trim()
        if (-not $path -or $path -notmatch $AdminFolderPattern -or -not (Test-Path -LiteralPath $path)) { continue }
        if (-not (Test-AdminFolder $path $sid)) {
            Write-Host "   (A folder an earlier administrator step left, $path, doesn't have the"
            Write-Host "   permissions it should, so it was left alone. IT can remove it.)"
            continue
        }
        # Checked again right before removing: still a real folder, not a link.
        if (Test-AdminOwned $path) { Remove-Tree $path }
        if (Test-Path -LiteralPath $path) { $keep += $path }
    }
    if ($keep) { Set-Content -LiteralPath $AdminRecord -Value $keep -Encoding UTF8 }
    else { Remove-Item -LiteralPath $AdminRecord -Force -ErrorAction SilentlyContinue }
}
# --- End of the part shared by install.ps1 and uninstall.ps1 ---------------

Remove-RecordedAdminFolder $MySid

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
