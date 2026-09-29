# DataLab installer for Windows.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File install.ps1 [-Settings <lab settings file>]
#       [-Package <datalab .whl file or URL>] [-Requirements <requirements.txt>]
#       [-Practice] [-NoGitHub] [-Yes]
#
# Without -Package, it uses the one datalab-<version>-py3-none-any.whl beside
# this script (download both from the same release into one folder).
# requirements.txt comes with each release: every dependency pinned by version
# and hash, and the package by its checksum. It's found automatically next to
# a local package file. Nothing is installed that it doesn't name.
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
#   3. Installs uv (a Python installer), a pinned version checked by SHA-256 and signature.
#   4. Installs DataLab, with its own Python, in your user account (no admin rights).
#      Each version gets its own folder, so an update installs beside the one in use
#      and the previous version is kept (docs/DISTRIBUTION.md).
#   5. Downloads the pinned container images (with -Practice, Oracle Database Free too).
#   6. Saves the lab's settings and asks for your U-M GPT key and database password,
#      which go into Windows Credential Manager. -Practice asks for no password, and
#      the key is optional there.
#   7. Offers the GitHub sign-in for the lab's knowledge base and pipelines, then
#      downloads both. With -Practice, instead: sets up the practice database (a
#      container on this computer only, with made-up data), keeping any data it
#      already has.
#   8. Adds DataLab to the Start menu and the Desktop ("DataLab", or "DataLab (practice)",
#      each with its own icon).
#
# Everything after step 1 runs as you, without administrator rights.
param(
    [string]$Package = "",
    [string]$Settings = "",
    [string]$Requirements = "",
    # The practice profile: synthetic data only, and no lab repos.
    [switch]$Practice,
    # Leave the GitHub sign-in for later (Settings > GitHub in DataLab).
    [switch]$NoGitHub,
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
# Constrained Language Mode (AppLocker, WDAC) blocks the .NET calls this relies
# on, partway through; better to say so now.
if ($ExecutionContext.SessionState.LanguageMode -ne "FullLanguage") {
    Write-Host ""
    Write-Host "   This computer runs PowerShell in a restricted mode ($($ExecutionContext.SessionState.LanguageMode))," -ForegroundColor Red
    Write-Host "   usually set by IT (AppLocker or Windows Defender Application Control), so the" -ForegroundColor Red
    Write-Host "   DataLab installer can't run. Ask IT to install DataLab, or to allow this script." -ForegroundColor Red
    exit 1
}
# 32-bit PowerShell ("Windows PowerShell (x86)") on 64-bit Windows sees other
# Program Files and system folders, and would install the wrong things.
if ([Environment]::Is64BitOperatingSystem -and -not [Environment]::Is64BitProcess) {
    Write-Host ""
    Write-Host "   This is the 32-bit PowerShell, 'Windows PowerShell (x86)'. Please open the" -ForegroundColor Red
    Write-Host "   normal one (Start menu > Windows PowerShell, without '(x86)'), then run the" -ForegroundColor Red
    Write-Host "   installer again from there." -ForegroundColor Red
    exit 1
}
# The text of this script as it's running, for the administrator part (see
# Invoke-AdminPart). Empty when the script wasn't started from its file.
$ScriptText = $MyInvocation.MyCommand.ScriptContents
# The installer's own folder, where the package is looked for without -Package.
$ScriptFolder = $PSScriptRoot
# uv, which installs DataLab: its release for 64-bit Windows, pinned by
# version and by the SHA-256 in that release's published .sha256 file, and
# its uv.exe checked for its publisher's signature. To move to a newer uv,
# change all three from the new release's page on GitHub
# (docs/DISTRIBUTION.md, "Windows specifics").
$UvVersion = "0.12.19"
$UvZipUrl = "https://github.com/astral-sh/uv/releases/download/0.12.19/uv-x86_64-pc-windows-msvc.zip"
$UvZipSha256 = "6dbb02d79e419522f1c500f0adb1cddcff0cda7d59b0d66ea7f5e3b4a1b2f5f0"
$UvPublisher = "OpenAI OpCo, LLC"
# Pinned downloads, checked (SHA-256 and publisher's signature) before they run.
$WslVersion = "2.7.14"
$WslMsiUrl = "https://github.com/microsoft/WSL/releases/download/2.7.14/wsl.2.7.14.0.x64.msi"
$WslMsiSha256 = "db084e536279a59e90a26ec598d8aa8a4dff8309f41d078fd06242953ac1ebcd"
# The exact organisation (O=) of the certificate each one must be signed with.
$WslPublisher = "Microsoft Corporation"
$DockerVersion = "4.77.0"
$DockerUrl = "https://desktop.docker.com/win/main/amd64/228796/Docker%20Desktop%20Installer.exe"
$DockerSha256 = "5b866599f0de9208f4594d64aa33658fa55cbdd64e0db13648cffe12c91795d2"
$DockerPublisher = "Docker Inc"
$DockerAgreement = "https://www.docker.com/legal/docker-subscription-service-agreement/"

$StateDir = Join-Path $Env:LOCALAPPDATA "DataLab"
$ResumeFile = Join-Path $StateDir "installer-resume.json"
# The pinned uv, in a folder of DataLab's own, used by full path (never a uv
# found on PATH). DataLab's updater looks for it here too.
$UvDir = Join-Path $StateDir "uv"
$Uv = Join-Path $UvDir "uv.exe"
# The administrator part's folders this account's installer made, one per line,
# so that exactly those (and nothing else in ProgramData) are removed later.
$AdminRecord = Join-Path $StateDir "installer-admin-folder.txt"
$ProgramFilesDir = [Environment]::GetFolderPath("ProgramFiles")
$ResumeShortcut = Join-Path ([Environment]::GetFolderPath("Startup")) "DataLab setup.lnk"
$DockerDesktop = Join-Path $ProgramFilesDir "Docker\Docker\Docker Desktop.exe"
# Docker Desktop's Windows service. Docker Desktop starts without an
# administrator only when this service starts by itself (--always-run-service).
$DockerService = "com.docker.service"
# Docker Desktop's own processes, which run as the person: the ones closing it
# ends (Repair-VmLogon). Never its service above, which runs as SYSTEM. The same
# list as DOCKER_DESKTOP_IMAGES in DataLab's windows_vm.py (tests check they match).
$DockerDesktopImages = @("Docker Desktop.exe", "com.docker.backend.exe", "com.docker.build.exe", "docker-sandbox.exe")
$WslExe = Join-Path $ProgramFilesDir "WSL\wsl.exe"
# The group Docker Desktop creates for the people allowed to use it; its SID
# differs per computer, so it's matched by name.
$DockerUsers = "docker-users"
# The administrator part's folder: "<ProgramData>\DataLab-setup-<32 hex digits>".
# ProgramData comes from Windows itself, not from $Env:ProgramData, which a
# person can change for their own account.
$AdminBase = [Environment]::GetFolderPath("CommonApplicationData")
$AdminFolderPrefix = "DataLab-setup-"
$AdminFolderPattern = "^" + [regex]::Escape((Join-Path $AdminBase $AdminFolderPrefix)) + "[0-9a-f]{32}$"
# Programs run by full path from Windows' own folder, never looked up by name
# (a person can add folders and App Paths entries for their own account).
$SystemDir = [Environment]::SystemDirectory
$WindowsPowerShell = Join-Path $SystemDir "WindowsPowerShell\v1.0\powershell.exe"
# Well-known SIDs.
$SystemSid = "S-1-5-18"
$AdminsSid = "S-1-5-32-544"
$OwnerRightsSid = "S-1-3-4"
$TrustedInstallerSid = "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464"
# WSL's virtual machine (and so Docker's) signs in as NT VIRTUAL MACHINE\Virtual
# Machines, which needs the "Log on as a service" right. Hyper-V adds it when
# Windows starts; a domain policy that sets that right to its own list (on the
# Michigan Medicine network) takes it away again, and then no WSL virtual
# machine starts (HCS 0x80070569) and Docker Desktop waits for ever. This puts
# back that one right for that one account, and changes nothing else. It's the
# same text as GRANT_SCRIPT in DataLab's windows_vm.py (tests check they match),
# which offers the same fix whenever DataLab starts.
$VmLogonRefused = "0x80070569"
$VmLogonGrant = @'
$ErrorActionPreference = 'Stop'
$Sid = 'S-1-5-83-0'
$Right = 'SeServiceLogonRight'
$Marshal = [Runtime.InteropServices.Marshal]
$assembly = [AppDomain]::CurrentDomain.DefineDynamicAssembly(
    (New-Object Reflection.AssemblyName 'DataLabVmLogonRight'),
    [Reflection.Emit.AssemblyBuilderAccess]::Run)
$type = $assembly.DefineDynamicModule('DataLabVmLogonRight').DefineType('Lsa', 'Public, Class')
foreach ($method in @(
    @('LsaOpenPolicy', @([IntPtr], [byte[]], [UInt32], [IntPtr].MakeByRefType())),
    @('LsaAddAccountRights', @([IntPtr], [byte[]], [IntPtr], [UInt32])),
    @('LsaNtStatusToWinError', @([UInt32])),
    @('LsaClose', @([IntPtr])))) {
    $defined = $type.DefinePInvokeMethod($method[0], 'advapi32.dll',
        'Public, Static, PinvokeImpl', [Reflection.CallingConventions]::Standard,
        [UInt32], [Type[]]$method[1], [Runtime.InteropServices.CallingConvention]::Winapi,
        [Runtime.InteropServices.CharSet]::Unicode)
    # The NTSTATUS comes back as the value, not turned into an exception.
    $defined.SetImplementationFlags([Reflection.MethodImplAttributes]::PreserveSig)
}
$Lsa = $type.CreateType()
function Test-Status($status) {
    if ($status -ne 0) {
        throw (New-Object ComponentModel.Win32Exception([int]$Lsa::LsaNtStatusToWinError($status)))
    }
}
$sidObject = New-Object Security.Principal.SecurityIdentifier($Sid)
$sidBytes = New-Object byte[] $sidObject.BinaryLength
$sidObject.GetBinaryForm($sidBytes, 0)
# LSA_OBJECT_ATTRIBUTES, all zero (LsaOpenPolicy ignores its members).
$attributes = New-Object byte[] 64
$policy = [IntPtr]::Zero
# POLICY_CREATE_ACCOUNT | POLICY_LOOKUP_NAMES
Test-Status ($Lsa::LsaOpenPolicy([IntPtr]::Zero, $attributes, 0x810, [ref]$policy))
$name = $Marshal::StringToHGlobalUni($Right)
$unicode = $Marshal::AllocHGlobal(16)
try {
    # LSA_UNICODE_STRING: Length, MaximumLength (in bytes), then the text's address.
    $Marshal::WriteInt16($unicode, 0, [int16]($Right.Length * 2))
    $Marshal::WriteInt16($unicode, 2, [int16]($Right.Length * 2 + 2))
    $Marshal::WriteIntPtr($unicode, [IntPtr]::Size, $name)
    Test-Status ($Lsa::LsaAddAccountRights($policy, $sidBytes, $unicode, 1))
} finally {
    $Marshal::FreeHGlobal($unicode)
    $Marshal::FreeHGlobal($name)
    $null = $Lsa::LsaClose($policy)
}
'@

function Step($text) { Write-Host "`n== $text ==" -ForegroundColor Cyan }
function Say($text) { Write-Host "   $text" }
function Good($text) { Write-Host "   OK: $text" -ForegroundColor Green }
function Note($text) { Write-Host "   $text" -ForegroundColor Yellow }

# Whether hardware virtualization is on (Step 1). $true when Windows can't say.
function Test-VirtualizationOn {
    try {
        $system = Get-CimInstance Win32_ComputerSystem -ErrorAction Stop
        if ($system.HypervisorPresent) { return $true }
        $processor = Get-CimInstance Win32_Processor -ErrorAction Stop | Select-Object -First 1
        return [bool]$processor.VirtualizationFirmwareEnabled
    } catch {
        Say "(Couldn't check whether virtualization is on; carrying on.)"
        return $true
    }
}

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
# Checked quietly, first whether the group is there at all (before Docker
# Desktop is installed, it isn't), so expected cases never show up as errors.
function Test-InDockerUsers($sid) {
    if (-not (Get-LocalGroup -Name $DockerUsers -ErrorAction SilentlyContinue)) { return $null }
    $members = @(Get-LocalGroupMember -Group $DockerUsers -ErrorAction SilentlyContinue -ErrorVariable unreadable)
    if ($unreadable) { return $null }
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

# Whether Windows refuses to let WSL's virtual machine sign in (see
# $VmLogonGrant). Found out by starting WSL's own system distribution, never
# Docker's: a docker-desktop started outside Docker Desktop can leave it
# waiting for that to shut down. Asking Windows directly needs an administrator.
function Test-VmLogonRefused {
    $wsl = Join-Path $SystemDir "wsl.exe"
    if (-not (Test-Path $wsl)) { return $false }
    $out = [System.IO.Path]::GetTempFileName()
    $Env:WSL_UTF8 = "1"  # wsl.exe's own messages in UTF-8, not UTF-16
    try {
        $process = Start-Process $wsl -ArgumentList "--system", "-e", "true" -NoNewWindow -PassThru `
            -RedirectStandardOutput $out -RedirectStandardError "$out.err"
        $null = $process.Handle  # without this, Windows PowerShell loses the exit code
        if (-not $process.WaitForExit(90 * 1000)) {
            try { $process.Kill() } catch {}
            return $false
        }
        if ($process.ExitCode -eq 0) { return $false }
        $said = (@(Get-Content -Raw -LiteralPath $out, "$out.err" -ErrorAction SilentlyContinue) -join "") -replace "`0", ""
        return $said.ToLower().Contains($VmLogonRefused)
    } finally {
        Remove-Item Env:WSL_UTF8 -ErrorAction SilentlyContinue
        Remove-Item $out, "$out.err" -ErrorAction SilentlyContinue
    }
}

# Puts the virtual machines' sign-in right back (Step 2), behind one
# administrator prompt that runs only the fixed $VmLogonGrant text, from memory.
function Repair-VmLogon {
    Write-Host ""
    Note "Docker can't start on this computer right now: Windows won't let its virtual"
    Note "machine sign in. A Windows policy took away a right it needs (this happens on"
    Note "the Michigan Medicine network)."
    Say "Two ways to fix it:"
    Say "  - Restart Windows (no administrator needed), then run the installer again, or"
    Say "  - Fix it now: Windows asks for administrator permission once, and the right is"
    Say "    given back. If your computer gives you administrator access for a limited"
    Say "    time, request it first."
    Say "It can happen again later; DataLab then offers the same fix when it starts."
    Say "If Docker Desktop is open, fixing it closes and reopens Docker Desktop, so"
    Say "anything running in Docker stops."
    if (-not (Ask "Fix it now?")) {
        Stop-Install "Docker can't start until that's fixed. Restart Windows, then run the installer again."
    }
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($VmLogonGrant))
    Say "Asking Windows for permission now (look for the box; it may be behind this window)..."
    try {
        $grant = Start-Process $WindowsPowerShell -Verb RunAs -Wait -PassThru -WindowStyle Hidden `
            -ArgumentList "-NoProfile -NonInteractive -ExecutionPolicy Bypass -EncodedCommand $encoded"
    } catch {
        Stop-Install ("Windows didn't give administrator permission, so nothing was changed. " +
            "Restart Windows (or get administrator access), then run the installer again.")
    }
    if ($grant.ExitCode -ne 0 -or (Test-VmLogonRefused)) {
        Stop-Install "That didn't fix it. Restart Windows, then run the installer again."
    }
    Good "Docker's virtual machine may start again."
    $script:VmLogonRepaired = $true
    # A Docker Desktop that was already waiting for its engine never tries
    # again, and `docker desktop restart` left that stuck backend running (seen
    # with DataLab 0.3.0b3): so it's closed and its VM stopped, and Step 2
    # opens it afresh.
    $open = @($DockerDesktopImages | ForEach-Object { Get-OwnPids $_ } | Where-Object { $_ })
    if ($open) {
        Say "Closing Docker Desktop, so it starts again with its virtual machine..."
        if (-not (Stop-DockerDesktopProcesses)) {
            Stop-Install ("Windows lets Docker's virtual machine start again, but Docker Desktop " +
                "wouldn't close. Restart Windows, then run the installer again.")
        }
        if (-not (Stop-DockerVm)) {
            Stop-Install ("Windows lets Docker's virtual machine start again, but Docker's virtual " +
                "machine wouldn't stop. Restart Windows, then run the installer again.")
        }
    }
}

# Runs a program and returns its exit code and what it printed, or $null if it
# didn't finish in time. `$arguments` is the command line after the program.
function Invoke-Captured($file, [string]$arguments, $seconds = 30) {
    $info = New-Object System.Diagnostics.ProcessStartInfo $file, $arguments
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $info.EnvironmentVariables["WSL_UTF8"] = "1"  # wsl.exe's own messages in UTF-8
    $process = [System.Diagnostics.Process]::Start($info)
    $out = $process.StandardOutput.ReadToEndAsync()
    $err = $process.StandardError.ReadToEndAsync()
    if (-not $process.WaitForExit($seconds * 1000)) {
        try { $process.Kill() } catch {}
        return $null
    }
    return @{ Code = $process.ExitCode; Out = $out.Result + $err.Result }
}

# The ids of one program's processes (by its exact file name) that run as this
# account: never another account's, nor SYSTEM's. $null when tasklist can't say.
# The account comes from this process's own token, not the environment.
function Get-OwnPids($image) {
    $me = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    $listed = Invoke-Captured (Join-Path $SystemDir "tasklist.exe") `
        "/FI `"IMAGENAME eq $image`" /FI `"USERNAME eq $me`" /FO CSV /NH" 15
    if (-not $listed -or $listed.Code -ne 0) { return $null }
    # No match is one line, "INFO: No tasks are running which match ...". A Docker
    # Desktop started elevated may list with no user name: it isn't found here.
    $rows = @($listed.Out -split "`r?`n" | Where-Object { $_.StartsWith('"') } |
        ConvertFrom-Csv -Header Image, Id, Session, Number, Memory)
    # The comma keeps an empty list a list: `return @()` would give the caller $null.
    return ,@($rows | Where-Object { $_.Image -eq $image -and $_.Id -match '^\d+$' } | ForEach-Object { [int]$_.Id })
}

# Ends Docker Desktop's own processes ($DockerDesktopImages), this account's
# only, by process id. $false if any is still there afterwards.
function Stop-DockerDesktopProcesses {
    $me = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    foreach ($image in $DockerDesktopImages) {
        $ids = Get-OwnPids $image
        if ($null -eq $ids) { return $false }
        # The same filters as the listing, beside the id: an id Windows has
        # handed to another program since isn't ended, nor one that has ended
        # already ("INFO: No tasks running with the specified criteria.").
        foreach ($id in $ids) {
            $null = Invoke-Captured (Join-Path $SystemDir "taskkill.exe") `
                "/F /FI `"IMAGENAME eq $image`" /FI `"USERNAME eq $me`" /PID $id" 15
        }
    }
    # taskkill /F answers before a process has quite gone: look again, once a
    # second, for 15 seconds.
    for ($i = 0; $i -lt 15; $i++) {
        Start-Sleep -Seconds 1
        $left = @($DockerDesktopImages | ForEach-Object { Get-OwnPids $_ })
        if (-not ($left | Where-Object { $null -eq $_ -or $_.Count -gt 0 })) { return $true }
    }
    return $false
}

# Stops Docker's own WSL distribution, and nothing else of WSL's (never
# `wsl --shutdown`). One that isn't there counts as stopped.
function Stop-DockerVm {
    $done = Invoke-Captured (Join-Path $SystemDir "wsl.exe") "--terminate docker-desktop" 60
    if (-not $done) { return $false }
    return ($done.Code -eq 0 -or $done.Out.Contains("WSL_E_DISTRO_NOT_FOUND"))
}

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
    # Windows PowerShell 5.1 may not offer TLS 1.2 by itself.
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $file
    $actual = (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLower()
    if ($actual -ne $sha256) {
        Remove-Item -LiteralPath $file -ErrorAction SilentlyContinue
        throw "The download of $url didn't match its expected checksum, so it wasn't run."
    }
    $signature = Get-AuthenticodeSignature -LiteralPath $file
    $signedBy = Get-Organisation $signature.SignerCertificate
    if ($signature.Status -ne "Valid" -or $signedBy -cne $publisher) {
        Remove-Item -LiteralPath $file -ErrorAction SilentlyContinue
        throw "The download of $url isn't signed by its publisher ($publisher), so it wasn't run."
    }
    # For the log (audits): what was checked, and whose signature it has,
    # as the certificate itself names it.
    Say "Checked: SHA-256 and signature ($signedBy)"
}

# The organisation (O=) a certificate was issued to, exactly as written in it,
# or "" (which never matches a publisher). A value with a comma in it
# ("OpenAI OpCo, LLC") may come back quoted, as O="OpenAI OpCo, LLC" (with any
# quote inside doubled): that's unquoted, so the comparison stays exact.
# Refused ("") unless every line is "KEY=value" and exactly one is O=: a value
# with a line break in it (say, a CN of "x<newline>O=Docker Inc") would
# otherwise read as a line of its own.
function Get-Organisation($certificate) {
    if (-not $certificate) { return "" }
    $name = $certificate.SubjectName.Format($true).TrimEnd("`r", "`n")  # one "KEY=value" per line
    $found = @()
    foreach ($line in $name -split "\r?\n") {
        if ($line -notmatch '^\s*[A-Za-z][A-Za-z0-9.]*=') { return "" }
        if ($line -match '^\s*O=(.*)$') { $found += $Matches[1].Trim() }
    }
    if ($found.Count -ne 1) { return "" }
    $value = $found[0]
    if ($value.Length -ge 2 -and $value.StartsWith('"') -and $value.EndsWith('"')) {
        $value = $value.Substring(1, $value.Length - 2).Replace('""', '"')
    }
    return $value
}

# The SHA-256 of some bytes, written the same way as in the command that starts
# the administrator part (Invoke-AdminPart).
function Get-BytesHash([byte[]]$bytes) {
    return [BitConverter]::ToString([System.Security.Cryptography.SHA256]::Create().ComputeHash($bytes))
}

# Turns off QuickEdit for this console window only: otherwise a click in it
# starts selecting text ("Select" in the title) and pauses the script until
# Esc. It changes this window's mode through the Windows API, nothing saved
# (not the person's console settings, nor the registry). The API call is
# defined in memory: Add-Type would compile code through files in TEMP.
function Disable-QuickEdit {
    try {
        $name = New-Object System.Reflection.AssemblyName("DataLabConsole")
        $assembly = [System.Reflection.Emit.AssemblyBuilder]::DefineDynamicAssembly($name,
            [System.Reflection.Emit.AssemblyBuilderAccess]::Run)
        $type = $assembly.DefineDynamicModule("DataLabConsole").DefineType("DataLabConsole.Native", "Public, Class")
        $calls = @(
            @("GetStdHandle", [IntPtr], [Type[]]@([int])),
            @("GetConsoleMode", [bool], [Type[]]@([IntPtr], [uint32].MakeByRefType())),
            @("SetConsoleMode", [bool], [Type[]]@([IntPtr], [uint32])))
        foreach ($call in $calls) {
            $method = $type.DefinePInvokeMethod($call[0], "kernel32.dll",
                [System.Reflection.MethodAttributes]"Public, Static, PinvokeImpl",
                [System.Reflection.CallingConventions]::Standard, $call[1], $call[2],
                [System.Runtime.InteropServices.CallingConvention]::Winapi,
                [System.Runtime.InteropServices.CharSet]::Auto)
            # Without this, the call's result is dropped (every call returns 0).
            $method.SetImplementationFlags([System.Reflection.MethodImplAttributes]::PreserveSig)
        }
        $native = $type.CreateType()
        $console = $native::GetStdHandle(-10)  # the console's input
        if ($console -eq [IntPtr]::Zero -or $console -eq [IntPtr]-1) { throw "no console input handle" }
        $mode = [uint32]0
        if (-not $native::GetConsoleMode($console, [ref]$mode)) { throw "GetConsoleMode failed" }
        # ENABLE_QUICK_EDIT_MODE (0x40) off; ENABLE_EXTENDED_FLAGS (0x80) makes that apply.
        if (-not $native::SetConsoleMode($console, [uint32](($mode -band (-bnot 0x40)) -bor 0x80))) {
            throw "SetConsoleMode failed"
        }
    } catch {
        # Not a problem for the install, just a note in the log.
        Write-Host "   (QuickEdit stays on in this window ($_): if a click pauses it, press Esc.)"
    }
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
    $system = New-Object System.Security.Principal.SecurityIdentifier($SystemSid)
    $admins = New-Object System.Security.Principal.SecurityIdentifier($AdminsSid)
    # A file's owner may always change its permissions, and Windows can make
    # the person the owner of what an elevated window creates. With this rule
    # an owner gets only what it gives: reading the permissions.
    $ownerRights = New-Object System.Security.Principal.SecurityIdentifier($OwnerRightsSid)
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
# result and log, and check and remove the folder (Remove-RecordedAdminFolder).
# Nothing elevated uses the folder after this.
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
        $person, ($rights::ListDirectory -bor $rights::ReadAttributes -bor $rights::ReadPermissions -bor
            $rights::Delete -bor $rights::Synchronize),
        $noInherit, $none, $allow)))
    $item.SetAccessControl($acl)
}

