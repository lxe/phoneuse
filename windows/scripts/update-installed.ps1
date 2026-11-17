# Put a change to the window (windows\PhoneUseMirror) on the installed Phone Use in seconds, instead of
# packaging and installing it again (package.ps1 takes minutes). Builds the window as package.ps1 does,
# copies what changed over the install, and starts it again. core\ (phoneuse itself, its Python, UxPlay)
# is left alone, and phoneuse keeps running, so agents keep the phone meanwhile.
#
#   powershell -ExecutionPolicy Bypass -File windows\scripts\update-installed.ps1 [-App <folder>] [-NoStart]
#
# -App: where Phone Use is installed (default: the installer's, %LOCALAPPDATA%\Programs\Phone Use).
# A change to phoneuse (Python) or to what the installer sets up needs package.ps1 and the installer.
param(
    [string]$App = (Join-Path $env:LOCALAPPDATA "Programs\Phone Use"),
    [switch]$NoStart
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. "$PSScriptRoot\common.ps1"
if (-not (Test-Path "$App\PhoneUseMirror.exe")) { throw "no Phone Use installed in $App (-App names another folder)" }

# As package.ps1 builds it (self-contained, x64, English resources), with the same version
$base = (Select-String -Path "$Root\pyproject.toml" -Pattern '^version\s*=\s*"([^"]+)"').Matches[0].Groups[1].Value
$Out = Join-Path $Root "windows\build\window"
Write-Step "Building the mirror window"
# The trailing slash on -o keeps the XAML .xbf files inside the folder
dotnet build "$Root\windows\PhoneUseMirror\PhoneUseMirror.csproj" -c Release -r win-x64 -p:Platform=x64 `
    -p:SatelliteResourceLanguages=en -p:DebugType=none -p:Version="$base.0" -o "$Out/" -nologo -v q
if ($LASTEXITCODE -ne 0) { throw "the mirror window didn't build" }

# The running window locks its files
$running = Get-Process PhoneUseMirror -ErrorAction SilentlyContinue | Where-Object { $_.Path -like "$App\*" }
if ($running) {
    $running | Stop-Process -Force
    $running | Wait-Process -Timeout 10 -ErrorAction SilentlyContinue
}

Write-Step "Copying it over $App"
# Only what changed (size or time); nothing in the install is deleted
robocopy $Out $App /E /NFL /NDL /NJH /NJS /NP | Out-Null
if ($LASTEXITCODE -ge 8) { throw "couldn't copy the window into $App (robocopy $LASTEXITCODE)" }

if (-not $NoStart) { Start-Process "$App\PhoneUseMirror.exe" }
Write-Step "Updated $App"
exit 0  # robocopy's 1-7 mean success
