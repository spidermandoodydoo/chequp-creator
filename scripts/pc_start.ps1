<#
.SYNOPSIS
  Start scripts/pc_run.ps1 detached in a hidden PowerShell and return within about 30 seconds.

.DESCRIPTION
  For the Claude Code session on the PC (PUPPET.md): a render takes hours, longer than any shell-tool timeout,
  so it must not run under the tool. This starts it on its own, prints the run id, PID, log and done marker,
  watches the first ~30 s (pre-flight failures show up here) and returns. Then wait with scripts/pc_wait.ps1.

  One run at a time: out\logs\pc_run.lock names the running run; a second start exits 10 and starts nothing.
  Takes the same options as pc_run.ps1 and passes them on.

  Exit codes:
    0   started and still running: wait with pc_wait.ps1
    10  a run is already going: start nothing, wait for that one
    9   pc_run died at start without writing its done marker
    other: the run already ended during pre-flight with that code (pc_run.ps1 table: 2-7, ...)

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File scripts/pc_start.ps1 -Boards numan -Formats 9x16
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
  [int]$Settle = 30
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $Root
$OnWindows = ($env:OS -eq 'Windows_NT')
$Logs = Join-Path (Join-Path $Root 'out') 'logs'
New-Item -ItemType Directory -Force -Path $Logs | Out-Null
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

function Get-LockJson([string]$RunId, [int]$ProcId, [string]$Log, [string]$Done) {
  $o = [ordered]@{ run_id = $RunId; pid = $ProcId; started = (Get-Date).ToString('s'); log = $Log; done = $Done;
                   host = $env:COMPUTERNAME; by = 'pc_start' }
  return ($o | ConvertTo-Json)
}

