# NOT FOR CHEQUP. This updates the SHARED ComfyUI that shorts-factory renders with (C:\Users\white\ComfyUI-Installs,
# venv C:\Users\white\ComfyUI-Factory-venv): it changes that install's code and packages and needs a restart, which
# interrupts shorts-factory. CheqUp has its own side-by-side ComfyUI v0.39.2 instead:
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts/install_comfy_cheq_pc.ps1
# Kept only for Dan, if he ever decides to update shorts-factory's install himself; it refuses to run without
# -ConfirmSharedInstall. Never run it from pc_run, PUPPET.md or a CheqUp message.
#
# Update the PC's ComfyUI to a version with the SeedVR2 / FrameInterpolate nodes (>= v0.39.2).
# shorts-factory uses the same ComfyUI: pause it first (data\STOP) and test one of its renders afterwards.
param([string]$Comfy = "C:\Users\white\ComfyUI-Installs\ComfyUI\ComfyUI", [string]$Py = "C:\Users\white\ComfyUI-Factory-venv\Scripts\python.exe", [string]$Tag = "v0.39.2",
      [switch]$ConfirmSharedInstall)
$ErrorActionPreference = "Stop"
if (-not $ConfirmSharedInstall) {
  Write-Host "Not run: this updates shorts-factory's shared ComfyUI (and interrupts it). For CheqUp use:"
  Write-Host "  powershell -NoProfile -ExecutionPolicy Bypass -File scripts/install_comfy_cheq_pc.ps1"
  Write-Host "Only Dan updates the shared install, with -ConfirmSharedInstall."
  exit 3
}
Push-Location $Comfy
git fetch --tags
git checkout $Tag
& $Py -m pip install -r requirements.txt
Pop-Location
Write-Host "ComfyUI is now $Tag. Restart it (close its window / restart the factory's ComfyUI), optionally with --disable-metadata."
