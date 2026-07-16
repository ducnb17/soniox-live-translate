# build.ps1 — one-command Windows build of the installer.
#
# Prerequisites on the build machine (or GitHub Actions windows-latest):
#   - Python 3.13+ (added to PATH)
#   - Inno Setup 6 (ISCC.exe on PATH, default install path checked)
#   - Repo checked out, cwd = repo root
#
# Usage:
#   pwsh packaging/build.ps1
#
# Output:
#   dist/                          (PyInstaller onedir)
#   dist/SonioxLiveTranslate-Setup-<version>.exe   (Inno Setup installer)

$ErrorActionPreference = "Stop"

$root = Resolve-Path "$PSScriptRoot/.."
Set-Location $root

# --- 1. Python deps -----------------------------------------------------------
Write-Host "==> Installing runtime + packaging deps"
python -m pip install --upgrade pip
python -m pip install -r backend/requirements.txt
python -m pip install pyinstaller pystray pillow

# --- 1b. Frontend build (Vite + TS) -------------------------------------------
Write-Host "==> Building frontend (Vite + TS)"
$corepack = Get-Command corepack -ErrorAction SilentlyContinue
if ($corepack) {
    corepack enable
    corepack prepare pnpm@latest --install
} else {
    $pnpm = Get-Command pnpm -ErrorAction SilentlyContinue
    if (-not $pnpm) { throw "pnpm not found. Install Node.js + run: npm i -g pnpm" }
}
Push-Location frontend
pnpm install --frozen-lockfile
pnpm build
Pop-Location
if (-not (Test-Path "frontend/dist/index.html")) { throw "Vite build did not produce frontend/dist/index.html" }

# --- 2. PyInstaller onedir ----------------------------------------------------
Write-Host "==> PyInstaller build"
$env:PYTHONPATH = "$root\backend;$root"
pyinstaller packaging/spec.spec --noconfirm --distpath dist --workpath build

if (-not (Test-Path "dist/SonioxLiveTranslate/SonioxLiveTranslate.exe")) {
    throw "PyInstaller output not found: dist/SonioxLiveTranslate/SonioxLiveTranslate.exe"
}

# --- 3. Inno Setup installer --------------------------------------------------
Write-Host "==> Inno Setup installer"
$iscc = $null
foreach ($candidate in @(
    "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    "C:\Program Files\Inno Setup 6\ISCC.exe"
)) {
    if (Test-Path $candidate) { $iscc = $candidate; break }
}
if (-not $iscc) { $iscc = Get-Command ISCC.exe -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source }
if (-not $iscc) { throw "Inno Setup 6 (ISCC.exe) not found. Install from https://jrsoftware.org/isdl.php" }

$version = (Get-Content "$root/backend/pyproject.toml" | Select-String 'version = "(.*)"').Matches.Groups[1].Value
Write-Host "Version: $version"

& $iscc /Q "/DAPP_VERSION=$version" "$root/packaging/installer.iss"
if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed with exit code $LASTEXITCODE" }

$installer = "dist/SonioxLiveTranslate-Setup-$version.exe"
if (-not (Test-Path $installer)) { throw "Installer not produced: $installer" }
Write-Host ""
Write-Host "SUCCESS: $installer"