function New-LockFile([string]$Json) {
  # Atomic: fails if the lock already exists, so two starts can't both win.
  try {
    $fs = [System.IO.File]::Open($LockPath, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
  } catch { return $false }
  try {
    $bytes = (New-Object System.Text.UTF8Encoding($false)).GetBytes($Json)
    $fs.Write($bytes, 0, $bytes.Length)
  } finally { $fs.Close() }
  return $true
}

function Show-Tail([string]$Path, [int]$Lines) {
  if (Test-Path -LiteralPath $Path) {
    Write-Host ('--- last ' + $Lines + ' lines of ' + $Path)
    Get-Content -LiteralPath $Path -Tail $Lines -Encoding UTF8 | ForEach-Object { Write-Host $_ }
    Write-Host '---'
  } else {
    Write-Host ('(no log yet at ' + $Path + ')')
  }
}

$RunId = Get-Date -Format 'yyyyMMdd-HHmmss'
$LogPath = Join-Path $Logs ('pc_run_' + $RunId + '.log')
$DonePath = Join-Path $Logs ('pc_run_' + $RunId + '.done')

if (-not (New-LockFile (Get-LockJson $RunId 0 $LogPath $DonePath))) {
  $lock = Read-Lock
  $starting = $false
  if ($null -ne $lock) {
    try { $starting = ([int]$lock.pid -eq 0) -and (((Get-Date) - (Get-LockTime $lock.started)).TotalSeconds -lt 90) } catch { }
  } else {
    # Unreadable: another pc_start may be writing it this very moment. Only an old unreadable lock is stale.
    try { $starting = (((Get-Date) - (Get-Item -LiteralPath $LockPath).LastWriteTime).TotalSeconds -lt 15) } catch { }
  }
  if ((Test-RunAlive $lock) -or $starting) {
    if ($null -eq $lock) { Write-Host ('A CheqUp run is starting right now (' + $LockPath + ').') }
    else {
      Write-Host ('A CheqUp run is already going: run ' + $lock.run_id + ', PID ' + $lock.pid)
      Write-Host ('  log:  ' + $lock.log)
      Write-Host ('  done: ' + $lock.done)
    }
    Write-Host 'Not starting another. Wait for it: powershell -NoProfile -ExecutionPolicy Bypass -File scripts/pc_wait.ps1 -Minutes 100'
    exit 10
  }
  Write-Host ('Removing a stale lock (run ' + $(if ($lock) { $lock.run_id } else { '?' }) + ' is not running).')
  Remove-Item -LiteralPath $LockPath -Force -ErrorAction SilentlyContinue
  if (-not (New-LockFile (Get-LockJson $RunId 0 $LogPath $DonePath))) {
    Write-Host 'Another start took the lock just now. Not starting.'
    exit 10
  }
}

# Same options to pc_run.ps1, quoted for paths with spaces (Start-Process joins arguments with plain spaces).
$runScript = Join-Path $PSScriptRoot 'pc_run.ps1'
$argLine = '-NoProfile -ExecutionPolicy Bypass '
if ($OnWindows) { $argLine += '-WindowStyle Hidden ' }
$argLine += '-File "' + $runScript + '" -RunId ' + $RunId
$bt = @($Boards | ForEach-Object { ([string]$_).Replace('"', '') } | Where-Object { $_ })
if ($bt.Count -gt 0) { $argLine += ' -Boards "' + ($bt -join ',') + '"' }
$ft = @($Formats | ForEach-Object { ([string]$_).Replace('"', '') } | Where-Object { $_ })
if ($ft.Count -gt 0) { $argLine += ' -Formats "' + ($ft -join ',') + '"' }
if ($Strict) { $argLine += ' -Strict' }
if ($SkipModels) { $argLine += ' -SkipModels' }
if ($Draft) { $argLine += ' -Draft' }
if ($Clips) { $argLine += ' -Clips' }
if ($PauseFactory) { $argLine += ' -PauseFactory' }

$psExe = (Get-Process -Id $PID).Path            # powershell.exe (5.1) on the PC; pwsh if started from pwsh
try {
  if ($OnWindows) {
    $proc = Start-Process -FilePath $psExe -ArgumentList $argLine -WorkingDirectory $Root -WindowStyle Hidden -PassThru
  } else {
    $proc = Start-Process -FilePath $psExe -ArgumentList $argLine -WorkingDirectory $Root -PassThru
  }
} catch {
  Remove-Item -LiteralPath $LockPath -Force -ErrorAction SilentlyContinue
  Write-Host ('Could not start pc_run.ps1: ' + $_)
  exit 9
}
try { $null = $proc.Handle } catch { }            # keeps ExitCode readable after exit (Windows PowerShell quirk)
# pc_run rewrites the lock with its own PID (the same process); write it now so a quick second start sees it.
try {
  [System.IO.File]::WriteAllText($LockPath, (Get-LockJson $RunId $proc.Id $LogPath $DonePath), (New-Object System.Text.UTF8Encoding($false)))
} catch { }

Write-Host ('Started CheqUp run ' + $RunId)
Write-Host ('  PID:  ' + $proc.Id)
Write-Host ('  log:  ' + $LogPath)
Write-Host ('  done: ' + $DonePath + '  (written last; holds the exit code)')
Write-Host ('  args: ' + $argLine)

$deadline = (Get-Date).AddSeconds($Settle)
while ((Get-Date) -lt $deadline) {
  if (Test-Path -LiteralPath $DonePath) { break }
  if ($proc.HasExited) { Start-Sleep -Seconds 2; break }
  Start-Sleep -Seconds 2
}

if (Test-Path -LiteralPath $DonePath) {
  $d = Get-Content -LiteralPath $DonePath -Raw -Encoding UTF8 | ConvertFrom-Json
  $code = [int]$d.exit
  Write-Host ('The run already ended: exit ' + $code + ' (' + $ExitMeaning[$code] + ')')
  Show-Tail $LogPath 40
  exit $code
}
if ($proc.HasExited) {
  Write-Host ('pc_run exited at start without writing its done marker (exit ' + $proc.ExitCode + ').')
  Show-Tail $LogPath 40
  $lock = Read-Lock
  if ($null -ne $lock -and [string]$lock.run_id -eq $RunId) { Remove-Item -LiteralPath $LockPath -Force -ErrorAction SilentlyContinue }
  if ($proc.ExitCode -eq 10) { exit 10 }
  exit 9
}
Write-Host ('Running (pre-flight so far ok). Now wait, with run_in_background and timeout 7200000:')
Write-Host '  powershell -NoProfile -ExecutionPolicy Bypass -File scripts/pc_wait.ps1 -Minutes 100'
Show-Tail $LogPath 12
exit 0
