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
    # Internal: the one part that runs as administrator (step 1).
    [switch]$Prepare,
    [string]$ForUserSid = "",
    [string]$ResultFile = ""
)
$ErrorActionPreference = "Stop"
$UvVersion = "0.12.19"
# Pinned downloads, checked before they run.
$WslVersion = "2.7.14"
$WslMsiUrl = "https://github.com/microsoft/WSL/releases/download/2.7.14/wsl.2.7.14.0.x64.msi"
$WslMsiSha256 = "db084e536279a59e90a26ec598d8aa8a4dff8309f41d078fd06242953ac1ebcd"
$DockerVersion = "4.77.0"
$DockerUrl = "https://desktop.docker.com/win/main/amd64/228796/Docker%20Desktop%20Installer.exe"
$DockerSha256 = "5b866599f0de9208f4594d64aa33658fa55cbdd64e0db13648cffe12c91795d2"
$DockerAgreement = "https://www.docker.com/legal/docker-subscription-service-agreement/"

$StateDir = Join-Path $Env:LOCALAPPDATA "DataLab"
$ResumeFile = Join-Path $StateDir "installer-resume.json"
$ResumeTask = "DataLab setup (continue after restart)"
$ResumeShortcut = Join-Path ([Environment]::GetFolderPath("Startup")) "DataLab setup.lnk"
$DockerDesktop = Join-Path $Env:ProgramFiles "Docker\Docker\Docker Desktop.exe"
$WslExe = Join-Path $Env:ProgramFiles "WSL\wsl.exe"
# The group Docker Desktop creates for the people allowed to use it; its SID
# differs per computer, so it's matched by name.
$DockerUsers = "docker-users"

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
# take effect. Matched by SID, so no domain controller is needed; if the group
# can't be read, the administrator part checks again.
function Test-InDockerUsers($sid) {
    try {
        $members = Get-LocalGroupMember -Group $DockerUsers -ErrorAction Stop
    } catch { return $false }
    return [bool]($members | Where-Object { $_.SID.Value -eq $sid })
}

# A Docker Desktop that was uninstalled (or crashed) can leave its socket files
# behind. Windows can't open or delete them ("The file cannot be accessed by the
# system"), and the next Docker Desktop then fails to start on them. They can
# still be moved, so each folder holding them is renamed aside and Docker makes
# a fresh one. Only while Docker Desktop isn't running at all.
function Clear-StaleDockerSockets {
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
        } catch {}
    }
}

function Save-Download($url, $sha256, $file) {
    Say "Downloading $([System.Uri]::UnescapeDataString(($url -split '/')[-1]))..."
    $ProgressPreference = "SilentlyContinue"  # the progress bar makes big downloads much slower
    Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $file
    $actual = (Get-FileHash $file -Algorithm SHA256).Hash.ToLower()
    if ($actual -ne $sha256) {
        Remove-Item $file -ErrorAction SilentlyContinue
        throw "The download of $url didn't match its expected checksum, so it wasn't run."
    }
}