if ($Prepare) {
    $Host.UI.RawUI.WindowTitle = "DataLab setup (administrator part)"
    Disable-QuickEdit
    $result = @{ ok = $false; restart = $false; error = "" }
    $ready = $false
    $resultFile = Join-Path $WorkDir "result.json"
    $logFile = Join-Path $WorkDir "setup.log"
    try {
        if ($WorkDir -notmatch $AdminFolderPattern) { throw "Unexpected working folder: $WorkDir" }
        # ProgramData itself must be Windows' own: not a link, owned by SYSTEM,
        # TrustedInstaller or Administrators, and nobody else may delete,
        # replace or re-permission what's in it.
        $baseItem = Get-Item -LiteralPath $AdminBase -Force
        $baseAcl = Get-Acl -LiteralPath $AdminBase
        $baseOwner = $baseAcl.GetOwner([System.Security.Principal.SecurityIdentifier]).Value
        $trusted = @($SystemSid, $TrustedInstallerSid, $AdminsSid)
        if (($baseItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -or $baseOwner -notin $trusted) {
            throw "$AdminBase isn't set up the way Windows sets it up (owner $baseOwner), so it wasn't used. Please ask IT to check it."
        }
        $fsr = [System.Security.AccessControl.FileSystemRights]
        $risky = [int]($fsr::DeleteSubdirectoriesAndFiles -bor $fsr::ChangePermissions -bor $fsr::TakeOwnership) -bor 0x10000000  # GENERIC_ALL
        $modify = [int]$fsr::Modify
        $loose = @($baseAcl.GetAccessRules($true, $true, [System.Security.Principal.SecurityIdentifier]) | Where-Object {
            $rights = [int]$_.FileSystemRights
            "$($_.AccessControlType)" -eq "Allow" -and $_.IdentityReference.Value -notin $trusted -and
                -not ($_.PropagationFlags -band [System.Security.AccessControl.PropagationFlags]::InheritOnly) -and
                (($rights -band $risky) -or (($rights -band $modify) -eq $modify))
        })
        if ($loose) {
            $who = ($loose | ForEach-Object { $_.IdentityReference.Value } | Sort-Object -Unique) -join ", "
            throw ("On this computer, other accounts ($who) may delete or change what's in $AdminBase, " +
                "so the administrator part can't keep its downloads safe there. Please ask IT to check the permissions on $AdminBase.")
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
            $install = Start-Process (Join-Path $SystemDir "msiexec.exe") -ArgumentList "/i", "`"$msi`"", "/qn", "/norestart" `
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
        # Checked first, quietly, so the expected cases don't show in the log as errors.
        if (-not (Get-LocalGroup -Name $DockerUsers -ErrorAction SilentlyContinue)) {
            throw "Docker Desktop's '$DockerUsers' group isn't on this computer, so your account can't be added to it. Reinstall Docker Desktop, or ask IT."
        }
        # Any trouble listing the members just means trying to add (below).
        $members = @()
        try { $members = @(Get-LocalGroupMember -Group $DockerUsers -ErrorAction SilentlyContinue) }
        catch { Write-Verbose "Couldn't list $DockerUsers members: $_" }
        if ($members | Where-Object { $_.SID.Value -eq $sid }) {
            Good "You're already in the $DockerUsers group."
        } else {
            try {
                Add-LocalGroupMember -Group $DockerUsers -Member $sid
                Good "Added you to the $DockerUsers group (it takes effect after the restart)."
                $result.restart = $true
            } catch [Microsoft.PowerShell.Commands.MemberExistsException] {
                # (The list above can come back short, e.g. with a member Windows can't name.)
                Good "You're already in the $DockerUsers group."
            }
        }

        Step "Letting Docker's virtual machine start"
        # A policy can take this away again later: Step 2 and DataLab itself
        # check for that and offer the same fix.
        try {
            & ([scriptblock]::Create($VmLogonGrant))
            Good "Its virtual machine has the right it needs to sign in."
        } catch {
            Note "Couldn't give Docker's virtual machine the right it needs to sign in: $_"
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
            foreach ($child in @(Get-ChildItem -LiteralPath $WorkDir -Force)) {
                if ($child.FullName -notin @($resultFile, $logFile)) {
                    if ($child.PSIsContainer) { Remove-Tree $child.FullName }
                    else { Remove-Item -LiteralPath $child.FullName -Force -ErrorAction SilentlyContinue }
                }
            }
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
# Per account, so two people installing on one computer don't share it.
$ResumeTask = "DataLab setup for $MySid (continue after restart)"

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
        $action = New-ScheduledTaskAction -Execute $WindowsPowerShell -Argument $arguments
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
        $link.TargetPath = $WindowsPowerShell
        $link.Arguments = $arguments
        $link.Save()
    }
}

function Unregister-Resume {
    # (Also the name an earlier version of this installer used.)
    foreach ($task in $ResumeTask, "DataLab setup (continue after restart)") {
        Unregister-ScheduledTask -TaskName $task -Confirm:$false -ErrorAction SilentlyContinue
    }
    Remove-Item $ResumeShortcut -ErrorAction SilentlyContinue
}

# Puts the pinned uv in $UvDir, as the person: the release zip is checked for
# its SHA-256 and uv.exe for its publisher's signature before it's used. A
# uv already there is used only if it's that version and still signed.
function Install-PinnedUv {
    # The version, the zip's SHA-256, and uv.exe's own SHA-256 when it was
    # unpacked: a copy is reused only if it's still exactly that file.
    $marker = Join-Path $UvDir "datalab-pinned.txt"
    $want = "$UvVersion $UvZipSha256"
    $recorded = if (Test-Path -LiteralPath $marker) { "$(Get-Content -LiteralPath $marker -TotalCount 1)".Trim() } else { "" }
    if ((Test-Path -LiteralPath $Uv) -and $recorded -eq "$want $((Get-FileHash -LiteralPath $Uv -Algorithm SHA256).Hash.ToLower())") {
        $signature = Get-AuthenticodeSignature -LiteralPath $Uv
        if ($signature.Status -eq "Valid" -and (Get-Organisation $signature.SignerCertificate) -ceq $UvPublisher) {
            Good "uv $UvVersion is installed already."
            return
        }
    }
    if (Test-Path -LiteralPath $UvDir) { Remove-Tree $UvDir }
    if (Test-Path -LiteralPath $UvDir) { Stop-Install "The folder $UvDir couldn't be replaced. Close anything using it, then run the installer again." }
    $download = Join-Path $StateDir ("uv-download-" + [guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Force $download | Out-Null
    try {
        $zip = Join-Path $download "uv.zip"
        Say "Downloading uv $UvVersion (from github.com/astral-sh/uv)..."
        $ProgressPreference = "SilentlyContinue"
        [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest -UseBasicParsing -Uri $UvZipUrl -OutFile $zip
        if ((Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash.ToLower() -ne $UvZipSha256) {
            Stop-Install "The download of uv didn't match its expected checksum, so it wasn't used."
        }
        $unpacked = Join-Path $download "uv"
        Expand-Archive -LiteralPath $zip -DestinationPath $unpacked
        $signature = Get-AuthenticodeSignature -LiteralPath (Join-Path $unpacked "uv.exe")
        $signedBy = Get-Organisation $signature.SignerCertificate
        if ($signature.Status -ne "Valid" -or $signedBy -cne $UvPublisher) {
            Stop-Install "uv.exe isn't signed by its publisher ($UvPublisher), so it wasn't used."
        }
        Say "Checked: SHA-256 and signature ($signedBy)"
        $uvHash = (Get-FileHash -LiteralPath (Join-Path $unpacked "uv.exe") -Algorithm SHA256).Hash.ToLower()
        Move-Item -LiteralPath $unpacked -Destination $UvDir
        Set-Content -LiteralPath $marker -Value "$want $uvHash" -Encoding ASCII
    } finally {
        Remove-Tree $download
    }
    Good "uv $UvVersion is ready."
}

# What of DataLab is running, in words, or "" if nothing: a DataLab process
# (the side-by-side versions under $root, or an older installer's copy under
# uv's tools folder), or something listening on DataLab's ports (8765 real,
# 8766 practice).
function Get-RunningDataLab($root) {
    $found = @()
    $places = @((Join-Path $root "versions"), (Join-Path $Env:APPDATA "uv\tools\datalab"))
    # (The older installer's command, which starts that copy.)
    $oldCommand = Join-Path $Env:USERPROFILE ".local\bin\datalab.exe"
    foreach ($process in @(Get-Process -ErrorAction SilentlyContinue)) {
        $path = $null
        try { $path = $process.Path } catch { Write-Verbose "No path for process $($process.Id)" }
        if (-not $path) { continue }
        if ([string]::Equals($path, $oldCommand, [StringComparison]::OrdinalIgnoreCase)) { $found += "process $($process.Id)" }
        foreach ($place in $places) {
            if ($path.StartsWith($place + [System.IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
                $found += "process $($process.Id)"
            }
        }
    }
    try {
        $listening = [System.Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners()
        foreach ($port in 8765, 8766) {
            if ($listening | Where-Object { $_.Port -eq $port }) { $found += "something on port $port" }
        }
    } catch { Write-Verbose "Couldn't list the listening ports: $_" }
    return (@($found | Select-Object -Unique) -join ", ")
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
Remove-RecordedAdminFolder $MySid

$saved = $null
if (Test-Path $ResumeFile) { $saved = Get-Content $ResumeFile -Raw | ConvertFrom-Json }
if ($Resume) {
    if (-not $saved) { Write-Host "There's no DataLab install waiting to continue."; exit 2 }
    $Package = $saved.Package; $Settings = $saved.Settings; $Requirements = $saved.Requirements
    $Practice = [bool]$saved.Practice; $NoGitHub = [bool]$saved.NoGitHub
    Write-Host ""
    Write-Host "Welcome back! Let's finish setting up DataLab." -ForegroundColor Cyan
    Write-Host "This part shouldn't need administrator permission. If it does, it says why first."
} else {
    Write-Host ""
    Write-Host "Welcome! This sets up DataLab on this computer." -ForegroundColor Cyan
    Write-Host "It takes about 20-30 minutes, most of it downloading. It walks you through"
    Write-Host "each step and tells you whenever it needs you to do something."
}
if (-not $Package) {
    # The package beside this script: download the installer and the package
    # (and requirements.txt) from the same release into one folder.
    $besideScript = @(Get-ChildItem -LiteralPath $ScriptFolder -File -Filter "datalab-*-py3-none-any.whl" -ErrorAction SilentlyContinue)
    if ($besideScript.Count -eq 1) { $Package = $besideScript[0].FullName }
    elseif ($besideScript.Count -eq 0) {
        Write-Host "The DataLab package (datalab-<version>-py3-none-any.whl) isn't in the installer's folder,"
        Write-Host "$ScriptFolder. Download it from the same release into that folder, or pass -Package <file>."
        exit 2
    } else {
        Write-Host "There's more than one DataLab package in $ScriptFolder"
        Write-Host "($(($besideScript | ForEach-Object Name) -join ', ')). Pass the one to install: -Package <file>."
        exit 2
    }
}

# Check the package before anything else, so a wrong path is found before
# any administrator step or restart.
$IsUrl = $Package -match '^[a-zA-Z][a-zA-Z0-9+.-]*://'
if (-not $IsUrl) {
    # A bare file name ("datalab-....whl") has no parent folder of its own, so
    # resolve it first: requirements.txt is looked for next to the real file.
    if (-not (Test-Path -LiteralPath $Package -PathType Leaf)) { Write-Host "Package file not found: $Package"; exit 2 }
    $Package = (Resolve-Path -LiteralPath $Package).ProviderPath
}
if (-not $Requirements -and -not $IsUrl) {
    $Beside = Join-Path ([System.IO.Path]::GetDirectoryName($Package)) "requirements.txt"
    if (Test-Path -LiteralPath $Beside) { $Requirements = $Beside }
}
if (-not $Requirements) { Write-Host "requirements.txt (every dependency, pinned by hash) wasn't found. Pass -Requirements."; exit 2 }
$Requirements = (Resolve-Path -LiteralPath $Requirements).ProviderPath
# The package's file name carries its version: datalab-<version>-py3-none-any.whl
$FileName = [System.IO.Path]::GetFileName(($Package -split '\?')[0])
if ($FileName -notmatch '^datalab-([0-9](?:[A-Za-z0-9.+!]*[A-Za-z0-9])?)-py3-none-any\.whl$') {
    Write-Host "The package must be a datalab-<version>-py3-none-any.whl file."; exit 2
}
$Version = $Matches[1]
# (Not $Profile: PowerShell uses that name for its own profile script.)
$DataLabProfile = if ($Practice) { "practice" } else { "real" }
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
    @{ Package = $Package; Settings = $Settings; Requirements = $Requirements; Prepared = $prepared
        Practice = [bool]$Practice; NoGitHub = [bool]$NoGitHub
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
    Note "a few moments later and carries on from here. It shouldn't need"
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
#
# The command is passed with -EncodedCommand, and the values in it (the copy's
# path, the SID, the folder) as Base64 text, so no quoting is involved: a
# path with any kind of quote in it can't change the command. Before anything
# else, the command limits where PowerShell looks for modules to Windows' own
# folders and turns off loading them automatically, then loads the ones the
# administrator part uses from $PSHOME: the person's own module folders
# (Documents, or PSModulePath in their environment) are never used elevated.
function Invoke-AdminPart {
    if (-not $ScriptText) { Stop-Install "Run the installer from its file: powershell -NoProfile -ExecutionPolicy Bypass -File install.ps1 ..." }
    $work = Join-Path $AdminBase ($AdminFolderPrefix + [guid]::NewGuid().ToString("N"))
    $copy = Join-Path $Env:TEMP ($AdminFolderPrefix + [guid]::NewGuid().ToString("N") + ".ps1")
    $bytes = (New-Object System.Text.UTF8Encoding($false)).GetBytes($ScriptText)
    [System.IO.File]::WriteAllBytes($copy, $bytes)
    # Recorded first, so this exact folder (and only it) is removed later, even
    # if this window is closed before the administrator part finishes.
    New-Item -ItemType Directory -Force $StateDir | Out-Null
    Add-Content -LiteralPath $AdminRecord -Value $work -Encoding UTF8
    $data = { param($text) "(D '" + [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($text)) + "')" }
    # The window stays open to show a problem: -Yes waits a while, otherwise it
    # waits for Enter. Only $Host is used for that, as other commands may not
    # have loaded yet.
    $pause = if ($Yes) { "[Threading.Thread]::Sleep(20000)" } else { "`$Host.UI.WriteLine('Press Enter to close this window.'); `$null = `$Host.UI.ReadLine()" }
    $command = @(
        "`$ErrorActionPreference = 'Stop'"
        "try {"
        "`$env:PSModulePath = `$PSHOME + '\Modules;' + [Environment]::GetFolderPath('ProgramFiles') + '\WindowsPowerShell\Modules'"
        "`$PSModuleAutoLoadingPreference = 'None'"
        "foreach (`$m in 'Microsoft.PowerShell.Management', 'Microsoft.PowerShell.Utility', 'Microsoft.PowerShell.Security', " +
            "'Microsoft.PowerShell.Host', 'Dism', 'Microsoft.PowerShell.LocalAccounts') { Import-Module (`$PSHOME + '\Modules\' + `$m) }"
        "function D(`$t) { [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String(`$t)) }"
        "`$b = [IO.File]::ReadAllBytes($(& $data $copy))"
        "`$h = [BitConverter]::ToString([Security.Cryptography.SHA256]::Create().ComputeHash(`$b))"
        "if (`$h -ne '$(Get-BytesHash $bytes)') { `$Host.UI.WriteErrorLine('The installer changed on disk after it started, so nothing was run.'); $pause; exit 1 }"
        "& ([scriptblock]::Create([Text.Encoding]::UTF8.GetString(`$b))) -Prepare -ForUserSid $(& $data $MySid) -WorkDir $(& $data $work)$(if ($Yes) { ' -Yes' })"
        "} catch {"
        "`$Host.UI.WriteErrorLine('The administrator part could not start: ' + `$_); $pause; exit 1"
        "}"
    ) -join "`n"
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($command))
    Say "Asking Windows for permission now (look for the box; it may be behind this window)..."
    try {
        $null = Start-Process $WindowsPowerShell -Verb RunAs -Wait -PassThru `
            -ArgumentList "-NoProfile -ExecutionPolicy Bypass -EncodedCommand $encoded"
    } catch {
        Stop-Install ("Windows didn't give administrator permission (the box was closed or 'No' was " +
            "clicked, or your temporary administrator access has run out), so nothing was changed. " +
            "Get administrator access again (or ask IT for it), then run the installer again." +
            "`n   (Windows said: $($_.Exception.Message))")
    } finally {
        Remove-Item -LiteralPath $copy -ErrorAction SilentlyContinue
    }
    $resultFile = Join-Path $work "result.json"
    if (-not (Test-AdminOwned $work) -or -not (Test-Path -LiteralPath $resultFile)) {
        Stop-Install "The administrator part closed before it finished."
    }
    $outcome = Get-Content -LiteralPath $resultFile -Raw | ConvertFrom-Json
    if (-not $outcome.ok) {
        Stop-Install ("The administrator part didn't finish: $($outcome.error)`n   " +
            "Details are saved in $(Join-Path $work 'setup.log')")
    }
    Remove-RecordedAdminFolder $MySid
    return $outcome
}

# Offers to run the administrator part again after a restart didn't fix
# something, then restarts. Never with -Yes: unattended, it would restart at
# every sign-in.
function Invoke-AdminPartAgain {
    if ($Yes) { return }
    Write-Host ""
    Say "You can also run the administrator part again now. If something on this"
    Say "computer undoes it, it won't last past the next restart either."
    if (Ask "Run the administrator part again?") {
        $null = Invoke-AdminPart
        Good "Windows is set up."
        Request-Restart
    }
}

# The administrator part ran and Windows restarted, but something it did is
# needed again. Running it (and restarting) again and again won't help, so
# this stops and says what's missing.
function Stop-StillMissing([string[]]$items) {
    Save-Progress $true  # keeps the count of restarts
    Write-Host ""
    Note "Windows has restarted after the administrator part, but this is still needed:"
    $items | ForEach-Object { Say "  - $_" }
    Say "On a managed computer, the likely cause is a policy that undoes it (for example,"
    Say "one that sets Docker Desktop's service back to starting by hand), or WSL or"
    Say "Docker Desktop being installed somewhere other than the usual place. Ask IT"
    Say "(the service desk), and show them this list."
    Invoke-AdminPartAgain
    Stop-Install "Windows isn't ready for Docker yet (see above)."
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
    Invoke-AdminPartAgain
    Stop-Install "Your account can't use Docker yet (see above)."
}

Step "Step 1 of 8: Getting Windows ready (WSL and Docker Desktop)"
Say "DataLab runs its analysis in Docker, a sealed-off space on your computer."
Say "Docker needs a Windows feature called WSL."
# WSL 2 needs hardware virtualization, which only the computer's firmware can
# turn on (administrator rights can't). Found out here, before any
# administrator step, download or restart, not after them. With a hypervisor
# already running (e.g. Credential Guard), the processor reports firmware
# virtualization as off, so a running hypervisor counts as on. If Windows
# can't say, carry on: an unknown isn't a refusal.
if (-not (Test-VirtualizationOn)) {
    Stop-Install ("This computer has virtualization turned off in its firmware (the settings " +
        "below Windows). DataLab needs it, and Windows settings or administrator rights can't " +
        "change it. Ask IT (the service desk) to turn on 'Intel Virtualization Technology " +
        "(VT-x)' or 'AMD-V / SVM' in this computer's firmware, then run the installer again. " +
        "Nothing was changed on this computer.")
}
$missing = @()
if (-not (Test-Path $WslExe)) { $missing += "Turn on WSL and install it (WSL $WslVersion, from Microsoft)" }
if (-not (Test-Path $DockerDesktop)) { $missing += "Install Docker Desktop $DockerVersion (from Docker)" }
if (Test-DockerServiceManual) { $missing += "Let Docker Desktop start without an administrator" }
$prepared = ($saved -and $saved.Prepared) -or (Test-InDockerUsers $MySid)
if (-not (Test-CanUseDocker) -and -not $prepared) { $missing += "Give your account permission to use Docker" }

# After the administrator part and a restart, the same things shouldn't be
# missing again; if they are, running it again every time won't help.
if ($missing -and $saved -and $saved.Prepared -and $Restarts -ge 1) { Stop-StillMissing $missing }
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

Step "Step 2 of 8: Starting Docker Desktop"
if (-not (Test-DockerRunning)) {
    Say "Starting Docker Desktop. The first start can take a few minutes."
    Say "If Docker Desktop shows a welcome screen or asks you to sign in, you can"
    Say "skip it: DataLab doesn't need a Docker account. Leave Docker Desktop running."
    Clear-StaleDockerSockets
    if (Test-VmLogonRefused) { Repair-VmLogon }
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
    if (-not $running -and $script:VmLogonRepaired) {
        Stop-Install ("Windows lets Docker's virtual machine start again, but Docker Desktop didn't " +
            "get ready within 10 minutes. Restart Windows, then run the installer again.")
    }
    if (-not $running) {
        Stop-Install ("Docker Desktop didn't finish starting. Open it from the Start menu, wait until " +
            "it says 'Engine running' (bottom left), then run the installer again.")
    }
}
Good "Docker Desktop is running."

Step "Step 3 of 8: Installing uv (the tool that installs DataLab)"
# Nothing from the environment may steer uv or pip (another index, checks
# turned off) or Python.
@(Get-ChildItem Env: | Where-Object { $_.Name -match '^(UV|PIP)_' -or $_.Name -in 'PYTHONPATH', 'PYTHONHOME', 'VIRTUAL_ENV' }) |
    ForEach-Object { Remove-Item -LiteralPath "Env:$($_.Name)" }
Install-PinnedUv
& $Uv --version

Step "Step 4 of 8: Installing DataLab"
# Beside the data folders: versions\<version>\, current, previous, bin\datalab.cmd.
# The real and practice DataLabs share them.
$Root = if ($Env:DATALAB_INSTALL_DIR) { $Env:DATALAB_INSTALL_DIR } else { Join-Path $StateDir "app" }
# A DataLab that's running holds its files open, and an update of the
# version it runs couldn't replace them; its data shouldn't change under it
# either. So it's closed first, never stopped by the installer.
while ($true) {
    $running = Get-RunningDataLab $Root
    if (-not $running) { break }
    Write-Host ""
    Note "DataLab is running ($running). Close it first: close its window, or press"
    Note "Ctrl-C in it."
    if ($Yes) { Stop-Install "DataLab is running, so it wasn't changed. Close it, then run the installer again." }
    $answer = Read-Host "   Press Enter once it's closed (or type q to stop here)"
    if ($answer -match '^q') { Say "OK. Nothing was changed; run the installer again when DataLab is closed."; exit 1 }
}
# uv cuts a path at its first space ("failed to read from file
# C:\Users\me\OneDrive"), and on Michigan Medicine computers downloads usually
# sit under "OneDrive - Michigan Medicine". So uv gets the package and
# requirements.txt under plain names, from their own folder.
$Stage = Join-Path $StateDir "install"
if (Test-Path -LiteralPath $Stage) { Remove-Tree $Stage }
New-Item -ItemType Directory -Force $Stage | Out-Null
if ($IsUrl) {
    if ($Package -notmatch '^https://') { Stop-Install "Only https downloads: $Package" }
    $ProgressPreference = "SilentlyContinue"
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -UseBasicParsing -Uri $Package -OutFile (Join-Path $Stage $FileName)
} else {
    Copy-Item -LiteralPath $Package (Join-Path $Stage $FileName) -Force
}
Copy-Item -LiteralPath $Requirements (Join-Path $Stage "requirements.txt") -Force
# The package must be the one requirements.txt names, by its checksum.
$Sha256 = (Get-FileHash -LiteralPath (Join-Path $Stage $FileName) -Algorithm SHA256).Hash.ToLower()
$Pinned = Get-Content -LiteralPath (Join-Path $Stage "requirements.txt") | Where-Object { $_ -ceq "./$FileName --hash=sha256:$Sha256" }
if (-not $Pinned) {
    Stop-Install ("requirements.txt doesn't name this package with this checksum. Use the two " +
        "files from the same DataLab release.")
}
Say "Checked: SHA-256 of $FileName (as requirements.txt names it)"
$Target = Join-Path $Root "versions\$Version"
$Complete = Join-Path $Target ".complete"
if ((Test-Path -LiteralPath $Complete) -and ((Get-Content -LiteralPath $Complete -Raw) -match "`"wheel_sha256`": `"$Sha256`"")) {
    Good "DataLab $Version is installed already."
} else {
    # A folder without .complete (or with another package) is replaced.
    if (Test-Path -LiteralPath $Target) { Remove-Tree $Target }
    if (Test-Path -LiteralPath $Target) { Stop-Install "The folder $Target couldn't be replaced. Close DataLab, then run the installer again." }
    New-Item -ItemType Directory -Force -Path (Join-Path $Root "versions") | Out-Null
    & $Uv venv -q --no-config --python 3.13 $Target
    if ($LASTEXITCODE -ne 0) { Stop-Install "Making DataLab's Python environment didn't work (see the messages above)." }
    # Every file checked against requirements.txt's hashes, only wheels, and only from PyPI.
    Push-Location $Stage
    try {
        # Copies, not hardlinks into uv's cache: a hardlink fails in a
        # cloud-synced or redirected folder ("incompatible hardlinks").
        & $Uv pip install -q --no-config --require-hashes --only-binary :all: `
            --default-index https://pypi.org/simple --link-mode copy `
            --python (Join-Path $Target "Scripts\python.exe") -r requirements.txt
    } finally { Pop-Location }
    if ($LASTEXITCODE -ne 0) {
        Remove-Tree $Target
        Stop-Install "Installing DataLab didn't work (see the messages above)."
    }
    $Said = & (Join-Path $Target "Scripts\datalab.exe") --version
    if ($Said -ne "datalab $Version") {
        Remove-Tree $Target
        Stop-Install "The installed DataLab says '$Said', not $Version."
    }
    $Stamp = Get-Date -Format "yyyy-MM-ddTHH:mm:sszzz"
    Set-Content -LiteralPath $Complete -Encoding ASCII `
        -Value "{`"version`": `"$Version`", `"wheel_sha256`": `"$Sha256`", `"installed_at`": `"$Stamp`"}"
}
Remove-Tree $Stage
# The Start menu entry runs bin\datalab.cmd, which opens whichever version
# `current` names: an update switches that, and keeps `previous`.
$Bin = Join-Path $Root "bin"
New-Item -ItemType Directory -Force -Path $Bin | Out-Null
$Shim = @'
@echo off
rem Runs the DataLab version the launcher opens (named in ..\current).
setlocal
rem UTF-8 for Python's own text files and console, whatever the code page.
set PYTHONUTF8=1
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
# An earlier installer's copy (uv tool install): the Start menu no longer
# opens it, and a second "datalab" command would only confuse. DataLab isn't
# running (checked above), so its files can go.
if (Test-Path -LiteralPath (Join-Path $Env:APPDATA "uv\tools\datalab")) {
    # (Invoke-Quiet: uv's messages on its error output would stop this script.)
    $code = Invoke-Quiet $Uv @("tool", "uninstall", "datalab") 120
    if ($code -eq 0) { Say "(Removed the copy of DataLab an earlier installer made.)" }
}

Step "Step 5 of 8: Downloading DataLab's containers (a few GB; this takes a while)"
& $DataLab --profile $DataLabProfile pull-images
if ($LASTEXITCODE -ne 0) {
    Stop-Install ("Downloading the containers didn't work (the messages above say why). If you're " +
        "offline, reconnect, then run the installer again.")
}

Step "Step 6 of 8: Your keys"
if ($Practice) {
    Say "Next, DataLab asks for your U-M GPT API key. It's optional for practice: press"
    Say "Enter to skip it. No database password, VPN or GitHub account is needed."
} else {
    Say "Next, DataLab asks for your U-M GPT API key (and the database password, if"
    Say "your lab uses one)."
}
Say "Each character shows as *. Press Enter when done. Keys are kept in Windows Credential Manager."
if ($Settings) { & $DataLab --profile $DataLabProfile setup --settings $Settings } else { & $DataLab --profile $DataLabProfile setup }
if ($LASTEXITCODE -ne 0) {
    Stop-Install "Saving your keys didn't work (the messages above say why)."
}

if ($Practice) {
    Step "Step 7 of 8: Setting up the practice database"
} else {
    Step "Step 7 of 8: The lab's knowledge base and pipelines"
}
if ($Practice) {
    Say "Skipped: practice DataLab doesn't use the lab's repositories. Instead it runs its"
    Say "own database of made-up data, in Docker, reachable from this computer only."
    Say "The first time, this takes a few minutes; data it already has is kept."
    # Not a reason to stop: DataLab sets it up (or starts it) each time it opens.
    & $DataLab --profile $DataLabProfile practice-db setup
    if ($LASTEXITCODE -ne 0) {
        Note "It isn't ready yet (the messages above say why). DataLab tries again each time it opens."
    }
} elseif ($NoGitHub -or $Yes) {
    # (-Yes runs unattended; the sign-in needs the person at github.com.)
    Say "Skipped. Sign in later in DataLab, under Settings > GitHub."
} elseif (Ask "Sign in to GitHub now, to download them?") {
    Say "DataLab shows a code: open the page it names, enter the code, and approve."
    # As the person, never elevated: the sign-in goes in their own Credential Manager.
    # 2: not set up here (the lab's settings don't name the repos); it says so.
    # 1: not signed in, or no access to a repo; it says whom to ask.
    & $DataLab --profile $DataLabProfile github sign-in
    if ($LASTEXITCODE -ne 2) {
        & $DataLab --profile $DataLabProfile repos sync
        if ($LASTEXITCODE -ne 0) { Note "You can sync again later in DataLab (Knowledge and Pipelines)." }
    }
} else {
    Say "Skipped. Sign in later in DataLab, under Settings > GitHub."
}

Step "Step 8 of 8: Adding DataLab to the Start menu and the Desktop"
# DataLab runs in a PowerShell window, so it's easy to see it's running and to
# quit (close the window or press Ctrl-C). It opens the version `current`
# names, which is what an update switches. Real and practice each have their
# own entry, so neither replaces the other.
$StartMenu = Join-Path $Env:APPDATA "Microsoft\Windows\Start Menu\Programs"
$LinkName = if ($Practice) { "DataLab (practice)" } else { "DataLab" }
# An earlier installer's "DataLab" entry opens the uv tool copy removed in
# step 4, so it would only give an error. It goes, but only if that's what it
# opens (a real DataLab's own entry is left alone, or rewritten just below).
$Earlier = Join-Path $StartMenu "DataLab.lnk"
if (Test-Path -LiteralPath $Earlier) {
    $Link = (New-Object -ComObject WScript.Shell).CreateShortcut($Earlier)
    if ("$($Link.TargetPath) $($Link.Arguments)" -like "*\.local\bin\datalab*") {
        Remove-Item -LiteralPath $Earlier -Force
        Say "(Removed the Start menu entry an earlier installer made.)"
    }
}
# Each profile has its own icon (practice: the same mark on cream, with an
# amber edge). It comes with the package, and is copied beside bin\ so an
# update that later removes this version's folder doesn't take it away.
$IconName = if ($Practice) { "DataLab-practice.ico" } else { "DataLab.ico" }
$Icons = Join-Path $Root "icons"
$Icon = Join-Path $Icons $IconName
$PackagedIcon = Join-Path $Target "Lib\site-packages\datalab\branding\$IconName"
if (Test-Path -LiteralPath $PackagedIcon -PathType Leaf) {
    New-Item -ItemType Directory -Force -Path $Icons | Out-Null
    Copy-Item -LiteralPath $PackagedIcon $Icon -Force
}
# The same entry in the Start menu and on the Desktop. Both run bin\datalab.cmd,
# never a version's own folder, so they keep working after an update.
$QuotedShim = [System.Management.Automation.Language.CodeGeneration]::EscapeSingleQuotedStringContent($DataLab)
$Desktop = [Environment]::GetFolderPath("Desktop")
$Links = @(Join-Path $StartMenu "$LinkName.lnk")
if ($Desktop) {
    # A Desktop shortcut of that name is replaced only if it's DataLab's (it
    # runs this bin\datalab.cmd); anything else there is the person's own.
    $DesktopLink = Join-Path $Desktop "$LinkName.lnk"
    $Existing = if (Test-Path -LiteralPath $DesktopLink) { (New-Object -ComObject WScript.Shell).CreateShortcut($DesktopLink) } else { $null }
    if ($Existing -and "$($Existing.Arguments)".IndexOf("'$QuotedShim'", [StringComparison]::OrdinalIgnoreCase) -lt 0) {
        Note "Your Desktop already has a shortcut called $LinkName that isn't DataLab's; it was left alone."
        $Desktop = ""
    } else {
        $Links += $DesktopLink
    }
}
foreach ($path in $Links) {
    $Shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut($path)
    $Shortcut.TargetPath = $WindowsPowerShell
    $Shortcut.Arguments = "-NoProfile -NoExit -Command `"& '$QuotedShim' --profile $DataLabProfile serve`""
    $Shortcut.Description = if ($Practice) { "IHS DataLab (practice: synthetic data only)" } else { "IHS DataLab" }
    if (Test-Path -LiteralPath $Icon -PathType Leaf) { $Shortcut.IconLocation = "$Icon,0" }
    $Shortcut.Save()
}
Good "Added $LinkName to the Start menu$(if ($Desktop) { ' and the Desktop' })."
Remove-Item $ResumeFile -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "All done! DataLab is installed." -ForegroundColor Green
Say "Start menu entry:  $(Join-Path $StartMenu "$LinkName.lnk")"
if ($Desktop) { Say "Desktop shortcut:  $(Join-Path $Desktop "$LinkName.lnk")" }
Say "Program files:     $Root"
Say "To open it: double-click $LinkName on your Desktop, or Start menu > type $LinkName"
Say "> press Enter. It opens in your browser."
Say "A small window stays open while DataLab runs; close it to quit DataLab."
Say "(Or run: `"$DataLab`" --profile $DataLabProfile serve)"
} finally {
    $SetupLock.Dispose()
}
