# Build OrbitBar into dist\OrbitBar\OrbitBar.exe (a folder build: starts fast, and the crash watchdog stays tiny).
#
#   .\build.ps1            normal build (no console window)
#   .\build.ps1 -Console   debug build: shows a console with any error message
#
# Needs: py -m pip install -r requirements.txt pyinstaller
param([switch]$Console)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

# File properties (right-click OrbitBar.exe > Properties > Details): version and publisher from orbitbar.py
$src = Get-Content orbitbar.py -Raw
$ver = [regex]::Match($src, '(?m)^APP_VERSION = "(.+?)"').Groups[1].Value
$pub = [regex]::Match($src, '(?m)^APP_PUBLISHER = "(.+?)"').Groups[1].Value
$n = @($ver.Split(".") | ForEach-Object { [int]$_ }) + @(0, 0, 0, 0)
$t = "($($n[0]), $($n[1]), $($n[2]), 0)"
@"
VSVersionInfo(
  ffi=FixedFileInfo(filevers=$t, prodvers=$t, mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', '$pub'),
      StringStruct('FileDescription', 'OrbitBar - a modern taskbar for Windows'),
      StringStruct('FileVersion', '$ver'),
      StringStruct('InternalName', 'OrbitBar'),
      StringStruct('LegalCopyright', 'Copyright (c) 2026 $pub. MIT license.'),
      StringStruct('OriginalFilename', 'OrbitBar.exe'),
      StringStruct('ProductName', 'OrbitBar'),
      StringStruct('ProductVersion', '$ver')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"@ | Set-Content -Encoding utf8 version_info.txt

$mode = if ($Console) { "--console" } else { "--windowed" }
pyinstaller --noconfirm --clean $mode --onedir --name OrbitBar --icon orbitbar.ico --version-file version_info.txt `
    --collect-all winrt `
    --collect-data certifi `
    --collect-data tzdata `
    --hidden-import pycaw.pycaw `
    --hidden-import comtypes.stream `
    orbitbar.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

Copy-Item README.md, LICENSE, CHANGELOG.md, test-calendar.ics dist\OrbitBar\
Write-Host "`nBuilt: dist\OrbitBar\OrbitBar.exe" -ForegroundColor Green