# Opens the installer again after the next sign-in. A logon task for this
# account, not a RunOnce entry: managed Windows machines were seen skipping
# RunOnce entirely. If the task can't be made, a Startup-folder shortcut does it.
function Register-Resume {
    $arguments = "-NoProfile -ExecutionPolicy Bypass -NoExit -File `"$PSCommandPath`" -Resume"
    try {
        $me = "$Env:USERDOMAIN\$Env:USERNAME"
        $action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $arguments
        $trigger = New-ScheduledTaskTrigger -AtLogOn -User $me
        $trigger.Delay = "PT20S"  # let the desktop finish appearing first
        $principal = New-ScheduledTaskPrincipal -UserId $me -LogonType Interactive -RunLevel Limited
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

# ---------------------------------------------------------------------------
# The administrator part. Runs in its own window, started from step 1 below.
# ---------------------------------------------------------------------------
if ($Prepare) {
    $Host.UI.RawUI.WindowTitle = "DataLab setup (administrator part)"
    $result = @{ ok = $false; restart = $false; error = "" }
    Start-Transcript -Path ([System.IO.Path]::ChangeExtension($ResultFile, ".log")) | Out-Null
    try {
        Write-Host "DataLab setup: the administrator part" -ForegroundColor Cyan
        Write-Host "Please leave this window open. It closes by itself when it's done."
        Write-Host "The downloads are large (about 900 MB), so this can take 10 minutes or more."
        $downloads = Join-Path $Env:TEMP "datalab-installer"
        New-Item -ItemType Directory -Force $downloads | Out-Null

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
            $msi = Join-Path $downloads "wsl.$WslVersion.x64.msi"
            Save-Download $WslMsiUrl $WslMsiSha256 $msi
            Say "Installing WSL $WslVersion..."
            $install = Start-Process msiexec.exe -ArgumentList "/i", "`"$msi`"", "/qn", "/norestart" -Wait -PassThru
            if ($install.ExitCode -eq 3010) { $result.restart = $true }
            elseif ($install.ExitCode -ne 0) { throw "Installing WSL failed (msiexec exit code $($install.ExitCode))." }
            Remove-Item $msi -ErrorAction SilentlyContinue
            Good "Installed WSL."
        }

        Step "Installing Docker Desktop"
        if (Test-Path $DockerDesktop) {
            Good "Docker Desktop is already installed."
        } else {
            $installer = Join-Path $downloads "Docker Desktop Installer.exe"
            Save-Download $DockerUrl $DockerSha256 $installer
            $signature = Get-AuthenticodeSignature $installer
            if ($signature.Status -ne "Valid" -or $signature.SignerCertificate.Subject -notmatch "O=Docker Inc") {
                throw "The Docker Desktop installer isn't signed by Docker, so it wasn't run."
            }
            Say "Installing Docker Desktop $DockerVersion (this takes a few minutes, with no progress shown)..."
            # --always-run-service: Docker Desktop can then start without an administrator.
            $install = Start-Process $installer -ArgumentList "install", "--quiet", "--accept-license", `
                "--backend=wsl-2", "--always-run-service" -Wait -PassThru
            if ($install.ExitCode -ne 0) { throw "Installing Docker Desktop failed (exit code $($install.ExitCode))." }
            Remove-Item $installer -ErrorAction SilentlyContinue
            Good "Installed Docker Desktop."
            $result.restart = $true
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
        Read-Host "Press Enter to close this window (the main installer window explains what to do)"
    } finally {
        $result | ConvertTo-Json | Set-Content -Encoding UTF8 $ResultFile
        Stop-Transcript | Out-Null
    }
    if ($result.ok) { exit 0 } else { exit 1 }
}

# ---------------------------------------------------------------------------
# The installer, run as the person installing.
# ---------------------------------------------------------------------------
$Host.UI.RawUI.WindowTitle = "DataLab setup"
$saved = $null
if (Test-Path $ResumeFile) { $saved = Get-Content $ResumeFile -Raw | ConvertFrom-Json }
if ($Resume) {
    if (-not $saved) { Write-Host "There's no DataLab install waiting to continue."; exit 2 }
    $Package = $saved.Package; $Settings = $saved.Settings; $Constraints = $saved.Constraints
    Unregister-Resume  # opened once; if it stops again, it asks for another restart itself
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

function Save-Progress($prepared) {
    New-Item -ItemType Directory -Force $StateDir | Out-Null
    @{ Package = $Package; Settings = $Settings; Constraints = $Constraints; Prepared = $prepared } |
        ConvertTo-Json | Set-Content -Encoding UTF8 $ResumeFile
}

function Request-Restart {
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

Step "Step 1 of 7: Getting Windows ready (WSL and Docker Desktop)"
Say "DataLab runs its analysis in Docker, a sealed-off space on your computer."
Say "Docker needs a Windows feature called WSL."
$missing = @()
if (-not (Test-Path $WslExe)) { $missing += "Turn on WSL and install it (WSL $WslVersion, from Microsoft)" }
if (-not (Test-Path $DockerDesktop)) { $missing += "Install Docker Desktop $DockerVersion (from Docker)" }
$mySid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$prepared = ($saved -and $saved.Prepared) -or (Test-InDockerUsers $mySid)
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

    $ResultFile = Join-Path $Env:TEMP "datalab-prepare.json"
    Remove-Item $ResultFile -ErrorAction SilentlyContinue
    $arguments = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$PSCommandPath`"",
        "-Prepare", "-ForUserSid", $mySid, "-ResultFile", "`"$ResultFile`"")
    Say "Asking Windows for permission now (look for the box; it may be behind this window)..."
    try {
        $null = Start-Process powershell.exe -Verb RunAs -ArgumentList $arguments -Wait -PassThru
    } catch {
        Stop-Install ("Windows didn't give administrator permission (the box was closed or 'No' was " +
            "clicked, or your temporary administrator access has run out), so nothing was changed. " +
            "Get administrator access again (or ask IT for it), then run the installer again." +
            "`n   (Windows said: $($_.Exception.Message))")
    }
    if (-not (Test-Path $ResultFile)) { Stop-Install "The administrator part closed before it finished." }
    $outcome = Get-Content $ResultFile -Raw | ConvertFrom-Json
    if (-not $outcome.ok) {
        Stop-Install ("The administrator part didn't finish: $($outcome.error)`n   " +
            "Details are saved in $([System.IO.Path]::ChangeExtension($ResultFile, '.log'))")
    }
    Good "Windows is set up."
    Save-Progress $true
    if ($outcome.restart -or -not (Test-CanUseDocker)) { Request-Restart }
} elseif (-not (Test-CanUseDocker)) {
    # The administrator part is done, but Windows only applies it at sign-in.
    Save-Progress $true
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
