<#
.SYNOPSIS
  One-command CheqUp render on Dan's Windows RTX 5090 PC: pre-flight, make, outbox, REPORT.md, review zip.

.DESCRIPTION
  Every model runs on this PC: b-roll and ACE-Step music on CheqUp's OWN ComfyUI (C:\Users\white\ComfyUI-CheqUp,
  127.0.0.1:8288, v0.39.2, model files shared read-only with shorts-factory), voice with Chatterbox (Kokoro as a
  reported fallback), render with Playwright + ffmpeg. Always uses config.pc.yaml, where mama is disabled; a config
  that enables any non-local ComfyUI, or sends CheqUp jobs to shorts-factory's ComfyUI, is refused. Never touches Meta.

  shorts-factory keeps its own ComfyUI (127.0.0.1:8188) and keeps rendering: pc_run never modifies, restarts,
  stops or POSTs to it (only reads GET /queue to report its state), and cqf's GPU gate (config gpu_gate) waits
  before every CheqUp GPU job while shorts-factory has work. If CheqUp's ComfyUI isn't answering, pc_run starts
  it (scripts/comfy_cheq_start.ps1); if it isn't installed, it stops with exit 4 and the install command
  (scripts/install_comfy_cheq_pc.ps1, which also never touches shorts-factory's install). After the run it frees
  the VRAM of CheqUp's ComfyUI only (POST /free to 8288), so shorts-factory gets the memory back.

  A run takes hours: start it detached with scripts/pc_start.ps1 and wait with scripts/pc_wait.ps1 (PUPPET.md).

  Writes out\logs\pc_run_<id>.log (transcript), out\REPORT.md (+ report.json), out\logs\review_<id>.zip and,
  as its very last step, out\logs\pc_run_<id>.done (JSON with exit, finished, report, log, pack).

  Exit codes:
    0 ok                      6 models missing (b-roll, Chatterbox env/reference clips, ACE-Step checkpoint)
    1 a board failed          7 unknown board or format
    2 tools missing           8 fallbacks used (expected with -SkipModels / -Draft)
    3 ComfyUI down            9 run crashed
    4 CheqUp ComfyUI missing  10 another run is already going
      or too old              12 GPU busy: the GPU gate waited gpu_gate.max_wait_s for shorts-factory (or for
    5 Python env or config       free VRAM) and gave up; the boards after that were skipped

.PARAMETER Boards
  Groups (concepts\<group>-*.json), "all", board ids, paths or globs; commas allowed. Default: concepts\numan-*.json
  if there are any, else concepts\made-simple-*.json.
.PARAMETER Formats
  9x16, 4x5, 1x1, 16x9 (commas allowed). Default: each board's own formats.
.PARAMETER Strict
  Refuse HOLD boards instead of rendering drafts.
.PARAMETER SkipModels
  Accept fallbacks instead of stopping (brand stills for b-roll, the procedural music bed, Kokoro for Chatterbox
  lines). The run then ends with exit 8 if anything fell back.
.PARAMETER Draft
  Don't stop on an old CheqUp ComfyUI or on missing Qwen/SeedVR2 files. cqf has no Wan-only still path today, so b-roll
  the quality graphs can't make uses CheqUp's own stills (REPORT.md says so; exit 8).
.PARAMETER Clips
  Every AI b-roll shot becomes a Wan 2.2 image-to-video clip made from its chosen still. Slower.
.PARAMETER PauseFactory
  Pause shorts-factory by creating <shorts_factory.dir>\data\STOP. Not needed any more (CheqUp has its own ComfyUI
  and the GPU gate yields to shorts-factory); off by default. pc_run never deletes it (only Dan resumes).
.PARAMETER RunId
  Set by pc_start.ps1. Default: a timestamp.

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File scripts/pc_run.ps1 -Boards numan -Formats 9x16
#>
[CmdletBinding()]
param(
  [string[]]$Boards = @(),
  [string[]]$Formats = @(),
  [switch]$Strict,
  [switch]$SkipModels,
  [switch]$Draft,
  [switch]$Clips,
  [switch]$PauseFactory,
  [string]$RunId = ''
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'         # Windows PowerShell 5.1 draws (slow) progress bars for web calls
$Root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $Root
$OnWindows = ($env:OS -eq 'Windows_NT')
$Config = 'config.pc.yaml'                       # never config.mama.yaml: mama is off-limits for CheqUp
$CheqDir = 'C:\Users\white\ComfyUI-CheqUp'          # CheqUp's own ComfyUI (config comfy_cheq overrides these)
$CheqPort = 8288
$script:CheqUrl = 'http://127.0.0.1:' + $CheqPort
$script:FactoryPorts = @(8188)                   # shorts-factory's ComfyUI: GET /queue only, never a POST
$MinComfy = New-Object System.Version(0, 39, 2)
$FactoryDefault = 'C:\Users\white\heatmap\shorts-factory'
$ExitMeaning = @{ 0 = 'ok'; 1 = 'a board failed'; 2 = 'tools missing'; 3 = 'ComfyUI down'; 4 = 'CheqUp ComfyUI missing or too old';
                  5 = 'Python env or config'; 6 = 'models missing'; 7 = 'unknown board or format'; 8 = 'fallbacks used';
                  9 = 'run crashed'; 10 = 'already running'; 12 = 'GPU busy (gate gave up)' }

if (-not $RunId) { $RunId = Get-Date -Format 'yyyyMMdd-HHmmss' }
$Logs = Join-Path (Join-Path $Root 'out') 'logs'
New-Item -ItemType Directory -Force -Path $Logs | Out-Null
$LogPath = Join-Path $Logs ('pc_run_' + $RunId + '.log')
$DonePath = Join-Path $Logs ('pc_run_' + $RunId + '.done')
$PackPath = Join-Path $Logs ('review_' + $RunId + '.zip')
$LockPath = Join-Path $Logs 'pc_run.lock'
$ReportPath = Join-Path (Join-Path $Root 'out') 'REPORT.md'
$ReportJson = Join-Path (Join-Path $Root 'out') 'report.json'
$Started = Get-Date
$Notes = New-Object System.Collections.Generic.List[string]
$script:Transcribing = $false
$script:FinishCode = $null
$script:WantPack = $false
$script:ReportOk = $false
$script:BoardFiles = @()
$script:Py = $null
$script:PyLines = New-Object System.Collections.Generic.List[string]
$script:FreeCheq = $false

$FlagParts = @()
foreach ($k in $PSBoundParameters.Keys) {
  if ($k -eq 'RunId') { continue }
  $v = $PSBoundParameters[$k]
  if ($v -is [System.Management.Automation.SwitchParameter]) { if ($v.IsPresent) { $FlagParts += ('-' + $k) } }
  else { $FlagParts += ('-' + $k + ' ' + (@($v) -join ',')) }
}
$FlagText = $FlagParts -join ' '

function Write-Utf8([string]$Path, [string]$Text) {
  [System.IO.File]::WriteAllText($Path, $Text, (New-Object System.Text.UTF8Encoding($false)))
}

function Read-Lock {
  if (-not (Test-Path -LiteralPath $LockPath)) { return $null }
  try { return (Get-Content -LiteralPath $LockPath -Raw -Encoding UTF8 | ConvertFrom-Json) } catch { return $null }
}

function Get-LockTime($Value) {
  # pwsh 7's ConvertFrom-Json already turns ISO strings into [datetime]; Windows PowerShell 5.1 leaves strings.
  if ($Value -is [datetime]) { return $Value }
  return [datetime]::ParseExact([string]$Value, 's', [System.Globalization.CultureInfo]::InvariantCulture)
}

function Test-RunAlive($Lock) {
  # The lock's process is a live PowerShell that started around when the lock was written (PIDs get reused).
  if ($null -eq $Lock) { return $false }
  $procId = 0
  try { $procId = [int]$Lock.pid } catch { $procId = 0 }
  if ($procId -le 0) { return $false }
  $p = Get-Process -Id $procId -ErrorAction SilentlyContinue
  if ($null -eq $p) { return $false }
  if ($p.ProcessName -notmatch '^(powershell|pwsh)$') { return $false }
  try {
    $since = Get-LockTime $Lock.started
    if ($p.StartTime -gt $since.AddMinutes(2)) { return $false }
  } catch { }
  return $true
}

function Write-Lock {
  $o = [ordered]@{ run_id = $RunId; pid = $PID; started = $Started.ToString('s'); log = $LogPath; done = $DonePath;
                   host = $env:COMPUTERNAME; flags = $FlagText }
  Write-Utf8 $LockPath ($o | ConvertTo-Json)
}

function Remove-OurLock {
  $l = Read-Lock
  if ($null -ne $l -and [string]$l.run_id -eq $RunId) { Remove-Item -LiteralPath $LockPath -Force -ErrorAction SilentlyContinue }
}

function Get-Count($x) {
  if ($null -eq $x) { return 0 }
  return @($x).Count
}

function ConvertTo-ComfyVersion([string]$Text) {
  if ($Text -match '^\s*v?(\d+)\.(\d+)(?:\.(\d+))?') {
    $patch = 0
    if ($Matches[3]) { $patch = [int]$Matches[3] }
    return (New-Object System.Version([int]$Matches[1], [int]$Matches[2], $patch))
  }
  return $null
}

function Update-PathFromRegistry {
  # A tool installed with winget after this session started is on the registry PATH but not on ours yet.
  if (-not $OnWindows) { return }
  $parts = @()
  foreach ($scope in @('Machine', 'User')) {
    $v = [Environment]::GetEnvironmentVariable('Path', $scope)
    if ($v) { $parts += $v.Split(';') }
  }
  if ($env:Path) { $parts += $env:Path.Split(';') }
  $seen = @{}
  $out = @()
  foreach ($p in $parts) {
    $t = $p.Trim()
    if ($t -and -not $seen.ContainsKey($t.ToLower())) { $seen[$t.ToLower()] = 1; $out += $t }
  }
  $env:Path = $out -join ';'
}

function Invoke-Py {
  # Run the venv's python; stream every line to the console/transcript and keep them in $script:PyLines.
  param([string[]]$PyArgs, [switch]$Silent)
  $script:PyLines = New-Object System.Collections.Generic.List[string]
  $prev = $ErrorActionPreference
  $ErrorActionPreference = 'Continue'            # python's stderr must not become a terminating error
  $code = 0
  try {
    & $script:Py @PyArgs 2>&1 | ForEach-Object {
      if ($_ -is [System.Management.Automation.ErrorRecord]) { $line = $_.ToString() } else { $line = [string]$_ }
      $script:PyLines.Add($line)
      if (-not $Silent) { Write-Host $line }
    }
    $code = $LASTEXITCODE
  } finally {
    $ErrorActionPreference = $prev
  }
  if ($null -eq $code) { $code = 9 }
  return [int]$code
}

function Get-JsonLine {
  for ($i = $script:PyLines.Count - 1; $i -ge 0; $i--) {
    $t = $script:PyLines[$i].Trim()
    if ($t.StartsWith('{')) { return ($t | ConvertFrom-Json) }
  }
  return $null
}

function Get-ComfyStats([string]$Url) {
  try { return (Invoke-RestMethod -Uri ($Url + '/system_stats') -Method Get -TimeoutSec 10 -UseBasicParsing) } catch { return $null }
}

function Free-CheqVram {
  # After a run: give back the VRAM held by CheqUp's OWN ComfyUI, so shorts-factory gets it. Only ever
  # $script:CheqUrl, and never a shorts-factory (gpu_gate.yield_to) port.
  try {
    $port = ([System.Uri]$script:CheqUrl).Port
    if ($script:FactoryPorts -contains $port) { Write-Host ('not freeing ' + $script:CheqUrl + ": that is shorts-factory's port"); return }
    $body = '{"unload_models": true, "free_memory": true}'
    Invoke-RestMethod -Uri ($script:CheqUrl + '/free') -Method Post -Body $body -ContentType 'application/json' -TimeoutSec 10 -UseBasicParsing | Out-Null
    Write-Host ("freed CheqUp's ComfyUI VRAM (" + $script:CheqUrl + '/free, unload_models)')
  } catch {
    Write-Host ("could not free CheqUp's ComfyUI VRAM (" + $script:CheqUrl + '): ' + $_)
  }
}

function Clean-Note([string]$Text) {
  # Notes go to python as arguments: Windows PowerShell 5.1 mangles embedded double quotes and a trailing backslash.
  $t = $Text.Replace('"', "'").Trim()
  if ($t.EndsWith('\')) { $t = $t + '.' }
  return $t
}

function Add-Note([string]$Text) {
  Write-Host ('NOTE ' + $Text) -ForegroundColor Yellow
  $Notes.Add((Clean-Note $Text))
}

function Finish([int]$Code, [string]$Why = '') {
  # Every step is guarded so the done marker is always written and the lock always released. Re-entered from
  # the catch below (something here threw): keep the first exit code, don't build the zip twice.
  if ($null -ne $script:FinishCode) { $Code = [int]$script:FinishCode } else { $script:FinishCode = $Code }
  $meaning = $ExitMeaning[$Code]
  try {
    if ($Why) { Write-Host $Why }
    if ($script:FreeCheq) { $script:FreeCheq = $false; Free-CheqVram }
    Write-Host ('pc_run ' + $RunId + ': exit ' + $Code + ' (' + $meaning + ')')
  } catch { }
  if ($script:Transcribing) {
    $script:Transcribing = $false
    try { Stop-Transcript | Out-Null } catch { }
  }
  $pack = $null
  if ($script:WantPack -and $script:Py) {
    $script:WantPack = $false
    # The review zip includes the log, so it is built after the transcript is closed; its output is appended.
    try {
      $packExit = Invoke-Py @('-m', 'cqf.report', '--config', $Config, 'pack', '--zip', $PackPath, '--log', $LogPath)
      try {
        [System.IO.File]::AppendAllText($LogPath, (($script:PyLines -join "`r`n") + "`r`n"), (New-Object System.Text.UTF8Encoding($false)))
      } catch { }
      if ($packExit -eq 0 -and (Test-Path -LiteralPath $PackPath)) { $pack = $PackPath }
    } catch {
      try { Write-Host ('review zip failed: ' + $_) } catch { }
    }
  }
  $report = $null
  if ($script:ReportOk) { $report = $ReportPath }
  try {
    $marker = [ordered]@{ run_id = $RunId; exit = $Code; meaning = $meaning; started = $Started.ToString('s');
                          finished = (Get-Date).ToString('s'); report = $report; log = $LogPath; pack = $pack;
                          boards = @($script:BoardFiles); flags = $FlagText }
    Write-Utf8 $DonePath ($marker | ConvertTo-Json -Depth 4)
  } catch {
    # Last resort: a minimal marker by hand, so pc_wait never reports a finished run as crashed.
    try { Write-Utf8 $DonePath ('{"run_id": "' + $RunId + '", "exit": ' + $Code + ', "finished": "' + (Get-Date).ToString('s') + '"}') }
    catch { try { Write-Host ('could not write ' + $DonePath + ': ' + $_) } catch { } }
  }
  try { Remove-OurLock } catch { }
  exit $Code
}

# --- one run at a time -------------------------------------------------------------------------------------
$existing = Read-Lock
if ($null -ne $existing -and [string]$existing.run_id -ne $RunId -and (Test-RunAlive $existing)) {
  Write-Host ('A CheqUp run is already going: run ' + $existing.run_id + ', PID ' + $existing.pid + ', log ' + $existing.log)
  Write-Host 'Wait for it instead: powershell -NoProfile -ExecutionPolicy Bypass -File scripts/pc_wait.ps1 -Minutes 100'
  exit 10
}
Write-Lock

try { Start-Transcript -LiteralPath $LogPath -Force | Out-Null; $script:Transcribing = $true }
catch { Write-Host ('transcript unavailable: ' + $_) }

try {
  try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false) } catch { }
  $OutputEncoding = New-Object System.Text.UTF8Encoding($false)
  $env:PYTHONIOENCODING = 'utf-8'
  $env:PYTHONUTF8 = '1'
  $env:PYTHONUNBUFFERED = '1'

  Write-Host ('CheqUp pc_run ' + $RunId + '  ' + $Started.ToString('s') + '  ' + $Root)
  Write-Host ('PowerShell ' + $PSVersionTable.PSVersion + '  flags: ' + $(if ($FlagText) { $FlagText } else { '(none)' }))
  if (-not $OnWindows) { Write-Host '(not Windows: test mode; the PC uses .venv\Scripts\python.exe)' }

  # --- 1. tools ----------------------------------------------------------------------------------------------
  Update-PathFromRegistry
  $missing = @()
  foreach ($t in @('node', 'ffmpeg', 'ffprobe')) {
    if (-not (Get-Command $t -ErrorAction SilentlyContinue)) { $missing += $t }
  }
  if ($missing.Count -gt 0) {
    $hint = @()
    if ($missing -contains 'node') { $hint += '  winget install -e --id OpenJS.NodeJS.LTS' }
    if (($missing -contains 'ffmpeg') -or ($missing -contains 'ffprobe')) { $hint += '  winget install -e --id Gyan.FFmpeg' }
    Finish 2 ("Missing tools: " + ($missing -join ', ') + "`nInstall, then run again (a new PowerShell picks up PATH):`n" + ($hint -join "`n"))
  }
  Write-Host 'ok   tools: node, ffmpeg, ffprobe'

  # --- 2. Python env -----------------------------------------------------------------------------------------
  if ($OnWindows) { $script:Py = Join-Path $Root '.venv\Scripts\python.exe' }
  else { $script:Py = Join-Path (Join-Path (Join-Path $Root '.venv') 'bin') 'python' }
  if (-not (Test-Path -LiteralPath $script:Py)) {
    if ($OnWindows) {
      Write-Host 'No .venv yet: running scripts/setup_pc.ps1 once (Python envs, npm, Playwright, voice reference clips).'
      $psExe = (Get-Process -Id $PID).Path
      $prev = $ErrorActionPreference
      $ErrorActionPreference = 'Continue'
      & $psExe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'setup_pc.ps1') 2>&1 | ForEach-Object { Write-Host ([string]$_) }
      $setupExit = $LASTEXITCODE
      $ErrorActionPreference = $prev
      if ($setupExit -ne 0) { Write-Host ('setup_pc.ps1 exited ' + $setupExit) }
    }
    if (-not (Test-Path -LiteralPath $script:Py)) {
      $script:Py = $null
      Finish 5 ('No Python env at .venv. Run: powershell -NoProfile -ExecutionPolicy Bypass -File scripts/setup_pc.ps1')
    }
  }
  if ((Invoke-Py @('-c', 'import yaml, requests, numpy') -Silent) -ne 0) {
    Finish 5 ("The .venv Python is broken (" + ($script:PyLines -join ' ') + "). Rerun scripts/setup_pc.ps1.")
  }
  if ((Invoke-Py @('-m', 'cqf.report', '--config', $Config, 'info') -Silent) -ne 0) {
    Finish 5 ("cqf can't read " + $Config + ":`n" + ($script:PyLines -join "`n"))
  }
  $CfgInfo = Get-JsonLine
  if ($null -eq $CfgInfo) { Finish 5 ("cqf.report info printed no JSON:`n" + ($script:PyLines -join "`n")) }
  if ((Get-Count $CfgInfo.remote_enabled) -gt 0) {
    Finish 5 ($Config + ' enables a ComfyUI that is not this PC (' + (@($CfgInfo.remote_enabled) -join ', ') +
              '). CheqUp renders only on this 5090; mama must stay "enabled: false". Fix the config; nothing was run.')
  }
  if ((Get-Count $CfgInfo.farm_conflicts) -gt 0) {
    Finish 5 ($Config + " sends CheqUp jobs to shorts-factory's ComfyUI (" + (@($CfgInfo.farm_conflicts) -join ', ') +
              '). CheqUp renders only on its own ComfyUI (comfy_cheq.port, 8288); fix the config. Nothing was run.')
  }
  if ($CfgInfo.comfy_cheq) {
    if ($CfgInfo.comfy_cheq.dir) { $CheqDir = [string]$CfgInfo.comfy_cheq.dir }
    if ($CfgInfo.comfy_cheq.port) { $CheqPort = [int]$CfgInfo.comfy_cheq.port }
  }
  $script:CheqUrl = 'http://127.0.0.1:' + $CheqPort
  $Factory = @()
  if ($CfgInfo.gpu_gate) { foreach ($y in @($CfgInfo.gpu_gate.yield_to)) { if ($y -and $y.url) { $Factory += $y } } }
  foreach ($y in $Factory) { try { $script:FactoryPorts += ([System.Uri][string]$y.url).Port } catch { } }
  if ($script:FactoryPorts -contains $CheqPort) {
    Finish 5 ('comfy_cheq.port ' + $CheqPort + " in " + $Config + " is shorts-factory's ComfyUI port. Use 8288. Nothing was run.")
  }
  Write-Host ('ok   Python env ' + $script:Py + ' (voice backend ' + $CfgInfo.voice_backend + ', music backend ' + $CfgInfo.music_backend + ')')

  # --- 3. boards and formats ---------------------------------------------------------------------------------
  $BoardTokens = @()
  foreach ($b in $Boards) { foreach ($t in ([string]$b).Split(',')) { if ($t.Trim()) { $BoardTokens += $t.Trim() } } }
  $FormatTokens = @()
  foreach ($f in $Formats) { foreach ($t in ([string]$f).Split(',')) { if ($t.Trim()) { $FormatTokens += $t.Trim() } } }
  $bargs = @('-m', 'cqf.report', '--config', $Config, 'boards') + $BoardTokens
  if ($FormatTokens.Count -gt 0) { $bargs += @('--formats', ($FormatTokens -join ',')) }   # 5.1 drops empty args
  if ((Invoke-Py $bargs -Silent) -ne 0) { Finish 5 ("Board lookup failed:`n" + ($script:PyLines -join "`n")) }
  $Sel = Get-JsonLine
  if ($null -eq $Sel) { Finish 5 ("cqf.report boards printed no JSON:`n" + ($script:PyLines -join "`n")) }
  if ((Get-Count $Sel.unknown) -gt 0 -or (Get-Count $Sel.boards) -eq 0) {
    Finish 7 ('Unknown board(s): ' + (@($Sel.unknown) -join ', ') + "`nGroups (concepts\<group>-*.json): " + (@($Sel.groups) -join ', ') +
              "`nBoard ids: " + (@($Sel.ids) -join ', ') + "`nAlso accepted: all, a path or a glob.")
  }
  if ((Get-Count $Sel.bad_formats) -gt 0) {
    Finish 7 ('Unknown format(s): ' + (@($Sel.bad_formats) -join ', ') + '. Use: ' + (@($Sel.allowed_formats) -join ', '))
  }
  $script:BoardFiles = @($Sel.boards)
  Write-Host ('ok   ' + $script:BoardFiles.Count + ' board(s): ' + ((@($script:BoardFiles) | ForEach-Object { [System.IO.Path]::GetFileNameWithoutExtension($_) }) -join ', '))
  if ($FormatTokens.Count -gt 0) { Write-Host ('     formats: ' + ($FormatTokens -join ', ')) }

  # --- 4. CheqUp's own ComfyUI (this PC; never shorts-factory's) ----------------------------------------------
  $InstallCmd = 'powershell -NoProfile -ExecutionPolicy Bypass -File scripts/install_comfy_cheq_pc.ps1'
  $UpdateFix = ("Re-run the CheqUp install (it only changes " + $CheqDir + ", never shorts-factory's ComfyUI; the PC session may run it):`n" +
                "  powershell -NoProfile -ExecutionPolicy Bypass -File scripts/comfy_cheq_stop.ps1`n  " + $InstallCmd +
                "`nthen run again (pc_run starts CheqUp's ComfyUI itself).")
  $stats = Get-ComfyStats $script:CheqUrl
  if ($null -eq $stats) {
    $installed = $false
    try {                                                # (a missing drive throws instead of returning false)
      $installed = (Test-Path -LiteralPath (Join-Path $CheqDir 'main.py')) -and
                   (Test-Path -LiteralPath (Join-Path (Join-Path (Join-Path $CheqDir '.venv') 'Scripts') 'python.exe')) -and
                   (Test-Path -LiteralPath (Join-Path $CheqDir 'extra_model_paths.yaml'))
    } catch { $installed = $false }
    if (-not $installed) {
      $msg = ("CheqUp's own ComfyUI is not installed at " + $CheqDir + ". Install it (it never touches shorts-factory's ComfyUI, " +
              "its venv or port 8188, so the PC session may run it itself):`n  " + $InstallCmd + "`nthen run again.")
      if ($SkipModels) { Add-Note ("CheqUp's ComfyUI is not installed (" + $CheqDir + '): -SkipModels, so b-roll uses CheqUp stills and music the procedural bed.') }
      else { Finish 4 $msg }
    } else {
      Write-Host ("CheqUp's ComfyUI is not answering at " + $script:CheqUrl + ': starting it (scripts/comfy_cheq_start.ps1)')
      $psExe = (Get-Process -Id $PID).Path
      $prev = $ErrorActionPreference
      $ErrorActionPreference = 'Continue'
      & $psExe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'comfy_cheq_start.ps1') -Dest $CheqDir -Port $CheqPort 2>&1 |
        ForEach-Object { Write-Host ([string]$_) }
      $startExit = $LASTEXITCODE
      $ErrorActionPreference = $prev
      $stats = Get-ComfyStats $script:CheqUrl
      if ($null -eq $stats) {
        $msg = ("CheqUp's ComfyUI did not come up at " + $script:CheqUrl + ' (comfy_cheq_start.ps1 exit ' + $startExit +
                '; its log is out\logs\comfy_cheq_<ts>.log). Nothing was rendered.')
        if ($SkipModels) { Add-Note ($msg.Replace(' Nothing was rendered.', '') + ' -SkipModels, so b-roll uses CheqUp stills and music the procedural bed.') }
        elseif ($startExit -eq 4) { Finish 4 ($msg + "`n" + $UpdateFix) }
        else { Finish 3 $msg }
      }
    }
  }
  if ($null -ne $stats) {
    $verText = [string]$stats.system.comfyui_version
    $ver = ConvertTo-ComfyVersion $verText
    if ($null -eq $ver -or $ver -lt $MinComfy) {
      $msg = "CheqUp's ComfyUI " + $(if ($verText) { $verText } else { '(no version reported)' }) + ' at ' + $script:CheqUrl + ' is older than ' + $MinComfy + ', which the Qwen-Image + SeedVR2 b-roll graphs need.'
      if ($Draft -or $SkipModels) { Add-Note ($msg + ' Not enforced (-Draft/-SkipModels): b-roll the quality graphs cannot make uses CheqUp stills.') }
      else { Finish 4 ($msg + "`n" + $UpdateFix + "`nOr run with -Draft (b-roll then falls back to CheqUp stills).") }
    } else {
      Write-Host ("ok   CheqUp's ComfyUI " + $verText + ' at ' + $script:CheqUrl + ' (' + $CheqDir + ')')
    }
  }

  # --- 5. shorts-factory: read-only look (its own ComfyUI; the GPU gate waits for it, no pause needed) ---------
  foreach ($y in $Factory) {
    $fq = $null
    try { $fq = Invoke-RestMethod -Uri ([string]$y.url + '/queue') -Method Get -TimeoutSec 10 -UseBasicParsing } catch { $fq = $null }
    if ($null -eq $fq) { Write-Host ('note: ' + $y.name + "'s ComfyUI (" + $y.url + ') is not answering (read-only check): the GPU gate treats a refused connection as idle and a stalled one as busy.') }
    else {
      $nr = Get-Count $fq.queue_running
      $np = Get-Count $fq.queue_pending
      if (($nr + $np) -gt 0) {
        Add-Note ($y.name + "'s ComfyUI (" + $y.url + ') had ' + $nr + ' running and ' + $np + " queued job(s) at the start: CheqUp's GPU jobs wait for it (gpu_gate).")
      } else { Write-Host ('ok   ' + $y.name + "'s ComfyUI " + $y.url + ': idle now (read-only check; the GPU gate waits whenever it has work)') }
    }
  }
  if (-not ($CfgInfo.gpu_gate -and $CfgInfo.gpu_gate.enabled)) {
    Add-Note ('gpu_gate is disabled in ' + $Config + ": CheqUp does NOT wait for shorts-factory's jobs on the shared GPU.")
  } else {
    Write-Host ('ok   GPU gate on: waits for ' + ((@($Factory) | ForEach-Object { $_.name }) -join ', ') + ' and for free VRAM (gives up after ' +
                ([double]$CfgInfo.gpu_gate.max_wait_s / 3600) + ' h: exit 12)')
  }
  if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
    try {
      $mem = (& nvidia-smi '--query-gpu=memory.used,memory.total' '--format=csv,noheader,nounits' | Select-Object -First 1)
      if ($mem) { $mm = ([string]$mem).Split(','); Write-Host ('     GPU memory now: ' + $mm[0].Trim() + ' of ' + $mm[1].Trim() + ' MiB in use') }
    } catch { }
  }
  $factory = [string]$CfgInfo.factory_dir
  if (-not $factory) { $factory = $FactoryDefault }
  $stop = Join-Path (Join-Path $factory 'data') 'STOP'
  if (Test-Path -LiteralPath (Join-Path $factory 'data')) {
    if (Test-Path -LiteralPath $stop) { Write-Host ('ok   shorts-factory paused (' + $stop + ' exists)') }
    elseif ($PauseFactory) {
      New-Item -ItemType File -Force -Path $stop | Out-Null
      Add-Note ('Paused shorts-factory: created ' + $stop + '. Only Dan resumes it (delete that file when he says).')
    } else {
      Write-Host 'ok   shorts-factory not paused (not needed: CheqUp has its own ComfyUI and the GPU gate yields to it)'
    }
  } else {
    Write-Host ('note: shorts-factory not found at ' + $factory + ' (shorts_factory.dir in ' + $Config + '): its pause file was not checked.')
  }
  if ($OnWindows) {
    try {
      $fp = @(Get-CimInstance Win32_Process -ErrorAction Stop | Where-Object { $_.ProcessId -ne $PID -and $_.CommandLine -and $_.CommandLine -like '*shorts-factory*' -and $_.CommandLine -notlike '*pc_run*' })
      if ($fp.Count -gt 0) { Write-Host ('note: ' + $fp.Count + ' shorts-factory process(es) running (PIDs ' + (($fp | ForEach-Object { $_.ProcessId }) -join ', ') + ')') }
    } catch { }
  }

  # --- 6. cqf doctor: env, voice, b-roll models, ACE-Step -----------------------------------------------------
  $dargs = @('-m', 'cqf', '--config', $Config, 'doctor')
  if ($Clips) { $dargs += '--clips' }
  $dc = Invoke-Py $dargs
  $doc = @($script:PyLines)
  if (($dc -ne 0 -and $dc -ne 1) -or (($doc -join "`n") -match 'Traceback \(most recent call last\)')) {
    Finish 5 ('cqf doctor crashed (exit ' + $dc + '): the Python env is broken. Rerun scripts/setup_pc.ps1.')
  }
  $toolsBad = @(); $models = @(); $old = @(); $envBad = @()
  $farmDown = $false; $farmSkip = $false; $brollMissing = @(); $musicOld = @()
  $voiceOn = @('tts', 'kokoro', 'chatterbox') -contains [string]$CfgInfo.voice_backend
  foreach ($line in $doc) {
    if ($line -match '^MISS (node|ffmpeg|ffprobe)\b') { $toolsBad += $Matches[1] }
    elseif ($line -match '^MISS playwright') { $toolsBad += 'playwright (cd render; npm install; npx playwright install chromium)' }
    elseif ($line -match '^FAIL (.*)$') { $envBad += $Matches[1] }        # e.g. a misspelt music.backend in the config
    elseif ($line -match '^MISS kokoro') { if ($voiceOn) { $envBad += 'kokoro (the voice fallback) is not installed in .venv' } }
    elseif ($line -match '^MISS chatterbox env') { if ($voiceOn) { $models += 'voice: Chatterbox env .venv-chatterbox missing (scripts/setup_pc.ps1)' } }
    elseif ($line -match '^MISS chatterbox reference (\S+)') { if ($voiceOn) { $models += ('voice: reference clip ' + $Matches[1] + ' missing (.venv/Scripts/python.exe scripts/make_voice_refs.py --config config.pc.yaml)') } }
    elseif ($line -match '^\s+(http\S+): skipping: (.*)$') {
      $farmSkip = $true
      foreach ($p in (($Matches[2] -replace '\s*\(see [^)]*\)\s*$', '') -split ';\s*')) {
        if ($p -match 'missing model (\S+)') { $brollMissing += $Matches[1] }
        elseif ($p.Trim()) { $old += $p.Trim() }      # version / missing node / system_stats problems
      }
    }
    elseif ($line -match '^DOWN ComfyUI farm') { $farmDown = $true }
    elseif ($line -match '^WARN music: ACE-Step 1\.5 unavailable \((.*)\)') {
      $why = $Matches[1]
      if ($why -match 'missing node') { $musicOld += ('music: ACE-Step 1.5 nodes missing (' + $why + ')') }   # ComfyUI < v0.12
      elseif ($why -match 'not responding|no ComfyUI') { $models += ('music: ACE-Step 1.5 unavailable: ' + $why) }
      else { $models += ('music: ACE-Step 1.5 unavailable: ' + $why + ' (scripts/fetch_models_pc.ps1)') }
    }
    elseif ($line -match '^MISS LLM backend') { Add-Note 'claude CLI not found: b-roll stills get no vision check (UNVERIFIED), so those boards stay on HOLD.' }
    elseif ($voiceOn -and $line -match '^MISS (faster-whisper|praat-parselmouth)') { Add-Note ($Matches[1] + ' missing in .venv: Chatterbox takes are ranked less well (scripts/setup_pc.ps1).') }
  }
  if ($brollMissing.Count -gt 0) {
    $uniq = @($brollMissing | Select-Object -Unique)
    $cnt = [string]$uniq.Count
    if (($brollMissing.Count + $old.Count) -ge 4) { $cnt = 'at least ' + $cnt }    # cqf doctor lists 4 problems per server at most
    $models = @('b-roll: ' + $cnt + ' model file(s) not in ComfyUI: ' + ($uniq -join ', ') + ' (scripts/fetch_models_pc.ps1)') + $models
  }
  if ($toolsBad.Count -gt 0) { Finish 2 ('cqf doctor: missing ' + ($toolsBad -join ', ') + '. Rerun scripts/setup_pc.ps1.') }
  if ($envBad.Count -gt 0) { Finish 5 ('cqf doctor: ' + ($envBad -join '; ') + '. Rerun scripts/setup_pc.ps1.') }
  if ($Draft) {
    $kept = @()
    foreach ($m in $models) { if ($m -like 'b-roll:*') { Add-Note ('-Draft: ' + $m + ': CheqUp stills instead.') } else { $kept += $m } }
    $models = $kept
    foreach ($o in $old) { Add-Note ('-Draft: ' + $o + '; b-roll falls back to CheqUp stills.') }
    $old = @()
  }
  foreach ($o in $musicOld) {
    if ($Draft -or $SkipModels) { Add-Note ($o + ': ComfyUI version not enforced (-Draft/-SkipModels), so the procedural music bed instead.') }
    else { $old += $o }
  }
  if ($old.Count -gt 0) {
    if ($SkipModels) { foreach ($o in $old) { Add-Note ('-SkipModels: ' + $o + '; b-roll falls back to CheqUp stills.') } }
    else { Finish 4 ('The b-roll/music graphs cannot run on this ComfyUI: ' + ($old -join '; ') + "`n" + $UpdateFix) }
  }
  if ($models.Count -gt 0) {
    if ($SkipModels) { foreach ($m in $models) { Add-Note ('-SkipModels: ' + $m + ': that piece falls back.') } }
    else {
      Finish 6 ("Missing models/files (nothing was rendered):`n  " + ($models -join "`n  ") +
                "`nscripts/fetch_models_pc.ps1 downloads the ComfyUI models into the shared models folder (~47 GB + ACE-Step 9.3 GB," +
                " resumable; then restart CheqUp's ComfyUI: comfy_cheq_stop.ps1, and pc_run starts it again)." +
                "`nOr run with -SkipModels to accept fallbacks (exit 8).")
    }
  }
  if ($farmDown -and -not $farmSkip -and -not $SkipModels -and -not $Draft) {
    Finish 3 ("cqf found no live ComfyUI for b-roll (CheqUp's ComfyUI stopped during pre-flight?). Run again: pc_run starts it.")
  }
  Write-Host 'ok   pre-flight done'

  # --- 7. make -> outbox -> report ---------------------------------------------------------------------------
  $script:WantPack = $true
  if ($null -ne (Get-ComfyStats $script:CheqUrl)) { $script:FreeCheq = $true }    # Finish frees its VRAM afterwards
  $margs = @('-m', 'cqf', '--config', $Config, 'make') + $script:BoardFiles
  foreach ($f in $FormatTokens) { $margs += @('--format', $f) }
  if ($Strict) { $margs += '--strict' }
  if ($Clips) { $margs += '--clips' }
  Write-Host ''
  Write-Host ('=== make ' + (Get-Date).ToString('s'))
  $makeExit = Invoke-Py $margs
  Write-Host ('=== make exit ' + $makeExit + '  ' + (Get-Date).ToString('s'))

  Write-Host '=== outbox'
  $outboxExit = Invoke-Py @('-m', 'cqf', '--config', $Config, 'outbox')
  $outboxPath = $null
  foreach ($l in $script:PyLines) { if ($l -match '^outbox -> (.+)$') { $outboxPath = $Matches[1].Trim() } }

  Write-Host '=== report'
  # --opt=value: argparse would read a value starting with '-' (e.g. --flags -SkipModels) as an option.
  $rargs = @('-m', 'cqf', '--config', $Config, 'report') + $script:BoardFiles +
           @(('--since=' + $Started.ToString('s')), ('--run-id=' + $RunId), ('--log=' + $LogPath), ('--pack-path=' + $PackPath),
             ('--make-exit=' + $makeExit), ('--outbox-exit=' + $outboxExit))
  if ($FlagText) { $rargs += ('--flags=' + (Clean-Note $FlagText)) }
  if ($outboxPath) { $rargs += ('--outbox=' + $outboxPath) }
  foreach ($n in $Notes) { $rargs += ('--note=' + $n) }
  # A failed report must not leave the previous run's REPORT.md looking like this run's (in out\ or in the zip).
  foreach ($stale in @($ReportJson, $ReportPath)) { Remove-Item -LiteralPath $stale -Force -ErrorAction SilentlyContinue }
  $reportExit = Invoke-Py $rargs
  $code = 9
  if ($reportExit -eq 0 -and (Test-Path -LiteralPath $ReportJson)) {
    try {
      $rep = Get-Content -LiteralPath $ReportJson -Raw -Encoding UTF8 | ConvertFrom-Json
      $code = [int]$rep.exit
      $script:ReportOk = $true
      Copy-Item -LiteralPath $ReportPath -Destination (Join-Path $Logs ('REPORT_' + $RunId + '.md')) -Force
    } catch { Write-Host ('could not read ' + $ReportJson + ': ' + $_) }
  } else {
    Write-Host ('cqf report failed (exit ' + $reportExit + '): out\REPORT.md is not this run''s result.')
  }
  Write-Host ''
  if (Test-Path -LiteralPath $ReportPath) { if ($script:ReportOk) { Write-Host ('REPORT: ' + $ReportPath) } }
  Finish $code
}
catch {
  Write-Host ('pc_run crashed: ' + $_.Exception.Message)
  if ($_.ScriptStackTrace) { Write-Host $_.ScriptStackTrace }
  Finish 9
}
