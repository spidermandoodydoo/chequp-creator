<#
.SYNOPSIS
  Start CheqUp's OWN ComfyUI (C:\Users\white\ComfyUI-CheqUp, port 8288) hidden in the background, if it isn't up.

.DESCRIPTION
  If http://127.0.0.1:<Port>/system_stats already answers from this install, it prints that and exits 0.
  Otherwise it starts <Dest>\.venv\Scripts\python.exe main.py on 127.0.0.1:<Port> with the shared model folders
  (<Dest>\extra_model_paths.yaml) and its own output, input, temp and user folders under <Dest>, so nothing is
  written into shorts-factory's folders. No browser opens. Output goes to out\logs\comfy_cheq_<ts>.log; it waits
  up to -WaitSeconds for /system_stats and prints the version and PID. The PID goes to <Dest>\comfy_cheq.pid
  (scripts/comfy_cheq_stop.ps1 reads it).

  It never touches shorts-factory's ComfyUI (port 8188), its venv or its files, and refuses -Port 8188.
  pc_run.ps1 runs this itself when 8288 isn't answering.

  Exit codes: 0 running, 3 did not come up (the log tail is printed), 4 not installed (run
  scripts/install_comfy_cheq_pc.ps1), 5 refused (port 8188, a -Dest inside shorts-factory's install, the port
  is taken by another program, or not Windows).

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File scripts/comfy_cheq_start.ps1
#>
[CmdletBinding()]
param(
  [string]$Dest = 'C:\Users\white\ComfyUI-CheqUp',
  [int]$Port = 8288,
  [string]$LogDir = '',
  [int]$WaitSeconds = 180,
  [double]$ReserveVram = 0,
  [string]$FactoryComfy = 'C:\Users\white\ComfyUI-Installs\ComfyUI\ComfyUI',
  [string]$FactoryVenv = 'C:\Users\white\ComfyUI-Factory-venv',
  [int]$FactoryPort = 8188
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$OnWindows = ($env:OS -eq 'Windows_NT')
$Root = Split-Path -Parent $PSScriptRoot
if (-not $LogDir) { $LogDir = Join-Path (Join-Path $Root 'out') 'logs' }
$Url = 'http://127.0.0.1:' + $Port

function Get-Full([string]$P) {
  return ([System.IO.Path]::GetFullPath($P)).TrimEnd([char[]]@('\', '/'))
}

function Test-Under([string]$Child, [string]$Parent) {
  $c = (Get-Full $Child).Replace('/', '\').ToLowerInvariant()
  $p = (Get-Full $Parent).Replace('/', '\').ToLowerInvariant()
  return (($c -eq $p) -or $c.StartsWith($p + '\'))
}

function Get-Stats {
  try { return (Invoke-RestMethod -Uri ($Url + '/system_stats') -Method Get -TimeoutSec 5 -UseBasicParsing) } catch { return $null }
}

function Test-OursStats($Stats) {
  # This install answered: its argv names <Dest> (--output-directory <Dest>\output etc.).
  $argv = (@($Stats.system.argv) -join ' ').ToLowerInvariant()
  return $argv.Contains($Dest.ToLowerInvariant() + '\')
}

function Test-OursProcess([int]$ProcId) {
  # A live process whose command line names <Dest> and not shorts-factory's install or venv.
  $p = Get-CimInstance Win32_Process -Filter ('ProcessId = ' + $ProcId) -ErrorAction SilentlyContinue
  if ($null -eq $p -or -not $p.CommandLine) { return $false }
  $cl = $p.CommandLine.ToLowerInvariant()
  if ($cl.Contains($FactoryComfy.ToLowerInvariant()) -or $cl.Contains($FactoryVenv.ToLowerInvariant())) { return $false }
  return $cl.Contains($Dest.ToLowerInvariant() + '\')
}

function Show-Tail([string]$Path) {
  if (Test-Path -LiteralPath $Path) {
    Write-Host ('--- last 40 lines of ' + $Path)
    Get-Content -LiteralPath $Path -Tail 40 -Encoding UTF8 | ForEach-Object { Write-Host $_ }
    Write-Host '---'
  } else { Write-Host ('(no log at ' + $Path + ')') }
}

function Quote([string]$S) {
  return ('"' + $S + '"')
}

$Dest = Get-Full $Dest
$PidFile = Join-Path $Dest 'comfy_cheq.pid'
if ($Port -eq $FactoryPort) { Write-Host ('Refused: port ' + $Port + " is shorts-factory's ComfyUI."); exit 5 }
if ((Test-Under $Dest $FactoryComfy) -or (Test-Under $FactoryComfy $Dest) -or (Test-Under $Dest $FactoryVenv)) {
  Write-Host ('Refused: -Dest ' + $Dest + " overlaps shorts-factory's install or venv."); exit 5
}

# --- already up? -----------------------------------------------------------------------------------------------
$stats = Get-Stats
if ($null -ne $stats) {
  $v = [string]$stats.system.comfyui_version
  if (Test-OursStats $stats) {
    $pidText = '?'
    if (Test-Path -LiteralPath $PidFile) { try { $pidText = [string]((Get-Content -LiteralPath $PidFile -Raw | ConvertFrom-Json).pid) } catch { } }
    Write-Host ('CheqUp ComfyUI ' + $v + ' already running on ' + $Url + ' (PID ' + $pidText + ', ' + $Dest + ')')
    exit 0
  }
  Write-Host ('Port ' + $Port + ' answers, but not from ' + $Dest + ' (ComfyUI ' + $v + ', argv: ' + (@($stats.system.argv) -join ' ') + ').')
  Write-Host 'Pick a free -Port and set comfy_cheq.port and the pc-5090 first_port in config.pc.yaml to it.'
  exit 5
}
if (-not $OnWindows) { Write-Host 'comfy_cheq_start.ps1 runs on the Windows PC only.'; exit 5 }

$Py = Join-Path $Dest '.venv\Scripts\python.exe'
$Yaml = Join-Path $Dest 'extra_model_paths.yaml'
if (-not (Test-Path -LiteralPath (Join-Path $Dest 'main.py')) -or -not (Test-Path -LiteralPath $Py) -or -not (Test-Path -LiteralPath $Yaml)) {
  Write-Host ("CheqUp's ComfyUI is not (fully) installed at " + $Dest + ' (main.py, .venv or extra_model_paths.yaml missing). Install it:')
  Write-Host '  powershell -NoProfile -ExecutionPolicy Bypass -File scripts/install_comfy_cheq_pc.ps1'
  exit 4
}

# --- starting already? (a PID file naming a live process of this install) ---------------------------------------
$proc = $null
$LogPath = $null
if (Test-Path -LiteralPath $PidFile) {
  try {
    $pf = Get-Content -LiteralPath $PidFile -Raw | ConvertFrom-Json
    if ($pf.pid -and (Test-OursProcess ([int]$pf.pid))) {
      $proc = Get-Process -Id ([int]$pf.pid) -ErrorAction SilentlyContinue
      $LogPath = [string]$pf.log
      if ($null -ne $proc) { Write-Host ('CheqUp ComfyUI is already starting (PID ' + $pf.pid + '): waiting for it') }
    }
  } catch { $proc = $null }
}

if ($null -eq $proc) {
  New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
  foreach ($d in @('output', 'input', 'temp', 'user')) { New-Item -ItemType Directory -Force -Path (Join-Path $Dest $d) | Out-Null }
  $LogPath = Join-Path $LogDir ('comfy_cheq_' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.log')
  $cargs = @('main.py', '--listen', '127.0.0.1', '--port', [string]$Port, '--extra-model-paths-config', $Yaml,
             '--output-directory', (Join-Path $Dest 'output'), '--input-directory', (Join-Path $Dest 'input'),
             '--temp-directory', (Join-Path $Dest 'temp'), '--user-directory', (Join-Path $Dest 'user'),
             '--disable-auto-launch')
  if ($ReserveVram -gt 0) { $cargs += @('--reserve-vram', [string]$ReserveVram) }
  # cmd.exe in its own hidden console: detached from the caller's window, stdout and stderr into one log.
  $inner = (Quote $Py) + ' ' + (($cargs | ForEach-Object { Quote ([string]$_) }) -join ' ') + ' > ' + (Quote $LogPath) + ' 2>&1'
  $argLine = '/d /s /c "' + $inner + '"'
  foreach ($n in @('PYTHONPATH', 'PYTHONHOME', 'VIRTUAL_ENV')) { Remove-Item -LiteralPath ('Env:' + $n) -ErrorAction SilentlyContinue }
  $env:PYTHONUNBUFFERED = '1'
  $env:PYTHONIOENCODING = 'utf-8'
  try {
    $proc = Start-Process -FilePath $env:ComSpec -ArgumentList $argLine -WorkingDirectory $Dest -WindowStyle Hidden -PassThru
  } catch {
    Write-Host ('Could not start ComfyUI: ' + $_)
    exit 3
  }
  try { $null = $proc.Handle } catch { }          # keeps ExitCode readable after exit (Windows PowerShell quirk)
  $o = [ordered]@{ pid = $proc.Id; port = $Port; dest = $Dest; log = $LogPath; started = (Get-Date).ToString('s') }
  [System.IO.File]::WriteAllText($PidFile, ($o | ConvertTo-Json), (New-Object System.Text.UTF8Encoding($false)))
  Write-Host ('Starting CheqUp ComfyUI: PID ' + $proc.Id + ', port ' + $Port + ', log ' + $LogPath)
}

# --- wait for /system_stats ------------------------------------------------------------------------------------
$deadline = (Get-Date).AddSeconds($WaitSeconds)
$stats = $null
while ((Get-Date) -lt $deadline) {
  Start-Sleep -Seconds 3
  $stats = Get-Stats
  if ($null -ne $stats) { break }
  if ($proc.HasExited) { break }
}
if ($null -ne $stats -and (Test-OursStats $stats)) {
  $kids = @(Get-CimInstance Win32_Process -Filter ('ParentProcessId = ' + $proc.Id) -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -like 'python*' } | ForEach-Object { $_.ProcessId })
  $pids = [string]$proc.Id
  if ($kids.Count -gt 0) { $pids += ' (python ' + ($kids -join ', ') + ')' }
  Write-Host ('CheqUp ComfyUI ' + [string]$stats.system.comfyui_version + ' running on ' + $Url + ': PID ' + $pids + ', log ' + $LogPath)
  exit 0
}
if ($proc.HasExited) { Write-Host ('ComfyUI exited during start-up (exit ' + $proc.ExitCode + ').') }
else { Write-Host ('ComfyUI is not answering on ' + $Url + ' after ' + $WaitSeconds + ' s (PID ' + $proc.Id + ' still running: it may still be loading; run this again to keep waiting).') }
Show-Tail $LogPath
exit 3
