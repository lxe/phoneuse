# Local screen reading for `phoneuse text` on Windows: RapidOCR (PP-OCRv6 small, ONNX) in its own
# venv (.venv-vision), the same packages as scripts/setup-vision.sh. With an NVIDIA GPU it runs on
# CUDA 13 through onnxruntime-gpu, whose CUDA and cuDNN DLLs come as pip packages ([cuda,cudnn]):
# about 2 GB, against about 250 MB for the CPU. Nothing is kept in pip's cache (--no-cache-dir): the
# packages are all there is to remove.
#   powershell -ExecutionPolicy Bypass -File windows\scripts\setup-vision.ps1
# PHONEUSE_PROGRESS=raw (the window's Settings > Screen reading sets it): pip-progress.py reports each
# download, "Downloading <name> (<size>)" then "Progress <bytes> of <total>" lines (pip's --progress-bar
# raw), and the unpacking after it the same way.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. "$PSScriptRoot\common.ps1"
$Raw = $env:PHONEUSE_PROGRESS -eq "raw"

$Py = Find-Python
$Venv = Join-Path $Root ".venv-vision"
if (-not (Test-Path "$Venv\Scripts\python.exe")) {
    Write-Step "Creating $Venv"
    & $Py -m venv $Venv
}
$VPy = "$Venv\Scripts\python.exe"
& $VPy -m pip install --quiet --no-cache-dir --upgrade pip

$gpu = $null -ne (Get-Command nvidia-smi -ErrorAction SilentlyContinue)
$ort = if ($gpu) { "onnxruntime-gpu[cuda,cudnn]" } else { "onnxruntime" }
$packages = "rapidocr", $ort, "pillow", "numpy"
Write-Step ("Installing OCR" + $(if ($gpu) { " for the GPU (about 2 GB)" } else { " (about 250 MB)" }))
if ($Raw) {
    # Its own lines as they come (stderr too, as text: Windows PowerShell would make it an error)
    $ErrorActionPreference = "Continue"
    & $VPy -I "$PSScriptRoot\pip-progress.py" --no-cache-dir @packages 2>&1 | ForEach-Object { "$_" }
    $ErrorActionPreference = "Stop"
} else {
    & $VPy -m pip install --quiet --no-cache-dir @packages
}
if ($LASTEXITCODE -ne 0) { throw "the OCR packages failed to install" }
Write-Host "screen reading ready" -ForegroundColor Green
