<#
.SYNOPSIS
  Stop CheqUp's OWN ComfyUI (C:\Users\white\ComfyUI-CheqUp, port 8288) and nothing else.

.DESCRIPTION
  Stops only processes whose command line names -Dest (the PID that comfy_cheq_start.ps1 wrote to
  <Dest>\comfy_cheq.pid, its child processes, and any "main.py" process started from -Dest). A process whose
  command line mentions shorts-factory's ComfyUI folder or venv is never touched, and neither is anything on
  port 8188. Refuses while a CheqUp render (pc_run) is going, unless -Force: the run would lose its ComfyUI.

  Use it to restart CheqUp's ComfyUI (stop, then scripts/comfy_cheq_start.ps1), e.g. after new model files or
  before re-running scripts/install_comfy_cheq_pc.ps1. shorts-factory's ComfyUI keeps running throughout.

  Exit codes: 0 stopped (or was not running), 1 port <Port> still answers afterwards, 5 not Windows or refused
  (-Port 8188, or a -Dest that is not a ComfyUI install or overlaps shorts-factory's folders), 10 a CheqUp run is
  going (nothing stopped).

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File scripts/comfy_cheq_stop.ps1
#>
[CmdletBinding()]
param(
  [string]$Dest = 'C:\Users\white\ComfyUI-CheqUp',
  [int]$Port = 8288,
  [switch]$Force,
  [string]$FactoryComfy = 'C:\Users\white\ComfyUI-Installs\ComfyUI\ComfyUI',
  [string]$FactoryVenv = 'C:\Users\white\ComfyUI-Factory-venv',
  [string]$FactoryDir = 'C:\Users\white\heatmap\shorts-factory',
  [int]$FactoryPort = 8188
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$OnWindows = ($env:OS -eq 'Windows_NT')
$Root = Split-Path -Parent $PSScriptRoot
$LockPath = Join-Path (Join-Path (Join-Path $Root 'out') 'logs') 'pc_run.lock'
$Url = 'http://127.0.0.1:' + $Port

function Get-Full([string]$P) {
  return ([System.IO.Path]::GetFullPath($P)).TrimEnd([char[]]@('\', '/'))
}

function Test-Under([string]$Child, [string]$Parent) {
  # True when $Child is $Parent or inside it (case-insensitive, either slash).
  $c = (Get-Full $Child).Replace('/', '\').ToLowerInvariant()
  $p = (Get-Full $Parent).Replace('/', '\').ToLowerInvariant()
  return (($c -eq $p) -or $c.StartsWith($p + '\'))
}

if (-not $OnWindows) { Write-Host 'comfy_cheq_stop.ps1 runs on the Windows PC only.'; exit 5 }
if ($Port -eq $FactoryPort) { Write-Host ('Refused: port ' + $Port + " is shorts-factory's ComfyUI; this only stops CheqUp's."); exit 5 }
$Dest = Get-Full $Dest
# A wrong -Dest (a parent folder, shorts-factory's own folder) would widen the command-line match below to other
# programs' processes: only a ComfyUI install that is not inside, and does not contain, shorts-factory's folders.
foreach ($f in @($FactoryComfy, $FactoryVenv, $FactoryDir)) {
  if ((Test-Under $Dest $f) -or (Test-Under $f $Dest)) {
    Write-Host ('Refused: -Dest ' + $Dest + " overlaps shorts-factory's " + $f + '. This only stops CheqUp''s own ComfyUI.'); exit 5
  }
}
if (-not (Test-Path -LiteralPath (Join-Path $Dest 'comfyui_version.py'))) {
  if (Test-Path -LiteralPath $Dest) { Write-Host ('Refused: ' + $Dest + ' is not a ComfyUI install (no comfyui_version.py). Nothing stopped.'); exit 5 }
  Write-Host ("CheqUp's ComfyUI is not installed at " + $Dest + ': nothing to stop.'); exit 0
}
$DestKey = $Dest.ToLowerInvariant() + '\'
$FactoryKeys = @($FactoryComfy.ToLowerInvariant(), $FactoryVenv.ToLowerInvariant(), (Get-Full $FactoryDir).ToLowerInvariant())
$PidFile = Join-Path $Dest 'comfy_cheq.pid'

# --- not during a CheqUp run -----------------------------------------------------------------------------------
if ((Test-Path -LiteralPath $LockPath) -and -not $Force) {
  $lock = $null
  try { $lock = Get-Content -LiteralPath $LockPath -Raw -Encoding UTF8 | ConvertFrom-Json } catch { $lock = $null }
  $alive = $false
  if ($null -ne $lock) {
    $lp = Get-Process -Id ([int]$lock.pid) -ErrorAction SilentlyContinue
    $alive = ($null -ne $lp) -and ($lp.ProcessName -match '^(powershell|pwsh)$')
  }
  if ($alive) {
    Write-Host ('A CheqUp run is going (run ' + $lock.run_id + ', PID ' + $lock.pid + '): not stopping its ComfyUI. Wait for it (scripts/pc_wait.ps1).')
    exit 10
  }
}

function Test-Ours($P) {
  if ($null -eq $P -or -not $P.CommandLine) { return $false }
  $cl = ([string]$P.CommandLine).ToLowerInvariant()
  foreach ($k in $FactoryKeys) { if ($cl.Contains($k)) { return $false } }
  return $cl.Contains($DestKey)
}

$all = @(Get-CimInstance Win32_Process)
$targets = @{}
$rootPid = 0
if (Test-Path -LiteralPath $PidFile) {
  try { $rootPid = [int]((Get-Content -LiteralPath $PidFile -Raw | ConvertFrom-Json).pid) } catch { $rootPid = 0 }
}
if ($rootPid -gt 0) {
  $front = @($rootPid)
  while ($front.Count -gt 0) {                    # the start PID (cmd.exe) and its descendants, if they name -Dest
    $next = @()
    foreach ($id in $front) {
      $p = $all | Where-Object { $_.ProcessId -eq $id } | Select-Object -First 1
      if (Test-Ours $p) { $targets[[int]$id] = $p.Name }
      foreach ($k in @($all | Where-Object { $_.ParentProcessId -eq $id })) {
        if (-not $targets.ContainsKey([int]$k.ProcessId)) { $next += [int]$k.ProcessId }
      }
    }
    $front = $next
  }
}
foreach ($p in $all) {                            # any ComfyUI started from -Dest without (or with a stale) PID file
  if ($p.ProcessId -ne $PID -and (Test-Ours $p) -and ([string]$p.CommandLine).ToLowerInvariant().Contains('main.py')) {
    $targets[[int]$p.ProcessId] = $p.Name
  }
}
$targets.Remove($PID)

if ($targets.Count -eq 0) {
  Write-Host ("CheqUp's ComfyUI is not running (no process names " + $Dest + ').')
  Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
  exit 0
}
foreach ($id in @($targets.Keys | Sort-Object -Descending)) {
  Write-Host ('stopping PID ' + $id + ' (' + $targets[$id] + ')')
  Stop-Process -Id $id -Force -ErrorAction SilentlyContinue
}
$deadline = (Get-Date).AddSeconds(20)
$up = $true
while ((Get-Date) -lt $deadline) {
  try { $null = Invoke-RestMethod -Uri ($Url + '/system_stats') -Method Get -TimeoutSec 3 -UseBasicParsing; $up = $true } catch { $up = $false }
  if (-not $up) { break }
  Start-Sleep -Seconds 2
}
Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
if ($up) { Write-Host ('Port ' + $Port + ' still answers: something else serves it (not stopped).'); exit 1 }
Write-Host ("Stopped CheqUp's ComfyUI (" + $Dest + "). shorts-factory's ComfyUI was not touched.")
exit 0
