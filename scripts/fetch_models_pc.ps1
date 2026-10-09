# Download the b-roll quality models into the 5090 PC's shared ComfyUI models folder (resumable; safe to re-run).
# CheqUp's own ComfyUI (C:\Users\white\ComfyUI-CheqUp) reads this same folder via its extra_model_paths.yaml.
#   powershell -ExecutionPolicy Bypass -File .\scripts\fetch_models_pc.ps1            # core (~47 GB) + ACE-Step music (9.3 GB, if missing)
#   powershell -ExecutionPolicy Bypass -File .\scripts\fetch_models_pc.ps1 -ABTest    # + A/B extras (~37 GB)
# All files are Apache-2.0 (commercial use of outputs OK) — see reference/broll-plan.md.
# The folder is shorts-factory's and it renders from it: this never overwrites, appends to or deletes a file that is
# already there (complete or not). New files download to <name>.cqpart (resumable) and are renamed into place only
# when complete and the name is still free. It refuses to start if the downloads would leave less than -MinFreeGB.
param([string]$Comfy = "C:\Users\white\ComfyUI-Installs\ComfyUI\ComfyUI", [string]$Models = "C:\Users\white\ComfyUI-Shared\models", [switch]$ABTest, [int]$MinFreeGB = 30)
$ErrorActionPreference = "Stop"
# The PC's models live in the shared folder (C:\Users\white\ComfyUI-Shared\models, which the factory's :8188 loads from);
# fall back to <Comfy>\models only if that folder doesn't exist.
$m = $Models
if (-not (Test-Path $m)) { $m = Join-Path $Comfy "models" }
if (-not (Test-Path $m)) { throw "ComfyUI models folder not found: $Models or $m (pass -Models <path>)" }
$ramGB = [math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB)
$freeGB = [math]::Round((Get-PSDrive ($m.Substring(0,1))).Free / 1GB)
Write-Host "RAM: $ramGB GB   free disk: $freeGB GB"
$hf = "https://huggingface.co"
$files = @(
  @("$hf/Comfy-Org/Qwen-Image_ComfyUI/resolve/main/split_files/diffusion_models/qwen_image_2512_fp8_e4m3fn.safetensors", "diffusion_models"),
  @("$hf/Comfy-Org/Qwen-Image_ComfyUI/resolve/main/split_files/text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors", "text_encoders"),
  @("$hf/Comfy-Org/Qwen-Image_ComfyUI/resolve/main/split_files/vae/qwen_image_vae.safetensors", "vae"),
  @("$hf/Comfy-Org/SeedVR2/resolve/main/vae/seedvr2_ema_vae_fp16.safetensors", "vae"),
  @("$hf/Comfy-Org/frame_interpolation/resolve/main/frame_interpolation/film_net_fp16.safetensors", "frame_interpolation")
)
# SeedVR2 7B: fp16 (16.5 GB) needs >= 64 GB system RAM alongside Qwen; otherwise the fp8 file (8.2 GB)
if ($ramGB -ge 64) { $files += ,@("$hf/Comfy-Org/SeedVR2/resolve/main/diffusion_models/seedvr2_7b_fp16.safetensors", "diffusion_models") }
else {
  $files += ,@("$hf/Comfy-Org/SeedVR2/resolve/main/diffusion_models/seedvr2_7b_fp8_e4m3fn.safetensors", "diffusion_models")
  Write-Host "Under 64 GB RAM: using SeedVR2 fp8. Set models.seedvr2.unet: seedvr2_7b_fp8_e4m3fn.safetensors in config.pc.yaml" -ForegroundColor Yellow
}
if ($ABTest) {
  $files += ,@("$hf/Comfy-Org/SeedVR2/resolve/main/diffusion_models/seedvr2_7b_sharp_fp16.safetensors", "diffusion_models")
  $files += ,@("$hf/Comfy-Org/z_image/resolve/main/split_files/diffusion_models/z_image_bf16.safetensors", "diffusion_models")
  $files += ,@("$hf/Comfy-Org/z_image/resolve/main/split_files/text_encoders/qwen_3_4b.safetensors", "text_encoders")
  $files += ,@("$hf/Comfy-Org/z_image/resolve/main/split_files/vae/ae.safetensors", "vae")
}
# Music: ACE-Step 1.5 turbo all-in-one checkpoint (model + text encoder/planner + VAE, 9.3 GB; upstream ACE-Step 1.5
# is MIT) for cqf/graphs/ace_step_bed.json (music.backend: ace_step). Only fetched if it isn't complete already.
$ace = Join-Path $m "checkpoints\ace_step_1.5_turbo_aio.safetensors"
if ((Test-Path $ace) -and ((Get-Item $ace).Length -eq 10025478736)) { Write-Host "ACE-Step 1.5 checkpoint already present: $ace" }
else { $files += ,@("$hf/Comfy-Org/ace_step_1.5_ComfyUI_files/resolve/main/checkpoints/ace_step_1.5_turbo_aio.safetensors", "checkpoints") }
function Get-RemoteSize([string]$Url) {
  # Upstream size in bytes (Hugging Face's x-linked-size, else a 200 response's Content-Length), or $null.
  $h = @()
  try { $h = @(& curl.exe -sIL --max-time 60 $Url) } catch { $h = @() }
  $size = $null
  $status = 0
  foreach ($l in $h) {
    $t = [string]$l
    if ($t -match '^HTTP/\S+\s+(\d{3})') { $status = [int]$Matches[1] }
    elseif ($t -match '^\s*x-linked-size:\s*(\d+)') { return [int64]$Matches[1] }
    elseif ($status -eq 200 -and $t -match '^\s*content-length:\s*(\d+)') { $size = [int64]$Matches[1] }
  }
  return $size
}
$todo = @(); $mismatch = @(); $unchecked = @(); $needBytes = [int64]0
foreach ($f in $files) {
  $out = Join-Path (Join-Path $m $f[1]) ([IO.Path]::GetFileName($f[0]))
  $size = Get-RemoteSize $f[0]
  if (Test-Path -LiteralPath $out) {                 # shorts-factory's (or already ours): never touched
    $have = [int64](Get-Item -LiteralPath $out).Length
    if ($null -eq $size) { $unchecked += $out }
    elseif ($have -eq $size) { Write-Host "present: $out" }
    else { $mismatch += ($out + ' (' + $have + ' bytes here, ' + $size + ' upstream)') }
    continue
  }
  $part = $out + '.cqpart'
  $done = [int64]0
  if (Test-Path -LiteralPath $part) { $done = [int64](Get-Item -LiteralPath $part).Length }
  if ($null -ne $size -and $size -gt $done) { $needBytes += ($size - $done) }
  $todo += ,@($f[0], $out, $part, $size)
}
$freeB = [int64](Get-PSDrive ($m.Substring(0,1))).Free
if ($todo.Count -gt 0 -and ($freeB - $needBytes) -lt ([int64]$MinFreeGB * 1GB)) {
  throw ("Not started: " + [math]::Ceiling($needBytes / 1GB) + " GB to download would leave less than " + $MinFreeGB +
         " GB free on the drive shorts-factory renders to (" + [math]::Round($freeB / 1GB) + " GB free now). Free space first, or pass -MinFreeGB.")
}
foreach ($t in $todo) {
  New-Item -ItemType Directory -Force (Split-Path -Parent $t[1]) | Out-Null
  Write-Host "-> $($t[1])"
  & curl.exe -L --fail --retry 5 --retry-delay 5 -C - -o $t[2] $t[0]
  if ($LASTEXITCODE -ne 0) { throw "download failed: $($t[0]) (the partial file $($t[2]) resumes on the next run)" }
  $got = [int64](Get-Item -LiteralPath $t[2]).Length
  if ($null -ne $t[3] -and $got -ne $t[3]) { throw "$($t[2]) is $got bytes, upstream $($t[3]): delete that .cqpart file and run again" }
  if (Test-Path -LiteralPath $t[1]) { Write-Host "  $($t[1]) appeared meanwhile: left alone (download kept as $($t[2]))"; continue }
  Move-Item -LiteralPath $t[2] -Destination $t[1]      # no -Force: never replaces a file
}
foreach ($u in $unchecked) { Write-Host "present (size not checked: upstream did not answer): $u" }
if ($mismatch.Count -gt 0) {
  Write-Host "NOT TOUCHED: these files already exist in the shared models folder but differ from upstream. shorts-factory may use them, so" -ForegroundColor Yellow
  Write-Host "this script never changes them. If one is a half-finished CheqUp download that nothing else uses, rename it to" -ForegroundColor Yellow
  Write-Host "<name>.cqpart and run this again to resume it; otherwise ask Dan." -ForegroundColor Yellow
  foreach ($x in $mismatch) { Write-Host "  $x" -ForegroundColor Yellow }
}
Write-Host "done. If CheqUp's ComfyUI doesn't list the new files, restart it (never shorts-factory's): scripts/comfy_cheq_stop.ps1 then scripts/comfy_cheq_start.ps1. Check: .venv\Scripts\python.exe -m cqf --config config.pc.yaml doctor"
if ($mismatch.Count -gt 0) { exit 1 }
