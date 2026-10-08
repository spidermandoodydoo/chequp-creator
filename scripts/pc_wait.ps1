<#
.SYNOPSIS
  Wait for a CheqUp run started by pc_start.ps1, then print its result. Never stops or kills anything.

.DESCRIPTION
  Run it with the shell tool's run_in_background (timeout 7200000). It returns when the run writes its done
  marker (out\logs\pc_run_<id>.done), or at the deadline. Stopping pc_wait never stops the run.

  Which run: -RunId, else the run named in out\logs\pc_run.lock, else the newest out\logs\pc_run_*.log.

  Exit codes:
    11  still running at the deadline: run pc_wait again
    9   the run's process is gone and it never wrote its done marker (killed, crashed, PC restarted):
        out\REPORT.md is NOT its result; the last log lines are printed
    other: the run's own exit code from its done marker (pc_run.ps1 table)

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File scripts/pc_wait.ps1 -Minutes 100
#>
[CmdletBinding()]
param(
  [int]$Minutes = 100,
  [string]$RunId = '',
  [int]$Tail = 80,
  [int]$PollSeconds = 20
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Logs = Join-Path (Join-Path $Root 'out') 'logs'
$LockPath = Join-Path $Logs 'pc_run.lock'
$ExitMeaning = @{ 0 = 'ok'; 1 = 'a board failed'; 2 = 'tools missing'; 3 = 'ComfyUI down'; 4 = 'ComfyUI too old';
                  5 = 'Python env or config'; 6 = 'models missing'; 7 = 'unknown board or format'; 8 = 'fallbacks used';
                  9 = 'run crashed'; 10 = 'already running'; 11 = 'still running' }

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

function Test-Starting($Lock) {
  # pc_start writes the lock with pid 0 a moment before the run exists.
  if ($null -eq $Lock) { return $false }
  try { return ([int]$Lock.pid -eq 0) -and (((Get-Date) - (Get-LockTime $Lock.started)).TotalSeconds -lt 90) } catch { return $false }
}

function Show-Tail([string]$Path, [int]$Lines) {
  if (Test-Path -LiteralPath $Path) {
    Write-Host ('--- last ' + $Lines + ' lines of ' + $Path)
    Get-Content -LiteralPath $Path -Tail $Lines -Encoding UTF8 | ForEach-Object { Write-Host $_ }
    Write-Host '---'
  } else {
    Write-Host ('(no log at ' + $Path + ')')
  }
}

function Show-Done([string]$DonePath, [string]$LogPath) {
  $d = $null
  try { $d = Get-Content -LiteralPath $DonePath -Raw -Encoding UTF8 | ConvertFrom-Json } catch { }
  if ($null -eq $d) {
    Write-Host ('Done marker ' + $DonePath + ' is unreadable.')
    Show-Tail $LogPath $Tail
    exit 9
  }
  $code = [int]$d.exit
  $fin = $d.finished
  if ($fin -is [datetime]) { $fin = $fin.ToString('s') }
  Write-Host ('Run ' + $d.run_id + ' finished ' + $fin + ': exit ' + $code + ' (' + $ExitMeaning[$code] + ')')
  if ($d.report) { Write-Host ('  REPORT:     ' + $d.report + '  (paste it in full in your reply)') }
  else { Write-Host '  REPORT:     none for this run (it stopped before rendering; the reason is in the log)' }
  if ($d.pack) { Write-Host ('  review zip: ' + $d.pack) }
  Write-Host ('  log:        ' + $d.log)
  if ($code -eq 9) { Show-Tail $LogPath $Tail } else { Show-Tail $LogPath 40 }
  exit $code
}

if (-not $RunId) {
  $lock = Read-Lock
  if ($null -ne $lock -and $lock.run_id) { $RunId = [string]$lock.run_id }
  elseif (Test-Path -LiteralPath $Logs) {
    $latest = Get-ChildItem -LiteralPath $Logs -Filter 'pc_run_*.log' | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($null -ne $latest) { $RunId = $latest.BaseName.Substring(7) }
  }
}
if (-not $RunId) {
  Write-Host 'No CheqUp run found in out\logs. Start one with scripts/pc_start.ps1.'
  exit 9
}
$LogPath = Join-Path $Logs ('pc_run_' + $RunId + '.log')
$DonePath = Join-Path $Logs ('pc_run_' + $RunId + '.done')
$deadline = (Get-Date).AddMinutes($Minutes)
Write-Host ('Waiting for CheqUp run ' + $RunId + ' (up to ' + $Minutes + ' min). Stopping this wait never stops the run.')

while ($true) {
  if (Test-Path -LiteralPath $DonePath) { Show-Done $DonePath $LogPath }
  $lock = Read-Lock
  $ours = ($null -ne $lock -and [string]$lock.run_id -eq $RunId)
  if (-not ($ours -and ((Test-RunAlive $lock) -or (Test-Starting $lock)))) {
    Start-Sleep -Seconds 5                        # the marker may be landing right now
    if (Test-Path -LiteralPath $DonePath) { Show-Done $DonePath $LogPath }
    $lock = Read-Lock                             # a lock caught mid-write reads as empty: look again before calling it a crash
    $ours = ($null -ne $lock -and [string]$lock.run_id -eq $RunId)
    if ($ours -and ((Test-RunAlive $lock) -or (Test-Starting $lock))) { continue }
    Write-Host ('Run ' + $RunId + ' is not running and never wrote ' + $DonePath + '.')
    Write-Host 'It was killed or crashed, or the PC restarted. out\REPORT.md is NOT its result.'
    Show-Tail $LogPath $Tail
    exit 9
  }
  if ((Get-Date) -ge $deadline) {
    Write-Host ('Still running after ' + $Minutes + ' min (run ' + $RunId + ', PID ' + $lock.pid + '). Run pc_wait.ps1 again; do not stop the run.')
    Show-Tail $LogPath 15
    exit 11
  }
  Start-Sleep -Seconds $PollSeconds
}
