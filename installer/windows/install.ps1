# DataLab installer for Windows.
#
#   powershell -ExecutionPolicy Bypass -File install.ps1 -Package <datalab .whl file or URL> [-Settings <lab settings file>]
#       [-Constraints <constraints.txt>] [-Yes]
#
# constraints.txt holds the exact tested dependency versions; it's found
# automatically if it sits next to a local package file.
#
# The people running this aren't expected to know Windows administration, so
# every step says what is about to happen, why, and what to click.
#
# What it does:
#   1. Checks for WSL and Docker Desktop. If anything needs an administrator
#      (turning on WSL, installing WSL and Docker Desktop, letting you use Docker),
#      it asks Windows for permission once and does all of it together. If Windows
#      then needs a restart, it offers one and carries on by itself after you sign in.
#   2. Starts Docker Desktop.
#   3. Installs uv (a Python installer) for you, if it isn't there already.
#   4. Installs DataLab, with its own Python, in your user account (no admin rights).
#   5. Downloads the pinned container images.
#   6. Saves the lab's settings and asks for your U-M GPT key and database password,
#      which go into Windows Credential Manager.
#   7. Adds DataLab to the Start menu.
#
# Everything after step 1 runs as you, without administrator rights.
param(
    [string]$Package = "",
    [string]$Settings = "",
    [string]$Constraints = "",
    # Answer yes to every question, including the restart (for IT, or testing).
    [switch]$Yes,
    # Windows starts the installer with this after the restart.
    [switch]$Resume,
    # Internal: the one part that runs as administrator (step 1). It only ever
    # runs from a checked copy of this script held in memory; see Invoke-AdminPart.
    [switch]$Prepare,
    [string]$ForUserSid = "",
    [string]$WorkDir = ""
)
$ErrorActionPreference = "Stop"
# The text of this script as it's running, for the administrator part (see
# Invoke-AdminPart). Empty when the script wasn't started from its file.
$ScriptText = $MyInvocation.MyCommand.ScriptContents
$UvVersion = "0.12.19"
# Pinned downloads, checked (SHA-256 and publisher's signature) before they run.
$WslVersion = "2.7.14"
$WslMsiUrl = "https://github.com/microsoft/WSL/releases/download/2.7.14/wsl.2.7.14.0.x64.msi"
$WslMsiSha256 = "db084e536279a59e90a26ec598d8aa8a4dff8309f41d078fd06242953ac1ebcd"
$WslPublisher = "O=Microsoft Corporation"
$DockerVersion = "4.77.0"
$DockerUrl = "https://desktop.docker.com/win/main/amd64/228796/Docker%20Desktop%20Installer.exe"
$DockerSha256 = "5b866599f0de9208f4594d64aa33658fa55cbdd64e0db13648cffe12c91795d2"
$DockerPublisher = "O=Docker Inc"
$DockerAgreement = "https://www.docker.com/legal/docker-subscription-service-agreement/"

$StateDir = Join-Path $Env:LOCALAPPDATA "DataLab"
$ResumeFile = Join-Path $StateDir "installer-resume.json"
$ResumeTask = "DataLab setup (continue after restart)"
$ResumeShortcut = Join-Path ([Environment]::GetFolderPath("Startup")) "DataLab setup.lnk"
$DockerDesktop = Join-Path $Env:ProgramFiles "Docker\Docker\Docker Desktop.exe"
# Docker Desktop's Windows service. Docker Desktop starts without an
# administrator only when this service starts by itself (--always-run-service).
$DockerService = "com.docker.service"
$WslExe = Join-Path $Env:ProgramFiles "WSL\wsl.exe"
# The group Docker Desktop creates for the people allowed to use it; its SID
# differs per computer, so it's matched by name.
$DockerUsers = "docker-users"
# The administrator part's folder: "<ProgramData>\DataLab-setup-<random>".
$AdminFolderPrefix = "DataLab-setup-"

function Step($text) { Write-Host "`n== $text ==" -ForegroundColor Cyan }
function Say($text) { Write-Host "   $text" }
function Good($text) { Write-Host "   OK: $text" -ForegroundColor Green }
function Note($text) { Write-Host "   $text" -ForegroundColor Yellow }

function Stop-Install($text) {
    Write-Host ""
    Write-Host "   $text" -ForegroundColor Red
    Write-Host "   Nothing is lost: you can run the installer again at any time, and it picks"
    Write-Host "   up where it stopped. If it keeps happening, send a screenshot of this window"
    Write-Host "   to the DataLab maintainer."
    exit 1
}

function Ask($question) {
    if ($Yes) { Write-Host "   $question [Y/n] y (-Yes)"; return $true }
    $answer = Read-Host "   $question [Y/n]"
    return ($answer -eq "" -or $answer -match '^(y|yes)$')
}

