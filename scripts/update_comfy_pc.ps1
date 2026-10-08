# Update the PC's ComfyUI to a version with the SeedVR2 / FrameInterpolate nodes (>= v0.39.2).
# shorts-factory uses the same ComfyUI: pause it first (data\STOP) and test one of its renders afterwards.
param([string]$Comfy = "C:\Users\white\ComfyUI-Installs\ComfyUI\ComfyUI", [string]$Py = "C:\Users\white\ComfyUI-Factory-venv\Scripts\python.exe", [string]$Tag = "v0.39.2")
$ErrorActionPreference = "Stop"
Push-Location $Comfy
git fetch --tags
git checkout $Tag
& $Py -m pip install -r requirements.txt
Pop-Location
Write-Host "ComfyUI is now $Tag. Restart it (close its window / restart the factory's ComfyUI), optionally with --disable-metadata."
