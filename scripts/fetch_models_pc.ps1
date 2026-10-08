# Download the b-roll quality models into the 5090 PC's ComfyUI (resumable; safe to re-run).
#   powershell -ExecutionPolicy Bypass -File .\scripts\fetch_models_pc.ps1            # core (~47 GB) + ACE-Step music (9.3 GB, if missing)
#   powershell -ExecutionPolicy Bypass -File .\scripts\fetch_models_pc.ps1 -ABTest    # + A/B extras (~37 GB)
# All files are Apache-2.0 (commercial use of outputs OK) — see reference/broll-plan.md.
param([string]$Comfy = "C:\Users\white\ComfyUI-Installs\ComfyUI\ComfyUI", [switch]$ABTest)
$ErrorActionPreference = "Stop"
$m = Join-Path $Comfy "models"
if (-not (Test-Path $m)) { throw "ComfyUI models folder not found: $m (pass -Comfy <path>)" }
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
foreach ($f in $files) {
  $dir = Join-Path $m $f[1]; New-Item -ItemType Directory -Force $dir | Out-Null
  $out = Join-Path $dir ([IO.Path]::GetFileName($f[0]))
  Write-Host "-> $out"
  & curl.exe -L --fail --retry 5 --retry-delay 5 -C - -o $out $f[0]
  if ($LASTEXITCODE -ne 0) { throw "download failed: $($f[0])" }
}
Write-Host "done. Restart ComfyUI so it sees the new files, then: .venv\Scripts\python.exe -m cqf --config config.pc.yaml doctor"