# Runs a program and returns its exit code, or $null if it didn't finish in
# time. Windows PowerShell 5.1 turns a program's error output into a
# terminating error under $ErrorActionPreference = "Stop", so a plain
# `docker info *> $null` would stop the installer instead of reporting that
# Docker isn't running; this also stops a stuck `docker` from hanging it.
function Invoke-Quiet($file, [string[]]$arguments, $seconds = 30) {
    $out = [System.IO.Path]::GetTempFileName()
    try {
        $process = Start-Process $file -ArgumentList $arguments -NoNewWindow -PassThru `
            -RedirectStandardOutput $out -RedirectStandardError "$out.err"
        $null = $process.Handle  # without this, Windows PowerShell loses the exit code
        if (-not $process.WaitForExit($seconds * 1000)) {
            try { $process.Kill() } catch {}
            return $null
        }
        return $process.ExitCode
    } finally {
        Remove-Item $out, "$out.err" -ErrorAction SilentlyContinue
    }
}

function Test-DockerRunning {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { return $false }
    return (Invoke-Quiet "docker" @("info") 30) -eq 0
}

# Whether this sign-in may use Docker. Windows applies a new group membership
# only at the next sign-in, so this reads the sign-in's own groups; it also
# needs no domain controller, so it works off the VPN.
function Test-CanUseDocker {
    return [bool](whoami /groups | Select-String -SimpleMatch "\$DockerUsers ")
}

# Whether this account is in docker-users already (Docker Desktop's own
# installer adds the account that runs it), even if it needs a new sign-in to
# take effect. Matched by SID, so no domain controller is needed. $null if the
# group can't be read (the administrator part then checks again).
function Test-InDockerUsers($sid) {
    try {
        $members = Get-LocalGroupMember -Group $DockerUsers -ErrorAction Stop
    } catch { return $null }
    return [bool]($members | Where-Object { $_.SID.Value -eq $sid })
}

# Whether Docker Desktop is installed but its service starts only for an
# administrator (installed without --always-run-service). A service turned
# off altogether ("Disabled") is left alone: that's IT's choice.
function Test-DockerServiceManual {
    if (-not (Test-Path $DockerDesktop)) { return $false }
    $service = Get-Service $DockerService -ErrorAction SilentlyContinue
    return [bool]($service -and "$($service.StartType)" -eq "Manual")
}

# Deletes a folder and what's in it without following links out of it: a link
# is removed, never what it points to. Whatever can't be deleted stays.
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

# A Docker Desktop that was uninstalled (or crashed) can leave its socket files
# behind. Windows can't open or delete them ("The file cannot be accessed by the
# system"), and the next Docker Desktop then fails to start on them. They can
# still be moved, so each folder holding them is renamed aside and Docker makes
# a fresh one. Only while Docker Desktop isn't running at all.
function Clear-StaleDockerSockets {
    # Folders an earlier run moved aside: Docker doesn't use them any more, and
    # after a restart their files can usually be deleted.
    $moved = @(Get-Item (Join-Path $Env:LOCALAPPDATA "Docker\run.stale-*"),
        (Join-Path $Env:LOCALAPPDATA "docker-secrets-engine.stale-*") -Force -ErrorAction SilentlyContinue)
    foreach ($folder in $moved) { Remove-Tree $folder.FullName }
    if (Get-Process "com.docker.backend", "Docker Desktop" -ErrorAction SilentlyContinue) { return }
    # Docker's own Linux VM, left running by a Docker Desktop that crashed or was
    # killed: the next start waits for it to shut down, times out ("waiting for
    # shutdown: context deadline exceeded"), and never comes up.
    if (Test-Path $WslExe) { $null = Invoke-Quiet $WslExe @("--terminate", "docker-desktop") 60 }
    $stamp = Get-Date -Format yyyyMMdd-HHmmss
    foreach ($folder in "Docker\run", "docker-secrets-engine") {
        $path = Join-Path $Env:LOCALAPPDATA $folder
        if (-not (Test-Path $path)) { continue }
        $stale = Get-ChildItem $path -Force -ErrorAction SilentlyContinue |
            Where-Object { $_.Attributes -band [System.IO.FileAttributes]::ReparsePoint }
        if (-not $stale) { continue }
        try {
            Rename-Item -LiteralPath $path -NewName "$(Split-Path $path -Leaf).stale-$stamp" -ErrorAction Stop
            Say "(Moved aside some files an earlier Docker Desktop left behind in $folder.)"
        } catch { Write-Verbose "Couldn't move $path aside: $_" }
    }
}

# Downloads a pinned file and checks it before it's used: its SHA-256, and
# that it's signed by its publisher.
function Save-Download($url, $sha256, $publisher, $file) {
    Say "Downloading $([System.Uri]::UnescapeDataString(($url -split '/')[-1]))..."
    $ProgressPreference = "SilentlyContinue"  # the progress bar makes big downloads much slower
    Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $file
    $actual = (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLower()
    if ($actual -ne $sha256) {
        Remove-Item -LiteralPath $file -ErrorAction SilentlyContinue
        throw "The download of $url didn't match its expected checksum, so it wasn't run."
    }
    $signature = Get-AuthenticodeSignature -LiteralPath $file
    if ($signature.Status -ne "Valid" -or $signature.SignerCertificate.Subject -notmatch [regex]::Escape($publisher)) {
        Remove-Item -LiteralPath $file -ErrorAction SilentlyContinue
        throw "The download of $url isn't signed by its publisher ($publisher), so it wasn't run."
    }
}

# The SHA-256 of some bytes, written the same way as in the command that starts
# the administrator part (Invoke-AdminPart).
function Get-BytesHash([byte[]]$bytes) {
    return [BitConverter]::ToString([System.Security.Cryptography.SHA256]::Create().ComputeHash($bytes))
}

# ---------------------------------------------------------------------------
# The administrator part. Runs in its own window, started from step 1 below.
# ---------------------------------------------------------------------------

# Its working folder: new, in ProgramData, and usable only by SYSTEM and
# Administrators, so nothing running as the person can change a download
# between its check and its run, add files beside an installer, or redirect
# what's written there. It's made with those permissions in one step (never
# looser, even briefly), then checked; anything unexpected stops the
# administrator part before it does anything.
function New-ProtectedFolder($path) {
    $system = New-Object System.Security.Principal.SecurityIdentifier("S-1-5-18")
    $admins = New-Object System.Security.Principal.SecurityIdentifier("S-1-5-32-544")
    # A file's owner may always change its permissions, and Windows can make
    # the person the owner of what an elevated window creates. With this rule
    # an owner gets only what it gives: reading the permissions.
    $ownerRights = New-Object System.Security.Principal.SecurityIdentifier("S-1-3-4")
    $inherit = [System.Security.AccessControl.InheritanceFlags]"ContainerInherit, ObjectInherit"
    $none = [System.Security.AccessControl.PropagationFlags]::None
    $allow = [System.Security.AccessControl.AccessControlType]::Allow
    $security = New-Object System.Security.AccessControl.DirectorySecurity
    $security.SetAccessRuleProtection($true, $false)  # nothing inherited from ProgramData
    $security.SetOwner($admins)
    foreach ($who in $system, $admins) {
        $security.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule(
            $who, [System.Security.AccessControl.FileSystemRights]::FullControl, $inherit, $none, $allow)))
    }
    $security.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule(
        $ownerRights, [System.Security.AccessControl.FileSystemRights]::ReadPermissions, $inherit, $none, $allow)))

    if (Test-Path -LiteralPath $path) { throw "The folder $path already exists, so it can't be trusted." }
    $null = [System.IO.Directory]::CreateDirectory($path, $security)

    $item = Get-Item -LiteralPath $path -Force
    if ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) { throw "$path is a link, not a folder." }
    $acl = Get-Acl -LiteralPath $path
    $owner = $acl.GetOwner([System.Security.Principal.SecurityIdentifier]).Value
    $allowed = @($system.Value, $admins.Value, $ownerRights.Value)
    $others = @($acl.GetAccessRules($true, $true, [System.Security.Principal.SecurityIdentifier]) |
        Where-Object { $_.IdentityReference.Value -notin $allowed })
    $wrongOwner = $owner -notin @($system.Value, $admins.Value)
    $notEmpty = @(Get-ChildItem -LiteralPath $path -Force).Count -ne 0
    if ($wrongOwner -or -not $acl.AreAccessRulesProtected -or $others.Count -ne 0 -or $notEmpty) {
        throw "The folder $path didn't get the expected permissions (owner $owner), so it wasn't used."
    }
}

# Once the administrator part is completely done, lets the person read its
# result and log, and remove the folder. Nothing elevated uses it after this.
function Grant-ResultToPerson($folder, [string[]]$files) {
    $person = New-Object System.Security.Principal.SecurityIdentifier($ForUserSid)
    $noInherit = [System.Security.AccessControl.InheritanceFlags]::None
    $none = [System.Security.AccessControl.PropagationFlags]::None
    $allow = [System.Security.AccessControl.AccessControlType]::Allow
    $rights = [System.Security.AccessControl.FileSystemRights]
    $access = [System.Security.AccessControl.AccessControlSections]::Access
    foreach ($file in $files) {
        if (-not (Test-Path -LiteralPath $file)) { continue }
        $item = Get-Item -LiteralPath $file -Force
        $acl = $item.GetAccessControl($access)
        $acl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule(
            $person, ($rights::Read -bor $rights::Delete), $noInherit, $none, $allow)))
        $item.SetAccessControl($acl)
    }
    $item = Get-Item -LiteralPath $folder -Force
    $acl = $item.GetAccessControl($access)
    $acl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule(
        $person, ($rights::ListDirectory -bor $rights::ReadAttributes -bor $rights::Delete -bor $rights::Synchronize),
        $noInherit, $none, $allow)))
    $item.SetAccessControl($acl)
}

if ($Prepare) {
    $Host.UI.RawUI.WindowTitle = "DataLab setup (administrator part)"
    $result = @{ ok = $false; restart = $false; error = "" }
    $ready = $false
    $resultFile = Join-Path $WorkDir "result.json"
    $logFile = Join-Path $WorkDir "setup.log"
    try {
        $expected = Join-Path $Env:ProgramData $AdminFolderPrefix
        if (-not $WorkDir.StartsWith($expected, [StringComparison]::OrdinalIgnoreCase)) {
            throw "Unexpected working folder: $WorkDir"
        }
        New-ProtectedFolder $WorkDir
        $ready = $true
        Set-Location -LiteralPath $WorkDir
        Start-Transcript -Path $logFile | Out-Null
        # The installers unpack into TEMP: keep that inside the protected
        # folder too, not in the person's own temp folder.
        $temp = Join-Path $WorkDir "temp"
        New-Item -ItemType Directory $temp | Out-Null
        $Env:TEMP = $temp; $Env:TMP = $temp

        Write-Host "DataLab setup: the administrator part" -ForegroundColor Cyan
        Write-Host "Please leave this window open. It closes by itself when it's done."
        Write-Host "The downloads are large (about 900 MB), so this can take 10 minutes or more."

        Step "Turning on the Windows features WSL needs"
        foreach ($name in "Microsoft-Windows-Subsystem-Linux", "VirtualMachinePlatform") {
            $feature = Get-WindowsOptionalFeature -Online -FeatureName $name
            if ($feature.State -eq "Enabled") { Good "$name is on."; continue }
            if ($feature.State -eq "EnablePending") { Good "$name turns on at the next restart."; $result.restart = $true; continue }
            $change = Enable-WindowsOptionalFeature -Online -FeatureName $name -All -NoRestart -WarningAction SilentlyContinue
            Good "Turned on $name."
            if ($change.RestartNeeded) { $result.restart = $true }
        }

        Step "Installing WSL (Windows Subsystem for Linux)"
        if (Test-Path $WslExe) {
            Good "WSL is already installed."
        } else {
            $msi = Join-Path $WorkDir "wsl.$WslVersion.x64.msi"
            Save-Download $WslMsiUrl $WslMsiSha256 $WslPublisher $msi
            Say "Installing WSL $WslVersion..."
            $install = Start-Process msiexec.exe -ArgumentList "/i", "`"$msi`"", "/qn", "/norestart" `
                -WorkingDirectory $WorkDir -Wait -PassThru
            if ($install.ExitCode -eq 3010) { $result.restart = $true }
            elseif ($install.ExitCode -ne 0) { throw "Installing WSL failed (msiexec exit code $($install.ExitCode))." }
            Good "Installed WSL."
        }

        Step "Installing Docker Desktop"
        if (Test-Path $DockerDesktop) {
            Good "Docker Desktop is already installed."
        } else {
            $installer = Join-Path $WorkDir "Docker Desktop Installer.exe"
            Save-Download $DockerUrl $DockerSha256 $DockerPublisher $installer
            Say "Installing Docker Desktop $DockerVersion (this takes a few minutes, with no progress shown)..."
            # --always-run-service: Docker Desktop can then start without an administrator.
            $install = Start-Process $installer -ArgumentList "install", "--quiet", "--accept-license", `
                "--backend=wsl-2", "--always-run-service" -WorkingDirectory $WorkDir -Wait -PassThru
            if ($install.ExitCode -ne 0) { throw "Installing Docker Desktop failed (exit code $($install.ExitCode))." }
            Good "Installed Docker Desktop."
            $result.restart = $true
        }

        Step "Letting Docker Desktop start without an administrator"
        # A Docker Desktop installed earlier without --always-run-service has a
        # service that only an administrator can start.
        $service = Get-Service $DockerService -ErrorAction SilentlyContinue
        $startType = "$($service.StartType)"
        if (-not $service) {
            Note "Docker Desktop's service ($DockerService) wasn't found; Docker Desktop may ask for an administrator when it starts."
        } elseif ($startType -eq "Disabled") {
            Note "Docker Desktop's service is turned off on this computer (by IT, most likely), so it was left as it is."
        } elseif ($startType -ne "Automatic") {
            Set-Service -Name $DockerService -StartupType Automatic
            Good "Docker Desktop's service now starts by itself."
        } else {
            Good "Docker Desktop's service already starts by itself."
        }
        if ($service -and $startType -ne "Disabled" -and -not $result.restart) {
            Start-Service -Name $DockerService -ErrorAction SilentlyContinue
        }

        Step "Letting you use Docker"
        # By SID, not name: looking up a domain account's name needs the domain
        # controller, which isn't reachable off the VPN.
        # Add-LocalGroupMember takes a SID only as text ("S-1-5-..."), not as a SecurityIdentifier.
        $sid = (New-Object System.Security.Principal.SecurityIdentifier($ForUserSid)).Value
        try {
            Add-LocalGroupMember -Group $DockerUsers -Member $sid
            Good "Added you to the $DockerUsers group (it takes effect after the restart)."
            $result.restart = $true
        } catch [Microsoft.PowerShell.Commands.MemberExistsException] {
            Good "You're already in the $DockerUsers group."
        }
        $result.ok = $true
        Write-Host "`nAll done here. This window closes in a moment." -ForegroundColor Green
        Start-Sleep -Seconds 3
    } catch {
        $result.error = "$_"
        Write-Host "`nSomething went wrong: $_" -ForegroundColor Red
        if ($Yes) { Start-Sleep -Seconds 10 }
        else { Read-Host "Press Enter to close this window (the main installer window explains what to do)" }
    } finally {
        # Only into the folder this part made itself; without it, nothing is written.
        if ($ready) {
            $result | ConvertTo-Json | Set-Content -Encoding UTF8 -LiteralPath $resultFile
            Stop-Transcript | Out-Null
            # The downloads and unpacked installers aren't needed any more.
            Get-ChildItem -LiteralPath $WorkDir -Force |
                Where-Object { $_.FullName -notin @($resultFile, $logFile) } |
                Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
            Grant-ResultToPerson $WorkDir @($resultFile, $logFile)
        }
    }
    if ($result.ok) { exit 0 } else { exit 1 }
}

