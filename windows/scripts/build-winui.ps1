# Build the native mirror window (WinUI 3) into windows\dist, where `phoneuse up` finds it.
#   powershell -ExecutionPolicy Bypass -File windows\scripts\build-winui.ps1
# Needs the .NET SDK (10 or later). Without windows\dist\PhoneUseMirror.exe, phoneuse
# falls back to the browser mirror in an Edge/Chrome app window.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$Proj = Join-Path $Root "windows\PhoneUseMirror\PhoneUseMirror.csproj"
$Dist = Join-Path $Root "windows\dist"
$arch = if ($env:PROCESSOR_ARCHITECTURE -eq "ARM64") { "ARM64" } else { "x64" }

Get-Process PhoneUseMirror -ErrorAction SilentlyContinue | Stop-Process  # the exe is locked while it runs
# The trailing slash on -o keeps the XAML .xbf files inside dist (without it they land next to it).
# The same version as phoneuse (pyproject.toml), as package.ps1 gives it: About shows it
$version = (Select-String -Path "$Root\pyproject.toml" -Pattern '^version\s*=\s*"([^"]+)"').Matches[0].Groups[1].Value
dotnet build $Proj -c Release -p:Platform=$arch -p:Version=$version -o "$Dist/" -nologo -v q
if ($LASTEXITCODE -ne 0) { throw "the mirror window didn't build" }
Write-Host "==> Built $Dist\PhoneUseMirror.exe"
