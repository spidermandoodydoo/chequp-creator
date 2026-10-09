<#
.SYNOPSIS
  Install (or repair) CheqUp's OWN ComfyUI v0.39.2 next to shorts-factory's, sharing its model files read-only.

.DESCRIPTION
  shorts-factory's ComfyUI (C:\Users\white\ComfyUI-Installs\ComfyUI\ComfyUI, venv C:\Users\white\ComfyUI-Factory-venv,
  port 8188) is older than the v0.39.2 CheqUp's graphs need, and it is busy rendering. Updating it in place would
  restart it and change its packages, so CheqUp gets its own install instead:

    1. git clone ComfyUI at -Tag (shallow) into -Dest; if -Dest is already that clone, fetch -Tag and check it out
    2. -Dest\.venv made with uv (Python 3.12): torch + torchvision from the PyTorch cu130 index when the driver
       supports CUDA 13 (what v0.39.2 wants on an RTX 5090), else cu128 (a re-run keeps the build installed;
       -TorchIndex with another CUDA build reinstalls torch), then ComfyUI's requirements.txt with torch pinned to it.
       No torchaudio: v0.39.2 never imports it, and its last release predates the newest torch.
    3. -Dest\extra_model_paths.yaml: every model folder type this ComfyUI knows (read from its folder_paths.py)
       points at the shared -Models folder (shorts-factory's), so no model file is copied or downloaded twice;
       then ComfyUI's own loader checks it (scripts/comfy_cheq_paths.py)
    4. a short summary: ComfyUI version, torch + CUDA, shared model folders

  Safe to re-run. It never touches shorts-factory's ComfyUI folder, its venv, its process or port 8188: it refuses
  a -Dest inside them, a -Port equal to the factory's, and a Python from the factory venv, and it only reads
  -Models. It refuses to run while CheqUp's own ComfyUI is running (stop it first: scripts/comfy_cheq_stop.ps1).
  Start the server with scripts/comfy_cheq_start.ps1 (pc_run.ps1 starts it by itself).

  Exit codes: 0 ok, 2 a tool is missing (git, uv, Python), 3 refused (unsafe -Dest/-Port/-Models, under -MinFreeGB free disk, or CheqUp's
  ComfyUI is running), 4 git failed, 5 venv or package install failed, 6 torch sees no CUDA GPU,
  7 extra_model_paths.yaml failed its check.

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File scripts/install_comfy_cheq_pc.ps1
#>
[CmdletBinding()]
param(
  [string]$Dest = 'C:\Users\white\ComfyUI-CheqUp',
  [string]$Tag = 'v0.39.2',
  [string]$Models = 'C:\Users\white\ComfyUI-Shared\models',   # the PC's shared models folder (the factory's :8188 loads from it too)
  [int]$Port = 8288,
  [string]$Repo = 'https://github.com/Comfy-Org/ComfyUI.git',
  [string]$TorchIndex = '',       # '' = cu130 when this driver supports CUDA 13 (nvidia-smi), else cu128
  [string]$FactoryComfy = 'C:\Users\white\ComfyUI-Installs\ComfyUI\ComfyUI',
  [string]$FactoryVenv = 'C:\Users\white\ComfyUI-Factory-venv',
  [string]$FactoryDir = 'C:\Users\white\heatmap\shorts-factory',
  [int]$FactoryPort = 8188,
  [int]$MinFreeGB = 30
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$OnWindows = ($env:OS -eq 'Windows_NT')
$script:Out = New-Object System.Collections.Generic.List[string]

function Get-Full([string]$P) {
  return ([System.IO.Path]::GetFullPath($P)).TrimEnd([char[]]@('\', '/'))
}

function Test-Under([string]$Child, [string]$Parent) {
  # True when $Child is $Parent or inside it (case-insensitive, either slash).
  $c = (Get-Full $Child).Replace('/', '\').ToLowerInvariant()
  $p = (Get-Full $Parent).Replace('/', '\').ToLowerInvariant()
  return (($c -eq $p) -or $c.StartsWith($p + '\'))
}

function Stop-With([int]$Code, [string]$Msg) {
  Write-Host $Msg
  Write-Host ('install_comfy_cheq_pc: exit ' + $Code)
  exit $Code
}

function Invoke-Native {
  # Run a program, echo its output, keep its stdout lines in $script:Out, return its exit code.
  # stderr never becomes a terminating error (Windows PowerShell 5.1 does that under 'Stop').
  param([string]$Exe, [string[]]$ArgList, [switch]$Quiet)
  $script:Out = New-Object System.Collections.Generic.List[string]
  $prev = $ErrorActionPreference
  $ErrorActionPreference = 'Continue'
  $code = 0
  try {
    & $Exe @ArgList 2>&1 | ForEach-Object {
      if ($_ -is [System.Management.Automation.ErrorRecord]) {
        if ($null -ne $_.TargetObject) { $line = [string]$_.TargetObject } else { $line = $_.ToString() }   # a stderr line
      }
      else { $line = [string]$_; $script:Out.Add($line) }
      if (-not $Quiet) { Write-Host $line }
    }
    $code = $LASTEXITCODE
  } catch {
    Write-Host ('could not run ' + $Exe + ': ' + $_)
    $code = 127
  } finally {
    $ErrorActionPreference = $prev
  }
  if ($null -eq $code) { $code = 0 }
  return [int]$code
}

function Get-LastJson {
  for ($i = $script:Out.Count - 1; $i -ge 0; $i--) {
    $t = $script:Out[$i].Trim()
    if ($t.StartsWith('{')) { try { return ($t | ConvertFrom-Json) } catch { return $null } }
  }
  return $null
}

function Find-BasePython {
  # A Python for 'pip install --user uv' (as setup_pc.ps1 gets uv), never the factory venv's or any venv's.
  $cands = @(@('py', '-3.12'), @('py', '-3'), @('python'), @('python3'))
  foreach ($c in $cands) {
    if (-not (Get-Command $c[0] -ErrorAction SilentlyContinue)) { continue }
    $pre = @()
    if ($c.Count -gt 1) { $pre = @($c[1]) }
    $code = Invoke-Native $c[0] ($pre + @('-c', 'import sys;print(sys.executable);print(int(sys.prefix==sys.base_prefix))')) -Quiet
    if ($code -ne 0 -or $script:Out.Count -lt 2) { continue }
    $exe = $script:Out[$script:Out.Count - 2].Trim()
    $base = $script:Out[$script:Out.Count - 1].Trim()
    if ($base -ne '1') { continue }
    if ((Test-Under $exe $FactoryVenv) -or (Test-Under $exe $FactoryComfy)) { continue }
    return @{ Exe = $c[0]; Pre = $pre; Path = $exe }
  }
  return $null
}

Write-Host ('CheqUp ComfyUI install: ' + $Tag + ' -> ' + $Dest + ' (port ' + $Port + '), models shared from ' + $Models)

# --- 0. never anywhere near shorts-factory's install ----------------------------------------------------------
$Dest = Get-Full $Dest
$Models = Get-Full $Models
if ((Test-Under $Dest $FactoryComfy) -or (Test-Under $FactoryComfy $Dest)) {
  Stop-With 3 ('Refused: -Dest ' + $Dest + " overlaps shorts-factory's ComfyUI (" + $FactoryComfy + '). Nothing was changed.')
}
if ((Test-Under $Dest $FactoryVenv) -or (Test-Under $FactoryVenv $Dest)) {
  Stop-With 3 ('Refused: -Dest ' + $Dest + " overlaps shorts-factory's venv (" + $FactoryVenv + '). Nothing was changed.')
}
if ((Test-Under $Dest $FactoryDir) -or (Test-Under $FactoryDir $Dest)) {
  Stop-With 3 ('Refused: -Dest ' + $Dest + " overlaps shorts-factory's folder (" + $FactoryDir + '). Nothing was changed.')
}
if ((Test-Under $Dest $Models) -or (Test-Under $Models $Dest)) {
  Stop-With 3 ('Refused: -Dest ' + $Dest + ' and the shared models folder ' + $Models + ' overlap. Nothing was changed.')
}
if ($Port -eq $FactoryPort) {
  Stop-With 3 ('Refused: port ' + $Port + " is shorts-factory's ComfyUI. Use another -Port (default 8288).")
}
if (-not (Test-Path -LiteralPath $Models -PathType Container)) {
  Stop-With 3 ('The shared models folder ' + $Models + ' does not exist (pass -Models <folder>). Nothing was changed.')
}
$running = $null
try { $running = Invoke-RestMethod -Uri ('http://127.0.0.1:' + $Port + '/system_stats') -Method Get -TimeoutSec 5 -UseBasicParsing } catch { $running = $null }
if ($null -ne $running) {
  $argv = (@($running.system.argv) -join ' ').ToLowerInvariant()
  if ($argv.Contains($Dest.ToLowerInvariant())) {
    Stop-With 3 ("CheqUp's ComfyUI is running from " + $Dest + ' (port ' + $Port + '): its files are in use. When no CheqUp run is going, stop it with' +
                 "`n  powershell -NoProfile -ExecutionPolicy Bypass -File scripts/comfy_cheq_stop.ps1`nthen run this again.")
  }
  Write-Host ('note: something else already answers on port ' + $Port + ' (' + [string]$running.system.comfyui_version + '): comfy_cheq_start.ps1 will refuse that port.')
}
# A new venv (~8 GB with uv's cache) lands on the disk shorts-factory renders to: never fill it.
$freeGB = $null
try { $freeGB = [math]::Floor((New-Object System.IO.DriveInfo([System.IO.Path]::GetPathRoot($Dest))).AvailableFreeSpace / 1GB) } catch { $freeGB = $null }
if ($null -ne $freeGB -and $freeGB -lt $MinFreeGB -and -not (Test-Path -LiteralPath (Join-Path (Join-Path $Dest '.venv') 'pyvenv.cfg'))) {
  Stop-With 3 ('Refused: only ' + $freeGB + ' GB free on ' + [System.IO.Path]::GetPathRoot($Dest) + ' (the install needs about 8 GB and must leave ' +
               $MinFreeGB + ' GB for shorts-factory). Free space first, or pass -MinFreeGB. Nothing was changed.')
}

# --- 1. ComfyUI at the tag -------------------------------------------------------------------------------------
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
  Stop-With 2 'git not found. Install it, then open a new PowerShell and run this again:  winget install -e --id Git.Git'
}
if (Test-Path -LiteralPath (Join-Path $Dest '.git')) {
  Write-Host ('Existing clone: fetching ' + $Tag)
  $c = Invoke-Native 'git' @('-C', $Dest, 'fetch', '--depth', '1', 'origin', ('refs/tags/' + $Tag + ':refs/tags/' + $Tag))
  if ($c -ne 0) { Write-Host ('git fetch exited ' + $c + ': trying the tag already in the clone') }
  $c = Invoke-Native 'git' @('-C', $Dest, '-c', 'advice.detachedHead=false', 'checkout', '-q', $Tag)
  if ($c -ne 0) { Stop-With 4 ('git checkout ' + $Tag + ' failed in ' + $Dest + ' (local edits? see above).') }
} else {
  if (Test-Path -LiteralPath $Dest) {
    if (@(Get-ChildItem -LiteralPath $Dest -Force -ErrorAction SilentlyContinue).Count -gt 0) {
      Stop-With 3 ($Dest + ' exists but is not a ComfyUI git clone: move it away or pass another -Dest. Nothing was changed.')
    }
  }
  Write-Host ('Cloning ' + $Repo + ' at ' + $Tag)
  $c = Invoke-Native 'git' @('-c', 'advice.detachedHead=false', 'clone', '--depth', '1', '--branch', $Tag, $Repo, $Dest)
  if ($c -ne 0) { Stop-With 4 ('git clone failed (exit ' + $c + ').') }
}
$verFile = Join-Path $Dest 'comfyui_version.py'
$ver = ''
if (Test-Path -LiteralPath $verFile) {
  $m = [regex]::Match((Get-Content -LiteralPath $verFile -Raw), '__version__\s*=\s*[''"]([^''"]+)')
  if ($m.Success) { $ver = $m.Groups[1].Value }
}
if ($ver -ne $Tag.TrimStart('v')) { Stop-With 4 ('After checkout ' + $Dest + ' reports version "' + $ver + '", not ' + $Tag + '.') }
Write-Host ('ok   ComfyUI ' + $ver + ' in ' + $Dest)

# --- 2. its own venv (uv, Python 3.12), CUDA torch, requirements -----------------------------------------------
$UvExe = $null
$UvPre = @()
$uvCmd = Get-Command uv -ErrorAction SilentlyContinue
if ($uvCmd) { $UvExe = $uvCmd.Source }
else {
  $bp = Find-BasePython
  if ($null -ne $bp) {
    Write-Host ('uv not on PATH: pip install --user uv with ' + $bp.Path)
    $null = Invoke-Native $bp.Exe ($bp.Pre + @('-m', 'pip', 'install', '--user', '--quiet', 'uv'))
    if ((Invoke-Native $bp.Exe ($bp.Pre + @('-m', 'uv', '--version'))) -eq 0) { $UvExe = $bp.Exe; $UvPre = $bp.Pre + @('-m', 'uv') }
  }
}
if (-not $UvExe) {
  Stop-With 2 "uv not found and no Python to install it with. Install it, open a new PowerShell, run this again:`n  winget install -e --id astral-sh.uv"
}
$Venv = Join-Path $Dest '.venv'
if ($OnWindows) { $VPy = Join-Path $Venv 'Scripts\python.exe' } else { $VPy = Join-Path (Join-Path $Venv 'bin') 'python' }
if (-not (Test-Path -LiteralPath $VPy)) {
  if (Test-Path -LiteralPath $Venv) { Remove-Item -LiteralPath $Venv -Recurse -Force }    # half-made; ours (inside -Dest)
  $c = Invoke-Native $UvExe ($UvPre + @('venv', '--seed', '-p', '3.12', $Venv))
  if ($c -ne 0 -or -not (Test-Path -LiteralPath $VPy)) { Stop-With 5 ('uv venv failed (exit ' + $c + ').') }
}
Write-Host ('ok   venv ' + $Venv)
if (-not $TorchIndex) {
  # ComfyUI v0.39.2 turns its optimized CUDA ops (comfy-kitchen: fp8/fp4) off under a CUDA < 13 torch and logs that cu130
  # is 'required' on RTX 20-series and newer (comfy/quant_ops.py). cu128 still renders, slower. So: cu130 when this
  # driver already supports CUDA 13, else cu128. Never update the NVIDIA driver for it while shorts-factory renders.
  # A re-run keeps the CUDA build already in the venv (only an explicit -TorchIndex switches it).
  $null = Invoke-Native $UvExe ($UvPre + @('pip', 'freeze', '--python', $VPy)) -Quiet
  $had = @($script:Out | Where-Object { $_ -match '^torch==\S+\+cu\d+$' })
  if ($had.Count -gt 0) {
    $TorchIndex = 'https://download.pytorch.org/whl/' + [regex]::Match($had[0], '\+(cu\d+)$').Groups[1].Value
    Write-Host ('Keeping the installed ' + $had[0] + ' (pass -TorchIndex to change the CUDA build)')
  } else {
    $TorchIndex = 'https://download.pytorch.org/whl/cu128'
    $drvCuda = 'unknown (no nvidia-smi)'
    if ((Invoke-Native 'nvidia-smi' @() -Quiet) -eq 0) {
      $m = [regex]::Match(($script:Out -join "`n"), 'CUDA Version:\s*(\d+)\.(\d+)')
      if ($m.Success) {
        $drvCuda = $m.Groups[1].Value + '.' + $m.Groups[2].Value
        if ([int]$m.Groups[1].Value -ge 13) { $TorchIndex = 'https://download.pytorch.org/whl/cu130' }
      }
    }
    Write-Host ('Driver supports CUDA ' + $drvCuda + ': torch from ' + $TorchIndex + ' (pass -TorchIndex to choose)')
  }
}
$TorchTag = ''
$mt = [regex]::Match($TorchIndex, '/(cu\d+)/*$')
if ($mt.Success) { $TorchTag = $mt.Groups[1].Value }
Write-Host ('Installing torch, torchvision from ' + $TorchIndex)
$c = Invoke-Native $UvExe ($UvPre + @('pip', 'install', '--python', $VPy, '--index-url', $TorchIndex, 'torch', 'torchvision'))
if ($c -ne 0) { Stop-With 5 ('torch install failed (exit ' + $c + ').') }
# Pin the CUDA builds just installed, so no requirement can swap them for a CPU torch from PyPI.
$null = Invoke-Native $UvExe ($UvPre + @('pip', 'freeze', '--python', $VPy)) -Quiet
$pins = @($script:Out | Where-Object { $_ -match '^(torch|torchvision)==' })
if ($TorchTag -and @($pins | Where-Object { $_ -match '^torch==' -and $_ -notmatch ('\+' + $TorchTag + '$') }).Count -gt 0) {
  # A re-run with another CUDA build (e.g. cu128 -> cu130): 'pip install torch' above keeps whatever is installed.
  Write-Host ('Installed ' + ($pins -join ', ') + ' is not the ' + $TorchTag + ' build: reinstalling torch, torchvision from ' + $TorchIndex)
  $c = Invoke-Native $UvExe ($UvPre + @('pip', 'install', '--python', $VPy, '--index-url', $TorchIndex, '--reinstall-package', 'torch',
                                       '--reinstall-package', 'torchvision', 'torch', 'torchvision'))
  if ($c -ne 0) { Stop-With 5 ('torch reinstall failed (exit ' + $c + ').') }
  $null = Invoke-Native $UvExe ($UvPre + @('pip', 'freeze', '--python', $VPy)) -Quiet
  $pins = @($script:Out | Where-Object { $_ -match '^(torch|torchvision)==' })
}
if ($pins.Count -lt 2) { Stop-With 5 ('torch and torchvision are not both installed: ' + ($pins -join ', ')) }
$Constraints = Join-Path $Venv 'cheq_torch_constraints.txt'
[System.IO.File]::WriteAllText($Constraints, (($pins -join "`n") + "`n"), (New-Object System.Text.UTF8Encoding($false)))
Write-Host ('ok   ' + ($pins -join ', '))
Write-Host 'Installing ComfyUI requirements.txt (torch pinned to the build above)'
$c = Invoke-Native $UvExe ($UvPre + @('pip', 'install', '--python', $VPy, '-r', (Join-Path $Dest 'requirements.txt'),
                                     '-c', $Constraints, '--index-url', $TorchIndex, '--extra-index-url', 'https://pypi.org/simple',
                                     '--index-strategy', 'unsafe-best-match'))
if ($c -ne 0) { Stop-With 5 ('requirements install failed (exit ' + $c + ').') }

# --- 3. shared model folders (read-only) -----------------------------------------------------------------------
$Yaml = Join-Path $Dest 'extra_model_paths.yaml'
$Helper = Join-Path $PSScriptRoot 'comfy_cheq_paths.py'
$c = Invoke-Native $VPy @($Helper, 'yaml', '--comfy', $Dest, '--models', $Models, '--out', $Yaml)
if ($c -ne 0) { Stop-With 7 ('writing ' + $Yaml + ' failed (exit ' + $c + ').') }
$c = Invoke-Native $VPy @($Helper, 'check', '--comfy', $Dest, '--models', $Models, '--yaml', $Yaml)
if ($c -ne 0) { Stop-With 7 ($Yaml + " failed ComfyUI's own loader check (see above).") }
Write-Host ('ok   ' + $Yaml + ' (every model folder type -> ' + $Models + ')')

# --- 4. summary ------------------------------------------------------------------------------------------------
$null = Invoke-Native $VPy @($Helper, 'summary', '--comfy', $Dest, '--models', $Models) -Quiet
$s = Get-LastJson
if ($null -eq $s) { Stop-With 5 ('summary printed no JSON: ' + ($script:Out -join ' ')) }
Write-Host ''
Write-Host ('CheqUp ComfyUI ' + $s.comfyui_version + ' at ' + $Dest + ' (port ' + $Port + ')')
if ($s.cuda_available) { Write-Host ('  torch ' + $s.torch + ', CUDA ' + $s.cuda + ': ' + $s.device) }
else { Write-Host ('  torch ' + $s.torch + ': NO CUDA GPU ' + $s.torch_error) }
Write-Host ('  shared models ' + $s.models + ': ' + (@($s.types_with_files).Count) + ' of ' + $s.folder_types + ' folder types have files (' +
            $s.model_files + ' files, ' + $s.model_gb + ' GB), read-only via extra_model_paths.yaml')
$factoryYaml = [System.IO.Path]::Combine($FactoryComfy, 'extra_model_paths.yaml')
$hasFactoryYaml = $false
try { $hasFactoryYaml = Test-Path -LiteralPath $factoryYaml -PathType Leaf } catch { $hasFactoryYaml = $false }
if ($hasFactoryYaml) {      # read-only look: CheqUp shares -Models only, not the factory's other model folders
  Write-Host ("  note: shorts-factory's ComfyUI also reads models listed in " + $factoryYaml + '; CheqUp sees only ' + $Models +
              '. If a model CheqUp needs is only there, cqf doctor / pc_run report it missing.')
}
$startCmd = 'powershell -NoProfile -ExecutionPolicy Bypass -File scripts/comfy_cheq_start.ps1'
if (($Dest -ne 'C:\Users\white\ComfyUI-CheqUp') -or ($Port -ne 8288)) { $startCmd += (' -Dest "' + $Dest + '" -Port ' + $Port) }
Write-Host ('  start: ' + $startCmd)
if (-not $s.cuda_available) {
  Stop-With 6 ('torch in the new venv sees no CUDA GPU: CheqUp cannot render on it. Do not update or reinstall the NVIDIA driver while ' +
               "shorts-factory renders (that resets the GPU under it). If torch is a cu130 build, re-run with`n" +
               '  -TorchIndex https://download.pytorch.org/whl/cu128')
}
Write-Host 'install_comfy_cheq_pc: exit 0'
exit 0