# ---------------------------------------------------------------------------
# The installer, run as the person installing.
# ---------------------------------------------------------------------------
$Host.UI.RawUI.WindowTitle = "DataLab setup"
$MySid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value

# One installer at a time: after a restart, the logon task, the Startup-folder
# shortcut and a run started by hand could otherwise overlap.
$createdNew = $false
$SetupLock = [System.Threading.Mutex]::new($false, "Local\IHS-DataLab-setup", [ref]$createdNew)
if (-not $createdNew) {
    $SetupLock.Dispose()
    Write-Host ""
    Write-Host "The DataLab installer is already open in another window. Carry on there," -ForegroundColor Yellow
    Write-Host "or close that window and run the installer again." -ForegroundColor Yellow
    exit 3
}

# Opens the installer again after the next sign-in. A logon task for this
# account, not a RunOnce entry: managed Windows machines were seen skipping
# RunOnce entirely. If the task can't be made, a Startup-folder shortcut does
# it. The account is given by its SID: its name would need the domain controller.
function Register-Resume {
    $arguments = "-NoProfile -ExecutionPolicy Bypass -NoExit -File `"$PSCommandPath`" -Resume"
    try {
        $action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $arguments
        $trigger = New-ScheduledTaskTrigger -AtLogOn -User $MySid
        $trigger.Delay = "PT20S"  # let the desktop finish appearing first
        $principal = New-ScheduledTaskPrincipal -UserId $MySid -LogonType Interactive -RunLevel Limited
        # Laptops: by default a task doesn't start on battery.
        $options = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
            -ExecutionTimeLimit (New-TimeSpan -Seconds 0)
        Register-ScheduledTask -TaskName $ResumeTask -Action $action -Trigger $trigger -Principal $principal `
            -Settings $options -Force -ErrorAction Stop | Out-Null
    } catch {
        $link = (New-Object -ComObject WScript.Shell).CreateShortcut($ResumeShortcut)
        $link.TargetPath = "powershell.exe"
        $link.Arguments = $arguments
        $link.Save()
    }
}

