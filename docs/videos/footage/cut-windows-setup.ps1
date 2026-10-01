# Cuts the takes for 09-getting-started-windows from the raw recordings that
# record-windows-setup.ps1 made, and lists them in takes.json beside the
# website takes. Each take is cropped to its window, scaled into 1920x1080,
# and anything personal still in the window is blurred:
#   - File Explorer's navigation pane (the person's own folders and name);
#   - the Start menu's other results (their files and folders) and account
#     picture, leaving the search box and the Best match.
# The PowerShell window is cropped below its title bar.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File docs\videos\footage\cut-windows-setup.ps1
#
# The cut points were read off contact sheets of the raw recordings; re-check
# them (the $Cuts table) after a new recording.
$ErrorActionPreference = "Stop"
$env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
$Build = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\build\09-getting-started-windows"))
$Raw = Join-Path $Build "raw"
$TakeDir = Join-Path $Build "takes"
New-Item -ItemType Directory -Force $TakeDir | Out-Null

# shot: take name, raw recording, from, to (seconds).
$Cuts = [ordered]@{
    "3.2" = @("explorer", "explorer", 1.8, 12.4)
    "3.4" = @("term-paste", "powershell", 1.4, 9.5)
    "3.6" = @("term-steps", "powershell", 9.3, 17.6)
    "4.2" = @("term-done", "powershell", 18.2, 24.2)
    "5.1" = @("start", "start", 4.2, 9.3)
}

# Explorer: the whole window; its navigation pane blurred.
$ex = Get-Content (Join-Path $Raw "explorer.json") -Raw | ConvertFrom-Json
$w = $ex.window; $n = $ex.nav
$nx = $n.x - $w.x; $ny = $n.y - $w.y
$Explorer = "[0:v]crop=$($w.w):$($w.h):$($w.x):$($w.y),split[a][b];[b]crop=$($n.w):$($n.h):${nx}:${ny},boxblur=18:3[nav];" +
    "[a][nav]overlay=${nx}:${ny},scale=-2:1080:flags=lanczos,pad=1920:1080:(ow-iw)/2:0:color=0x191919"
# PowerShell: below the title bar, left of the scroll bar (the console's
# place in record-windows-setup.ps1: 200,60).
$PowerShell = "[0:v]crop=840:447:208:91,scale=1920:-2:flags=lanczos,pad=1920:1080:0:0:color=0x012456"
# Start menu: the search pane; everything under Best match, and the account
# picture, blurred. 980 high, so its search box stays above the captions.
$Start = "[0:v]crop=784:780:62:0,split=3[a][b][c];[b]crop=344:590:0:150,boxblur=22:3[list];[c]crop=40:40:640:5,boxblur=lr=8:lp=4:cr=4:cp=4[me];" +
    "[a][list]overlay=0:150[d];[d][me]overlay=640:5,scale=-2:980:flags=lanczos,pad=1920:1080:(ow-iw)/2:0:color=0x1f1f1f"
$Graphs = @{ explorer = $Explorer; powershell = $PowerShell; start = $Start }

$manifest = Join-Path $Build "takes.json"
$takes = if (Test-Path $manifest) { Get-Content $manifest -Raw | ConvertFrom-Json } else { [pscustomobject]@{} }
foreach ($shot in $Cuts.Keys) {
    $name, $source, $from, $to = $Cuts[$shot]
    $out = Join-Path $TakeDir "$name.mp4"
    & ffmpeg -v error -y -ss $from -t ($to - $from) -i (Join-Path $Raw "$source.mkv") -filter_complex $Graphs[$source] `
        -r 30 -c:v libx264 -crf 16 -pix_fmt yuv420p -g 10 -an $out
    if ($LASTEXITCODE) { throw "ffmpeg failed on $name" }
    $take = [ordered]@{ file = "takes/$name.mp4"; seconds = [math]::Round($to - $from, 2); rects = @{}; ff = @() }
    # The Best match row, where the narration points (Start menu pixels
    # 62..406 x 84..148, in the frame after the crop and scale above).
    if ($shot -eq "5.1") {
        $k = 980 / 780; $x0 = (1920 - [math]::Round(784 * $k / 2) * 2) / 2
        $take.rects = @{ entry = @(@{ at = 0; x = [math]::Round($x0); y = [math]::Round(84 * $k); width = [math]::Round(344 * $k); height = [math]::Round(64 * $k) }) }
    }
    $takes | Add-Member -NotePropertyName $shot -NotePropertyValue ([pscustomobject]$take) -Force
    Write-Host "$shot  $name.mp4  $($take.seconds) s"
}
[IO.File]::WriteAllText($manifest, ($takes | ConvertTo-Json -Depth 8), (New-Object Text.UTF8Encoding $false))
