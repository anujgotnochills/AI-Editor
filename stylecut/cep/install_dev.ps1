# install_dev.ps1  —  StyleCut CEP Dev Install
# ==============================================
# Symlinks the extension into Premiere's CEP folder and sets the
# PlayerDebugMode registry flag for unsigned extensions.
#
# Does NOT require Administrator:
#   - HKCU registry writes are always per-user (no elevation needed)
#   - Directory junctions (mklink /J) work without SeCreateSymbolicLinkPrivilege
#
# Run as: powershell -ExecutionPolicy Bypass -File install_dev.ps1
#
# Tested on: Windows 10/11, Premiere Pro 2022–2025

$ErrorActionPreference = "Stop"

# ── Paths ────────────────────────────────────────────────────────────────────
$ScriptDir   = Split-Path -Parent $MyInvocation.MyCommand.Path
$PanelSrc    = Join-Path $ScriptDir "com.stylecut.panel"

# Adobe CEP extension folder (per-user, no admin needed for the folder itself)
$CepUserDir  = "$env:APPDATA\Adobe\CEP\extensions"
$PanelDest   = Join-Path $CepUserDir "com.stylecut.panel"

# ── 1. Create CEP extensions folder if it doesn't exist ──────────────────────
if (-not (Test-Path $CepUserDir)) {
    Write-Host "Creating CEP extensions folder: $CepUserDir"
    New-Item -ItemType Directory -Force -Path $CepUserDir | Out-Null
}

# ── 2. Remove old symlink/folder if present ───────────────────────────────────
if (Test-Path $PanelDest) {
    Write-Host "Removing existing install at: $PanelDest"
    cmd /c "rmdir `"$PanelDest`"" | Out-Null
    if (Test-Path $PanelDest) {
        Remove-Item -Path $PanelDest -Recurse -Force
    }
}

# ── 3. Create directory junction (symlink alternative, no admin for junction) ─
Write-Host "Creating junction: $PanelDest  →  $PanelSrc"
# cmd /c mklink /J works without SeCreateSymbolicLinkPrivilege
cmd /c "mklink /J `"$PanelDest`" `"$PanelSrc`"" | Out-Null

if (-not (Test-Path $PanelDest)) {
    Write-Error "Failed to create junction. Try running as Administrator."
    exit 1
}
Write-Host "✅ Panel linked."

# ── 4. Set PlayerDebugMode registry flag ─────────────────────────────────────
# This allows unsigned/unsigned-debug CEP extensions to load.
# The flag lives under HKCU so no elevation is needed.
#
# CEP version matrix:
#   Premiere 22.x  →  CSXS.11
#   Premiere 23.x  →  CSXS.11
#   Premiere 24.x  →  CSXS.11
#   Premiere 25.x  →  CSXS.11  (still CSXS 11 as of 2025)
#
# If Adobe ships CEP 12, add a CSXS.12 entry here.

$CsxsVersions = @("CSXS.12", "CSXS.11", "CSXS.10", "CSXS.9")
foreach ($ver in $CsxsVersions) {
    $regPath = "HKCU:\Software\Adobe\$ver"
    if (-not (Test-Path $regPath)) {
        New-Item -Path $regPath -Force | Out-Null
    }
    Set-ItemProperty -Path $regPath -Name "PlayerDebugMode" -Value "1" -Type String
    Write-Host "✅ Set PlayerDebugMode=1 under $regPath"
}

# ── 5. Done ───────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
Write-Host "  StyleCut dev install complete."
Write-Host ""
Write-Host "  Next steps:"
Write-Host "  1. Restart Adobe Premiere Pro (if already open)."
Write-Host "  2. Go to  Window > Extensions > StyleCut"
Write-Host "  3. The panel should appear. Click 'Read Sequence Info'"
Write-Host "     with a sequence open to verify the ExtendScript bridge."
Write-Host ""
Write-Host "  Panel folder : $PanelDest"
Write-Host "  Source folder: $PanelSrc"
Write-Host ""
Write-Host "  To open the CEP DevTools (Chrome Inspector):"
Write-Host "  → Right-click inside the panel → 'Inspect Element'"
Write-Host "    (only works while PlayerDebugMode=1)"
Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