function Unregister-Resume {
    Unregister-ScheduledTask -TaskName $ResumeTask -Confirm:$false -ErrorAction SilentlyContinue
    Remove-Item $ResumeShortcut -ErrorAction SilentlyContinue
}

# When Windows last started (in ticks, 0 if unknown): to tell whether it has
# restarted since a restart was asked for.
function Get-BootTime {
    try { return [int64](Get-CimInstance Win32_OperatingSystem -ErrorAction Stop).LastBootUpTime.ToUniversalTime().Ticks }
    catch { return [int64]0 }
}

# The rest runs inside try/finally, so the one-at-a-time lock is freed however
# it ends, also when the window stays open afterwards (-NoExit).
try {
# Whatever started this run, nothing should open the installer again unless
# this run asks for another restart (Request-Restart sets it up again).
Unregister-Resume
# What an earlier administrator part left for this account to remove.
Get-ChildItem -LiteralPath $Env:ProgramData -Directory -Filter "$AdminFolderPrefix*" -ErrorAction SilentlyContinue |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

$saved = $null
if (Test-Path $ResumeFile) { $saved = Get-Content $ResumeFile -Raw | ConvertFrom-Json }
if ($Resume) {
    if (-not $saved) { Write-Host "There's no DataLab install waiting to continue."; exit 2 }
    $Package = $saved.Package; $Settings = $saved.Settings; $Constraints = $saved.Constraints
    Write-Host ""
    Write-Host "Welcome back! Let's finish setting up DataLab." -ForegroundColor Cyan
    Write-Host "You won't be asked for administrator permission again."
} else {
    Write-Host ""
    Write-Host "Welcome! This sets up DataLab on this computer." -ForegroundColor Cyan
    Write-Host "It takes about 20-30 minutes, most of it downloading. It walks you through"
    Write-Host "each step and tells you whenever it needs you to do something."
}
if (-not $Package) { Write-Host "Pass the DataLab package: install.ps1 -Package <file or URL>"; exit 2 }

# Check the package before anything else, so a wrong path is found before
# any administrator step or restart.
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
$Constraints = (Resolve-Path -LiteralPath $Constraints).ProviderPath
if ($Settings) {
    if (-not (Test-Path -LiteralPath $Settings -PathType Leaf)) { Write-Host "Settings file not found: $Settings"; exit 2 }
    $Settings = (Resolve-Path -LiteralPath $Settings).ProviderPath
}

# How many restarts so far didn't make Docker usable, and when Windows had
# started at the time the last restart was asked for: 0 if no restart is
# waiting, -1 if one is but the start time couldn't be read.
$Restarts = 0
$RestartBoot = [int64]0
if ($saved) {
    if ($saved.Restarts) { $Restarts = [int]$saved.Restarts }
    if ($saved.RestartBoot) { $RestartBoot = [int64]$saved.RestartBoot }
}
if ($RestartBoot -ne 0) {
    $bootNow = Get-BootTime
    # Windows' start time can shift by a few seconds when its clock is set, so
    # only a clear change counts. With no start time to compare, a run from
    # the logon task counts as after a restart.
    $restarted = if ($RestartBoot -gt 0 -and $bootNow -gt 0) {
        [math]::Abs($bootNow - $RestartBoot) -gt [TimeSpan]::FromMinutes(2).Ticks
    } else { [bool]$Resume }
    if ($restarted) { $Restarts += 1; $RestartBoot = [int64]0 }
}

function Save-Progress($prepared) {
    New-Item -ItemType Directory -Force $StateDir | Out-Null
    @{ Package = $Package; Settings = $Settings; Constraints = $Constraints; Prepared = $prepared
        Restarts = $Restarts; RestartBoot = $RestartBoot } |
        ConvertTo-Json | Set-Content -Encoding UTF8 $ResumeFile
}

function Request-Restart {
    $script:RestartBoot = Get-BootTime
    if ($script:RestartBoot -eq 0) { $script:RestartBoot = [int64]-1 }
    Save-Progress $true
    Register-Resume
    Write-Host ""
    Note "Windows needs to restart to finish turning these on."
    Note "After the restart, sign in as usual. The DataLab installer opens by itself"
    Note "a few moments later and carries on from here. It won't ask for"
    Note "administrator permission again."
    Note "Save anything you have open in other programs first."
    if (Ask "Restart now?") {
        Restart-Computer -Force
    } else {
        Say "OK. Restart whenever you're ready (Start menu > Power > Restart);"
        Say "the installer carries on after you sign in again."
    }
    exit 0
}

# Runs the administrator part and returns its result (ok, restart, error).
#
# It runs elevated, so it must not run anything that a program running as the
# person could have changed, and this script's own file (in Downloads or
# OneDrive, say) is one of those. So the elevated window gets a short, fixed
# command that reads a copy of this script's text once, checks its SHA-256
# against the text this window is running, and runs that checked text from
# memory; no file is run elevated. What it downloads and writes goes into a
# new folder only administrators can change (New-ProtectedFolder), which it
# lets the person read and remove once it's done.
function Invoke-AdminPart {
    if (-not $ScriptText) { Stop-Install "Run the installer from its file: powershell -ExecutionPolicy Bypass -File install.ps1 ..." }
    $work = Join-Path $Env:ProgramData ($AdminFolderPrefix + [guid]::NewGuid().ToString("N"))
    $copy = Join-Path $Env:TEMP ($AdminFolderPrefix + [guid]::NewGuid().ToString("N") + ".ps1")
    $bytes = (New-Object System.Text.UTF8Encoding($false)).GetBytes($ScriptText)
    [System.IO.File]::WriteAllBytes($copy, $bytes)
    $quote = { param($text) "'" + ($text -replace "'", "''") + "'" }
    # Only single quotes inside: the whole command is one double-quoted argument.
    $command = "`$b = [IO.File]::ReadAllBytes($(& $quote $copy)); " +
        "`$h = [BitConverter]::ToString([Security.Cryptography.SHA256]::Create().ComputeHash(`$b)); " +
        "if (`$h -ne '$(Get-BytesHash $bytes)') { " +
        "Write-Host 'The installer changed on disk after it started, so nothing was run.'; Start-Sleep -Seconds 20; exit 1 }; " +
        "& ([scriptblock]::Create([Text.Encoding]::UTF8.GetString(`$b))) -Prepare " +
        "-ForUserSid $(& $quote $MySid) -WorkDir $(& $quote $work)$(if ($Yes) { ' -Yes' })"
    Say "Asking Windows for permission now (look for the box; it may be behind this window)..."
    try {
        $null = Start-Process powershell.exe -Verb RunAs -Wait -PassThru `
            -ArgumentList "-NoProfile -ExecutionPolicy Bypass -Command `"$command`""
    } catch {
        Stop-Install ("Windows didn't give administrator permission (the box was closed or 'No' was " +
            "clicked, or your temporary administrator access has run out), so nothing was changed. " +
            "Get administrator access again (or ask IT for it), then run the installer again." +
            "`n   (Windows said: $($_.Exception.Message))")
    } finally {
        Remove-Item -LiteralPath $copy -ErrorAction SilentlyContinue
    }
    $resultFile = Join-Path $work "result.json"
    if (-not (Test-Path -LiteralPath $resultFile)) { Stop-Install "The administrator part closed before it finished." }
    $outcome = Get-Content -LiteralPath $resultFile -Raw | ConvertFrom-Json
    if (-not $outcome.ok) {
        Stop-Install ("The administrator part didn't finish: $($outcome.error)`n   " +
            "Details are saved in $(Join-Path $work 'setup.log')")
    }
    Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
    return $outcome
}

# Windows has restarted, but this sign-in still can't use Docker. Asking for
# another restart could repeat for ever (and with -Yes, restart at every
# sign-in), so this stops and says why.
function Stop-StillNoDocker {
    Save-Progress $true  # keeps the count of restarts
    Write-Host ""
    Note "Windows has restarted, but your account still can't use Docker."
    $member = Test-InDockerUsers $MySid
    if ($member -eq $true) {
        Say "Your account is in the '$DockerUsers' group, but Windows hasn't applied that to"
        Say "this sign-in. Sign out and in again, then run the installer again."
        exit 1
    }
    if ($member -eq $false) {
        Say "Your account was added to the '$DockerUsers' group, but it isn't in it any more."
    } else {
        Say "Your account should be in the '$DockerUsers' group, but Windows doesn't show it there."
    }
    Say "On a managed computer, the likely cause is a group policy that resets who is in"
    Say "this computer's groups at each sign-in. Ask IT (the service desk) to let your"
    Say "account stay in the '$DockerUsers' group on this computer, and mention that a"
    Say "group policy seems to remove it."
    if (-not $Yes) {
        Write-Host ""
        Say "You can also run the administrator part again now, to add your account back."
        Say "If a policy removes it again, that won't last past the next sign-in."
        if (Ask "Run the administrator part again?") {
            $null = Invoke-AdminPart
            Good "Windows is set up."
            Request-Restart
        }
    }
    Stop-Install "Your account can't use Docker yet (see above)."
}

Step "Step 1 of 7: Getting Windows ready (WSL and Docker Desktop)"
Say "DataLab runs its analysis in Docker, a sealed-off space on your computer."
Say "Docker needs a Windows feature called WSL."
$missing = @()
if (-not (Test-Path $WslExe)) { $missing += "Turn on WSL and install it (WSL $WslVersion, from Microsoft)" }
if (-not (Test-Path $DockerDesktop)) { $missing += "Install Docker Desktop $DockerVersion (from Docker)" }
if (Test-DockerServiceManual) { $missing += "Let Docker Desktop start without an administrator" }
$prepared = ($saved -and $saved.Prepared) -or (Test-InDockerUsers $MySid)
if (-not (Test-CanUseDocker) -and -not $prepared) { $missing += "Give your account permission to use Docker" }

if ($missing) {
    Write-Host ""
    Say "To do that, Windows needs to:"
    $missing | ForEach-Object { Say "  - $_" }
    Write-Host ""
    Say "This needs administrator permission, just this once."
    Say "If your computer only gives you administrator access for a limited time"
    Say "(Michigan Medicine computers do), request it now, before you continue,"
    Say "and give it at least 30 minutes."
    Write-Host ""
    Say "When you continue:"
    Say "  1. Windows shows a box asking 'Do you want to allow this app to make"
    Say "     changes to your device?' Click Yes. (On a Michigan Medicine computer"
    Say "     you may be asked for your password or for a reason: 'Installing DataLab'.)"
    Say "  2. A second window opens and does the work. It can take 10 minutes or"
    Say "     more; leave it open until it closes by itself."
    Say "  3. Windows will then need a restart, and the installer carries on by"
    Say "     itself after you sign in again."
    if (-not (Test-Path $DockerDesktop)) {
        Say ""
        Say "Docker Desktop is used under Docker's Subscription Service Agreement:"
        Say "$DockerAgreement"
        Say "Continuing means you accept it."
    }
    Write-Host ""
    if (-not (Ask "Ready to continue?")) { Say "No problem. Nothing was changed; run the installer again when you're ready."; exit 1 }

    $outcome = Invoke-AdminPart
    Good "Windows is set up."
    Save-Progress $true
    if ($outcome.restart -or -not (Test-CanUseDocker)) { Request-Restart }
} elseif (-not (Test-CanUseDocker)) {
    # The administrator part is done, but Windows only applies it at sign-in.
    # After one restart that didn't help, another won't either.
    if ($Restarts -ge 1) { Stop-StillNoDocker }
    Request-Restart
}
Good "WSL and Docker Desktop are ready."

Step "Step 2 of 7: Starting Docker Desktop"
if (-not (Test-DockerRunning)) {
    Say "Starting Docker Desktop. The first start can take a few minutes."
    Say "If Docker Desktop shows a welcome screen or asks you to sign in, you can"
    Say "skip it: DataLab doesn't need a Docker account. Leave Docker Desktop running."
    Clear-StaleDockerSockets
    if (Test-Path $DockerDesktop) { Start-Process $DockerDesktop }
    # A real clock: each check can itself take up to 30 seconds.
    $clock = [System.Diagnostics.Stopwatch]::StartNew()
    $lastNote = 0
    do {
        Start-Sleep -Seconds 5
        $running = Test-DockerRunning
        $minutes = [int][math]::Floor($clock.Elapsed.TotalMinutes)
        if (-not $running -and $minutes -gt $lastNote) {
            $lastNote = $minutes
            Say "Still waiting for Docker Desktop ($minutes min)... this is normal the first time."
        }
    } until ($running -or $clock.Elapsed.TotalMinutes -ge 10)
    if (-not $running) {
        Stop-Install ("Docker Desktop didn't finish starting. Open it from the Start menu, wait until " +
            "it says 'Engine running' (bottom left), then run the installer again.")
    }
}
Good "Docker Desktop is running."

Step "Step 3 of 7: Installing uv (the tool that installs DataLab)"
$LocalBin = Join-Path $Env:USERPROFILE ".local\bin"
$Env:Path = "$LocalBin;$Env:Path"
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    powershell -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/$UvVersion/install.ps1 | iex"
}
uv --version

