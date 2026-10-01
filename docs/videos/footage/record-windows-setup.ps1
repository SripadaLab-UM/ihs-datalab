# Records the desktop footage for 09-getting-started-windows on a Windows PC:
# File Explorer extracting the download, PowerShell running the real
# installer, and DataLab in the Start menu. Writes, in
# build/09-getting-started-windows/: raw/<part>.mkv (the whole main screen;
# the takes are cut and cropped from these by cut-windows-setup.ps1) and
# terminal.json (the installer's output, for the drawn terminal).
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File docs\videos\footage\record-windows-setup.ps1 [-Part explorer|powershell|start]
#
# Needs: ffmpeg and Python on PATH; a 1920x1080 main screen at 100% scaling;
# Focus Assist on (no notifications); nothing else open on the main screen;
# DataLab already set up on this PC with its keys saved (so the installer asks
# nothing secret), Docker Desktop running, DataLab itself closed; and the
# release ZIP from https://datalab.cap-study.com/ in Downloads\DataLab-Windows.zip,
# extracted to Downloads\DataLab-Windows. Don't touch the PC while it runs
# (a few minutes): it moves the mouse, types, and uses the clipboard (put back
# afterwards).
#
# What's staged: File Explorer shows a folder named Downloads holding only the
# ZIP, on a drive letter of its own (subst, removed afterwards), so no one's
# files or user name are filmed. PowerShell is a copy of the Start menu's
# Windows PowerShell (its colours and font) with a plain "PS> " prompt, and the
# installer's output passes through a filter that shows the user's name as
# "you" (the typed command is unchanged). The filter puts back the
# installer's colours (Step, OK, Welcome, All done), which PowerShell drops
# when output is piped. The GitHub question is answered n.
param(
    [ValidateSet("all", "explorer", "powershell", "start")]
    [string]$Part = "all"
)
$ErrorActionPreference = "Stop"
$env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
$Ffmpeg = (Get-Command ffmpeg).Source
$Python = (Get-Command python).Source
$Build = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\build\09-getting-started-windows"))
$Raw = Join-Path $Build "raw"
$Work = Join-Path $env:TEMP ("datalab-film-" + [Guid]::NewGuid().ToString("N").Substring(0, 8))
New-Item -ItemType Directory -Force $Raw, $Work | Out-Null
$Zip = Join-Path $HOME "Downloads\DataLab-Windows.zip"
$Installer = Join-Path $HOME "Downloads\DataLab-Windows\Install-DataLab.ps1"
if (-not (Test-Path $Zip) -or -not (Test-Path $Installer)) { throw "Needs $Zip, extracted beside it." }

Add-Type -AssemblyName System.Windows.Forms, UIAutomationClient, UIAutomationTypes
Add-Type @"
using System; using System.Runtime.InteropServices;
public static class W {
  [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
  [DllImport("user32.dll")] public static extern void mouse_event(uint f, uint x, uint y, uint d, UIntPtr e);
  [DllImport("user32.dll")] public static extern void keybd_event(byte vk, byte scan, uint f, UIntPtr e);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern bool SetWindowPos(IntPtr h, IntPtr after, int x, int y, int cx, int cy, uint f);
  [DllImport("user32.dll")] public static extern bool GetCursorPos(out POINT p);
  [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr h, uint m, IntPtr w, IntPtr l);
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }
  [StructLayout(LayoutKind.Sequential)] public struct POINT { public int X, Y; }
}
"@

