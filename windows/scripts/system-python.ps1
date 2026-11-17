# The installer's "use the Python already on this PC": make core\.venv from a compatible system
# Python and install phoneuse's packages into it from PyPI, pinned to what the app was built and
# tested with (requirements-lock.txt). Exits 0 when core\.venv is ready; otherwise it removes
# whatever it made and exits non-zero, and the installer installs the bundled Python instead.
#   powershell -ExecutionPolicy Bypass -File system-python.ps1 -Core <app>\core -Lock requirements-lock.txt
param([Parameter(Mandatory = $true)][string]$Core, [Parameter(Mandatory = $true)][string]$Lock)
$ErrorActionPreference = "Stop"
$Root = $null  # Find-Python must not pick the bundled Python: this is about the system's
. "$PSScriptRoot\common.ps1"
$Venv = Join-Path $Core ".venv"
$Log = Join-Path $env:TEMP "phoneuse-system-python.log"
Start-Transcript -Path $Log -Force | Out-Null

function Fail($why) {
    Write-Host "not using the system Python: $why"
    if (Test-Path $Venv) { Remove-Item -Recurse -Force $Venv -ErrorAction SilentlyContinue }
    Stop-Transcript | Out-Null
    exit 1
}

try { $Py = Find-Python } catch { Fail $_.Exception.Message }
# The lock holds win_amd64 wheels (PyAV, numpy, the WinRT bindings): it needs python.org's
# CPython (or the Store's), not MSYS2's or Cygwin's, and a version those wheels are built for.
$ErrorActionPreference = "Continue"
$check = & $Py -c @"
import sys, sysconfig
ok = sysconfig.get_platform() == 'win-amd64' and sys.implementation.name == 'cpython' and 'GCC' not in sys.version and (3, 11) <= sys.version_info[:2] <= (3, 14)
print('ok' if ok else 'no: ' + sys.version.split()[0] + ' ' + sysconfig.get_platform())
"@ 2>$null
if ("$check".Trim() -ne "ok") { Fail "$Py isn't a compatible CPython ($check)" }
Write-Host "using $Py"

& $Py -m venv $Venv
if ($LASTEXITCODE -ne 0) { Fail "couldn't make a venv with $Py" }
$VPy = Join-Path $Venv "Scripts\python.exe"
& $VPy -m pip install --quiet --disable-pip-version-check --only-binary=:all: -r $Lock
if ($LASTEXITCODE -ne 0) {
    # The lock pins versions for the bundled Python; an older or newer one may need others.
    $deps = Get-Content $Lock | Where-Object { $_ -match '^\s*[A-Za-z]' } | ForEach-Object { ($_ -split '==')[0] }
    & $VPy -m pip install --quiet --disable-pip-version-check --only-binary=:all: @deps
    if ($LASTEXITCODE -ne 0) { Fail "pip couldn't install phoneuse's packages" }
}
& $VPy -c "import av, numpy, PIL, aiohttp, psutil, winrt.windows.devices.bluetooth"
if ($LASTEXITCODE -ne 0) { Fail "the packages installed but don't import" }
Write-Host "core\.venv is ready"
Stop-Transcript | Out-Null
exit 0