Step "Step 4 of 7: Installing DataLab"
# uv cuts a --constraints path at its first space ("failed to read from file
# C:\Users\me\OneDrive"), and on Michigan Medicine computers downloads usually
# sit under "OneDrive - Michigan Medicine". So uv gets a copy under a plain
# name, given relative to its folder. (The package path itself is fine.)
New-Item -ItemType Directory -Force $StateDir | Out-Null
Copy-Item -LiteralPath $Constraints (Join-Path $StateDir "constraints.txt") -Force
Push-Location $StateDir
try {
    uv tool install --force --python 3.13 --constraints constraints.txt $Package
} finally { Pop-Location }
if ($LASTEXITCODE -ne 0) { Stop-Install "Installing DataLab didn't work (see the messages above)." }
$DataLab = Join-Path (uv tool dir --bin) "datalab.exe"
& $DataLab --version

Step "Step 5 of 7: Downloading DataLab's containers (a few GB; this takes a while)"
& $DataLab pull-images
if ($LASTEXITCODE -ne 0) {
    Stop-Install ("Downloading the containers didn't work (the messages above say why). If you're " +
        "offline, reconnect, then run the installer again.")
}

Step "Step 6 of 7: Your keys"
Say "Next, DataLab asks for your U-M GPT API key (and the database password, if"
Say "your lab uses one). Nothing shows on screen while you type or paste; that's"
Say "normal. Press Enter when done. They're kept in Windows Credential Manager."
if ($Settings) { & $DataLab setup --settings $Settings } else { & $DataLab setup }

Step "Step 7 of 7: Adding DataLab to the Start menu"
# DataLab runs in a PowerShell window, so it's easy to see it's running and to
# quit (close the window or press Ctrl-C).
$StartMenu = Join-Path $Env:APPDATA "Microsoft\Windows\Start Menu\Programs"
$Shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $StartMenu "DataLab.lnk"))
$Shortcut.TargetPath = "powershell.exe"
$Shortcut.Arguments = "-NoExit -Command `"& '$DataLab' serve`""
$Shortcut.Description = "IHS DataLab"
$Shortcut.Save()
Good "Added DataLab to the Start menu."
Remove-Item $ResumeFile -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "All done! DataLab is installed." -ForegroundColor Green
Say "To open it: Start menu > type DataLab > press Enter. It opens in your browser."
Say "A small window stays open while DataLab runs; close it to quit DataLab."
Say "(Or run: `"$DataLab`" serve)"
} finally {
    $SetupLock.Dispose()
}