# The main screen, 1920x1080, to raw\<name>.mp4 until Stop-Recording.
function Start-Recording($name) {
    $info = New-Object Diagnostics.ProcessStartInfo $Ffmpeg
    $info.Arguments = "-v error -y -f gdigrab -framerate 30 -offset_x 0 -offset_y 0 -video_size 1920x1080 -i desktop " +
        "-c:v libx264 -preset ultrafast -crf 12 -pix_fmt yuv420p `"$(Join-Path $Raw "$name.mkv")`""
    $info.UseShellExecute = $false; $info.RedirectStandardInput = $true; $info.CreateNoWindow = $true
    $process = [Diagnostics.Process]::Start($info)
    Start-Sleep -Milliseconds 1200
    $process
}
function Stop-Recording($process) {
    if (-not $process -or $process.HasExited) { return }
    $process.StandardInput.Write("q"); $process.StandardInput.Flush()
    if (-not $process.WaitForExit(20000)) { $process.Kill() }
}

# The mouse glides (eased) to a point; clicks are real.
function Move-Mouse($x, $y, $seconds = 0.6) {
    $p = New-Object W+POINT; [W]::GetCursorPos([ref]$p) | Out-Null
    $steps = [int]($seconds * 60)
    for ($i = 1; $i -le $steps; $i++) {
        $t = $i / $steps; $e = $t * $t * (3 - 2 * $t)
        [W]::SetCursorPos([int]($p.X + ($x - $p.X) * $e), [int]($p.Y + ($y - $p.Y) * $e)) | Out-Null
        Start-Sleep -Milliseconds 16
    }
}
function Click($right = $false) {
    $down, $up = if ($right) { 0x0008, 0x0010 } else { 0x0002, 0x0004 }
    [W]::mouse_event($down, 0, 0, 0, [UIntPtr]::Zero); Start-Sleep -Milliseconds 70
    [W]::mouse_event($up, 0, 0, 0, [UIntPtr]::Zero)
}
function Center($element) {
    $r = $element.Current.BoundingRectangle
    @([int]($r.X + $r.Width / 2), [int]($r.Y + $r.Height / 2))
}
function Front($hwnd) {
    # A key press (F24, which nothing uses; Alt would show Explorer's key
    # tips), so Windows lets this script bring a window to the front.
    [W]::keybd_event(0x87, 0, 0, [UIntPtr]::Zero); [W]::keybd_event(0x87, 0, 2, [UIntPtr]::Zero)
    [W]::SetForegroundWindow([IntPtr]$hwnd) | Out-Null
}
function Keys($text, $gap = 0) {
    if (-not $gap) { [Windows.Forms.SendKeys]::SendWait($text); return }
    foreach ($c in $text.ToCharArray()) { [Windows.Forms.SendKeys]::SendWait("$c"); Start-Sleep -Milliseconds $gap }
}
$Desktop = [Windows.Automation.AutomationElement]::RootElement
function Find-Top($pattern, $seconds = 10) {
    $until = (Get-Date).AddSeconds($seconds)
    do {
        $hit = $Desktop.FindAll([Windows.Automation.TreeScope]::Children, [Windows.Automation.Condition]::TrueCondition) |
            Where-Object { $_.Current.Name -match $pattern -or $_.Current.ClassName -match $pattern } | Select-Object -First 1
        if ($hit) { return $hit }
        Start-Sleep -Milliseconds 150
    } while ((Get-Date) -lt $until)
    throw "No window matching $pattern"
}
function Find-In($root, $type, $pattern, $seconds = 10) {
    $condition = New-Object Windows.Automation.PropertyCondition ([Windows.Automation.AutomationElement]::ControlTypeProperty), $type
    $until = (Get-Date).AddSeconds($seconds)
    do {
        $hit = $root.FindAll([Windows.Automation.TreeScope]::Descendants, $condition) | Where-Object { $_.Current.Name -match $pattern } | Select-Object -First 1
        if ($hit) { return $hit }
        Start-Sleep -Milliseconds 150
    } while ((Get-Date) -lt $until)
    throw "No $($type.ProgrammaticName) matching $pattern"
}
$CT = [Windows.Automation.ControlType]
# The open shortcut menu's rectangle. (Its items aren't exposed to UI
# Automation, so they're found by position.)
function Find-Menu($seconds = 10) {
    $until = (Get-Date).AddSeconds($seconds)
    while ((Get-Date) -lt $until) {
        foreach ($menu in $Desktop.FindAll([Windows.Automation.TreeScope]::Children, [Windows.Automation.Condition]::TrueCondition)) {
            $r = $menu.Current.BoundingRectangle
            if ($menu.Current.ClassName -eq "#32768" -and $r.Width -gt 100 -and $r.Height -gt 100) { return $r }
        }
        Start-Sleep -Milliseconds 150
    }
    throw "No shortcut menu"
}

# ---- 1. File Explorer: a Downloads folder holding only the ZIP, on a drive of its own ----
function Record-Explorer {
    $letter = [char[]]"ZYXWVUTSRQPONM" | Where-Object { -not (Test-Path "$($_):\") } | Select-Object -First 1
    $stage = Join-Path $Work "stage"
    New-Item -ItemType Directory -Force (Join-Path $stage "Downloads") | Out-Null
    Copy-Item $Zip (Join-Path $stage "Downloads")
    subst "$($letter):" $stage
    try {
        $folder = "$($letter):\Downloads"
        $shell = New-Object -ComObject Shell.Application
        # Only a window this opens (one from an earlier run may still be closing).
        $before = @($shell.Windows() | ForEach-Object { $_.HWND })
        Start-Process explorer.exe $folder
        $window = $null
        for ($i = 0; $i -lt 60 -and -not $window; $i++) {
            Start-Sleep -Milliseconds 250
            $window = @($shell.Windows()) | Where-Object { $_.LocationURL -eq "file:///$($letter):/Downloads" -and $before -notcontains $_.HWND } | Select-Object -First 1
        }
        if (-not $window) { throw "Explorer didn't open $folder" }
        # Details: the ZIP's name, type and size, readable.
        try { $window.Document.CurrentViewMode = 4 } catch {}
        $hwnd = [IntPtr]$window.HWND
        [W]::SetWindowPos($hwnd, [IntPtr]::Zero, 260, 140, 1400, 800, 0x0040) | Out-Null
        Front $hwnd
        [W]::SetCursorPos(1500, 900) | Out-Null
        Start-Sleep -Milliseconds 1500
        $explorer = [Windows.Automation.AutomationElement]::FromHandle($hwnd)
        $item = Find-In $explorer $CT::ListItem "^DataLab-Windows" 20
        # Where the window and its navigation pane (the person's own folders
        # and name: blurred in the take) are, for cutting.
        $nav = Find-In $explorer $CT::Pane "^Control Host$"
        $box = { param($e) $r = $e.Current.BoundingRectangle; @{ x = [int]$r.X; y = [int]$r.Y; w = [int]$r.Width; h = [int]$r.Height } }
        @{ window = (& $box $explorer); nav = (& $box $nav) } | ConvertTo-Json | Set-Content -Encoding UTF8 (Join-Path $Raw "explorer.json")

        $rec = Start-Recording "explorer"
        Start-Sleep -Milliseconds 800
        # On the file's name (a Details row spans every column).
        $r = $item.Current.BoundingRectangle
        Move-Mouse ([int]($r.X + 70)) ([int]($r.Y + $r.Height / 2)) 0.7
        Start-Sleep -Milliseconds 250
        Click $true
        # A ZIP's menu: Open, Open in new window, a line, then Extract All
        # (62 pixels below the menu's top at 100% scaling). If that's wrong,
        # the wizard doesn't open, and this stops.
        $menu = Find-Menu
        Start-Sleep -Milliseconds 350
        Move-Mouse ([int]($menu.X + 70)) ([int]($menu.Y + 62)) 0.4
        Start-Sleep -Milliseconds 250
        Click
        # The wizard (in front once it opens; its controls aren't all exposed
        # to UI Automation, so they're found by position: the destination
        # field near its top left, Extract at its bottom right).
        $wizard = $null
        for ($i = 0; $i -lt 40 -and -not $wizard; $i++) {
            Start-Sleep -Milliseconds 150
            $front = [W]::GetForegroundWindow()
            $box = New-Object W+RECT; [W]::GetWindowRect($front, [ref]$box) | Out-Null
            if ($front -ne $hwnd -and ($box.Right - $box.Left) -in 500..800) { $wizard = $box }
        }
        if (-not $wizard) { throw "The Extract wizard didn't open" }
        Start-Sleep -Milliseconds 500
        # The destination is the ZIP's own folder plus its name; Downloads it is.
        Move-Mouse ($wizard.Left + 440) ($wizard.Top + 155) 0.5
        Click
        Start-Sleep -Milliseconds 200
        Keys "{END}"
        for ($i = 0; $i -lt "\DataLab-Windows".Length; $i++) { Keys "{BACKSPACE}"; Start-Sleep -Milliseconds 35 }
        Start-Sleep -Milliseconds 500
        Move-Mouse ($wizard.Right - 127) ($wizard.Bottom - 22) 0.5
        Start-Sleep -Milliseconds 200
        Click
        Start-Sleep -Milliseconds 2500
    } finally {
        Stop-Recording $rec
        # Any wizard still open, then the staged folder's windows (closed the
        # ordinary way: Quit can leave one behind).
        try { [W]::PostMessage([IntPtr](Find-Top "Extract Compressed" 0).Current.NativeWindowHandle, 0x10, [IntPtr]::Zero, [IntPtr]::Zero) | Out-Null } catch {}
        foreach ($w in @((New-Object -ComObject Shell.Application).Windows())) {
            if ("$($w.LocationURL)" -like "file:///$($letter):*") { [W]::PostMessage([IntPtr]$w.HWND, 0x10, [IntPtr]::Zero, [IntPtr]::Zero) | Out-Null }
        }
        Start-Sleep -Milliseconds 1000
        subst "$($letter):" /d
    }
}

# ---- 2. PowerShell: the real installer, a second run, through the name filter ----
$Filter = @'
import codecs, ctypes, json, os, queue, re, sys, threading, time
log_path, json_path = sys.argv[1], sys.argv[2]
user = os.environ["USERNAME"]
home = re.compile(re.escape("\\Users\\" + user), re.I)
bare = re.compile(r"\b" + re.escape(user) + r"\b", re.I)
redact = lambda s: bare.sub("you", home.sub(r"\\Users\\you", s))
held = ["\\users\\" + user.lower(), user.lower()]
# PowerShell's own colours for the installer's kinds of line.
COLOURS = [(r"^== .* ==$", "96"), (r"^Welcome( back)?! ", "96"), (r"^DataLab setup: the administrator part", "96"),
           (r"^   OK: |^All done", "92"), (r"^Setup stopped:|^Something went wrong", "91")]
k = ctypes.windll.kernel32
h = k.GetStdHandle(-11); mode = ctypes.c_uint()
if k.GetConsoleMode(h, ctypes.byref(mode)): k.SetConsoleMode(h, mode.value | 4)
q = queue.Queue()
def reader():
    while True:
        b = os.read(0, 4096); q.put(b)
        if not b: return
threading.Thread(target=reader, daemon=True).start()
utf8 = codecs.getincrementaldecoder("utf-8")()
log = open(log_path, "w", encoding="utf-8")
events, t0, buf, partial = [], time.monotonic(), "", False
def emit(shown, plain):
    sys.stdout.write(shown); sys.stdout.flush()
    e = [round(time.monotonic() - t0, 3), plain]
    events.append(e); log.write(json.dumps(e) + "\n"); log.flush()
def keep(s):  # characters that may be the start of the user's name: held back
    low = s.lower()
    return max((n for n in range(1, len(s) + 1) for w in held if w.startswith(low[-n:])), default=0)
while True:
    try:
        b = q.get(timeout=0.15)
    except queue.Empty:
        if buf:
            n = keep(buf); out, buf = buf[:len(buf) - n], buf[len(buf) - n:]
            if out:
                emit(redact(out), redact(out)); partial = True
        continue
    if not b:
        break
    try:
        text = utf8.decode(b)
    except UnicodeDecodeError:
        utf8.reset(); text = b.decode("cp437")
    buf += text.replace("\r\n", "\n").replace("\r", "")
    while "\n" in buf:
        line, buf = buf.split("\n", 1)
        line = redact(line)
        colour = None if partial else next((c for p, c in COLOURS if re.search(p, line)), None)
        emit(f"\x1b[{colour}m{line}\x1b[0m\n" if colour else line + "\n", line + "\n")
        partial = False
if buf:
    emit(redact(buf), redact(buf))
json.dump({"events": events}, open(json_path, "w", encoding="utf-8"), indent=1)
'@

function Record-PowerShell {
    $filterPy = Join-Path $Work "redact.py"; $log = Join-Path $Work "terminal.jsonl"
    [IO.File]::WriteAllText($filterPy, $Filter)
    # The typed command runs this "powershell": the real one, its output piped
    # (by cmd, so prompts without a line break still show) to the filter.
    $pipe = Join-Path $Work "filtered-powershell.cmd"
    [IO.File]::WriteAllText($pipe, "@powershell.exe %* 2>&1 | `"$Python`" -u `"$filterPy`" `"$log`" `"$(Join-Path $Build "terminal.json")`"`r`n")
    $shellSetup = @"
function prompt { "PS> " }
`$raw = `$Host.UI.RawUI
`$raw.BufferSize = New-Object Management.Automation.Host.Size(120, 3000)
`$raw.WindowSize = New-Object Management.Automation.Host.Size(120, 32)
`$raw.WindowTitle = "Windows PowerShell"
function powershell { & "$pipe" @args }
Set-Location `$HOME
Clear-Host
"@
    $setupFile = Join-Path $Work "film-shell.ps1"
    [IO.File]::WriteAllText($setupFile, $shellSetup)
    # A copy of the Start menu's shortcut keeps its colours and font.
    $lnk = Join-Path $Work "Windows PowerShell.lnk"
    Copy-Item "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Windows PowerShell\Windows PowerShell.lnk" $lnk
    $shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut($lnk)
    $shortcut.Arguments = "-NoExit -NoProfile -ExecutionPolicy Bypass -File `"$setupFile`""
    $shortcut.Save()
    $began = Get-Date
    Start-Process $lnk
    $console = $null
    for ($i = 0; $i -lt 80 -and -not $console; $i++) {
        Start-Sleep -Milliseconds 250
        $console = Get-Process powershell -ErrorAction SilentlyContinue |
            Where-Object { $_.StartTime -gt $began -and $_.MainWindowHandle -ne 0 } | Select-Object -First 1
    }
    if (-not $console) { throw "PowerShell didn't open" }
    Start-Sleep -Milliseconds 1500
    [W]::SetWindowPos($console.MainWindowHandle, [IntPtr]::Zero, 200, 60, 0, 0, 0x0001 -bor 0x0040) | Out-Null
    Front $console.MainWindowHandle
    [W]::SetCursorPos(1915, 1075) | Out-Null
    $clipboard = try { Get-Clipboard -Raw } catch { $null }
    Set-Clipboard -Value 'powershell -NoProfile -ExecutionPolicy Bypass -STA -File "$HOME\Downloads\DataLab-Windows\Install-DataLab.ps1"'
    try {
        $rec = Start-Recording "powershell"
        Start-Sleep -Milliseconds 1800
        Front $console.MainWindowHandle
        Keys "^v"
        Start-Sleep -Milliseconds 1400
        Keys "{ENTER}"
        $answered = 0
        $until = (Get-Date).AddMinutes(15)
        while ((Get-Date) -lt $until) {
            Start-Sleep -Milliseconds 500
            $text = ""
            if (Test-Path $log) {
                # Shared: the filter has it open for writing.
                $reader = New-Object IO.StreamReader ([IO.File]::Open($log, "Open", "Read", "ReadWrite"))
                $text = $reader.ReadToEnd(); $reader.Close()
            }
            # PowerShell writes a question straight to the window, not to the
            # pipe, so the log never shows it: the GitHub question is the one
            # after Step 7's heading, once nothing more has come for a moment.
            $asked = if ($text.Contains("Step 7 of 8") -and -not $text.Contains("Step 8 of 8") -and
                ((Get-Date) - (Get-Item $log).LastWriteTime).TotalSeconds -gt 2.5) { 1 } else { 0 }
            if ($asked -gt $answered) {
                Front $console.MainWindowHandle
                Keys "n"; Start-Sleep -Milliseconds 400; Keys "{ENTER}"
                $answered++
            }
            if ($text.Contains("Or run:")) { break }
        }
        Start-Sleep -Milliseconds 2500
    } finally {
        Stop-Recording $rec
        if ($clipboard) { Set-Clipboard -Value $clipboard }
        # The window and everything it started (an installer left waiting at
        # a question would otherwise carry on with no window).
        $tree = @($console.Id); $all = Get-CimInstance Win32_Process
        for ($i = 0; $i -lt $tree.Count; $i++) { $tree += @($all | Where-Object { $_.ParentProcessId -eq $tree[$i] } | ForEach-Object { $_.ProcessId }) }
        [array]::Reverse($tree)
        $tree | ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }
    }
}

# ---- 3. The Start menu: DataLab typed, the entry setup added (not opened) ----
function Record-Start {
    [W]::SetCursorPos(1915, 540) | Out-Null
    $rec = Start-Recording "start"
    try {
        Start-Sleep -Milliseconds 1500
        [W]::keybd_event(0x5B, 0, 0, [UIntPtr]::Zero); [W]::keybd_event(0x5B, 0, 2, [UIntPtr]::Zero)
        Start-Sleep -Milliseconds 1500
        Keys "DataLab" 140
        Start-Sleep -Milliseconds 4500
        Keys "{ESC}"
        Start-Sleep -Milliseconds 800
    } finally { Stop-Recording $rec }
}

try {
    if ($Part -in "all", "explorer") { Record-Explorer }
    if ($Part -in "all", "powershell") { Record-PowerShell }
    if ($Part -in "all", "start") { Record-Start }
} finally {
    Remove-Item -Recurse -Force $Work -ErrorAction SilentlyContinue
}
Write-Host "Done. Check every frame of $Raw for anything personal, then cut the takes."
