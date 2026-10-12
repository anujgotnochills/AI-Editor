# StyleCut CEP Panel — Developer Setup Guide

## Overview

This document covers **Step 1: Feasibility Spike** — loading an unsigned CEP panel
in Adobe Premiere Pro for development, verifying the ExtendScript bridge, and
understanding exactly what the Premiere scripting API can and cannot do.

---

## Files Produced (Step 1)

```
stylecut/cep/
├── install_dev.ps1                   ← one-shot dev installer
└── com.stylecut.panel/
    ├── CSXS/manifest.xml             ← CEP manifest (extension ID, host range)
    ├── index.html                    ← Panel UI entry point
    ├── jsx/host.jsx                  ← All ExtendScript / Premiere DOM calls
    ├── js/
    │   ├── panel.js                  ← Panel controller (CSInterface calls)
    │   └── lib/CSInterface.js        ← Adobe's JS bridge library (bundled)
    ├── css/panel.css                 ← Dark Premiere-themed styles
    ├── icons/icon_32.png             ← Panel icon (placeholder — add your own)
    └── demo_captions.srt             ← Hand-crafted SRT for the spike test
```

---

## Prerequisites

| Requirement | Version |
|---|---|
| Adobe Premiere Pro | 2022 (v22.x) or later |
| Windows | 10 or 11 |
| PowerShell | 5.1+ (ships with Windows) |
| Admin rights | Only needed if junction creation fails |

> **Tested Premiere versions:** 22.x, 23.x, 24.x. The panel targets CEP 11.1
> which ships with all of these. CEP 12 / Premiere 26+ may need a CSXS.12 key.

---

## Step-by-Step: Load the Panel for Development

### 1 — Run the installer script

Open **PowerShell** (no admin required in most cases):

```powershell
cd "D:\Personal Projects\ai editor\stylecut\cep"
powershell -ExecutionPolicy Bypass -File install_dev.ps1
```

What it does:
1. Creates (if needed): `%APPDATA%\Adobe\CEP\extensions\`
2. Creates a **directory junction** (not a symlink — no elevation needed):
   `%APPDATA%\Adobe\CEP\extensions\com.stylecut.panel` -> your source folder
3. Sets `PlayerDebugMode = 1` in the registry under:
   - `HKCU\Software\Adobe\CSXS.11`
   - `HKCU\Software\Adobe\CSXS.10` (fallback)

> **Why a junction?** Directory junctions work without `SeCreateSymbolicLinkPrivilege`.
> Every file edit in your source folder is instantly reflected in Premiere — no copy step needed.

### 2 — Set the registry flag manually (alternative)

If you prefer `regedit`:

```
HKEY_CURRENT_USER\Software\Adobe\CSXS.11
  PlayerDebugMode  REG_SZ  1
```

You can also do it from PowerShell:
```powershell
Set-ItemProperty "HKCU:\Software\Adobe\CSXS.11" -Name "PlayerDebugMode" -Value "1"
```

> **Security note:** `PlayerDebugMode=1` allows any unsigned extension to load.
> Remove or set to `0` when distributing the signed `.zxp`.

### 3 — Verify the CEP extensions folder

After running the script, confirm the junction exists:

```powershell
Get-Item "$env:APPDATA\Adobe\CEP\extensions\com.stylecut.panel"
```

The `Mode` column should show `d-----l` (the `l` = junction/symlink).

### 4 — Open Premiere Pro

- If Premiere was already running, **restart it completely**. The extension list is read at startup.
- Go to **Window - Extensions - StyleCut**
- The panel should dock.

### 5 — Test the panel

1. Open (or create) any sequence in Premiere.
2. Click **"Read Sequence Info"** the log area should show the sequence name and Premiere version.
3. Select a language (English / Hinglish) and click **"Import Demo SRT - Timeline"**.
4. Check the timeline — a new video track should appear with the SRT clip on it.
5. Double-click the clip to open Premiere's **Captions panel** for editing.

---

## Opening Chrome DevTools (CEP Inspector)

While `PlayerDebugMode=1` is set:

1. **Right-click inside the StyleCut panel** choose **"Inspect Element"** (if available)
2. Or navigate Chrome to: `http://localhost:7777` (Premiere's default CEF debug port)
3. The DevTools console lets you call engine functions directly:

```js
window._SC.evalScript("SC_getVersion").then(console.log)
window._SC.pingEngine()
```

---

## What ExtendScript CAN and CANNOT Do

### What works

| Feature | API | Notes |
|---|---|---|
| Read active sequence name/ID | `app.project.activeSequence.name` | Reliable across all versions |
| Walk project items for file paths | `rootItem.children[i].getMediaPath()` | Works for master clips |
| Import files into project | `app.project.importFiles([path])` | Triggers import dialog on some builds |
| Add video track | `seq.videoTracks.addTrack()` | Reliable |
| Place clip on video track | `track.clips.add(item, tick)` | Works but tick math is tricky |
| Create caption track (22.6+) | `seq.createCaptionTrack(type, time)` | New API tested on 22.6, 23.x |
| Import captions into track (23.0+) | `track.importCaptionFile(path)` | Only v23+; falls back gracefully |

### What is NOT possible via ExtendScript / CEP

| Feature | Limitation |
|---|---|
| **Export audio** from a sequence | No ExtendScript API. Engine must call ffmpeg on the media file path. |
| **Read sequence audio waveform** | Not exposed. Rely on the media file directly. |
| **UXP access** from CEP panel | CEP and UXP are separate runtimes. panel.js is structured to be runtime-agnostic for a future UXP port. |
| **Native Captions track in PP < 22.6** | `seq.createCaptionTrack()` does not exist. Falls back to placing the SRT on video track 2. |
| **`importCaptionFile` in PP < 23.0** | Not available; imports the SRT as a project item instead. |
| **Modify individual caption entries** | Cannot iterate over caption entries inside a caption track via ExtendScript. |
| **Multi-sequence batch processing** | ExtendScript can switch active sequence but is flaky with undo history. Engine handles batch work. |
| **Progress bar in Premiere's title bar** | No public API. We use the panel's own status box. |

---

## Registry Keys Reference

```
HKCU\Software\Adobe\CSXS.11
  PlayerDebugMode   REG_SZ  "1"    enables unsigned extensions
  LogLevel          REG_SZ  "6"    verbose CEP logging (0=off, 6=all)
```

CEP log location: `%APPDATA%\Adobe\CEP\logs\`

---

## Premiere Version vs CEP Version Matrix

| Premiere Version | Year | CEP Version | CSXS Registry Key |
|---|---|---|---|
| 22.x | 2022 | CEP 11 | `CSXS.11` |
| 23.x | 2023 | CEP 11 | `CSXS.11` |
| 24.x | 2024 | CEP 11 | `CSXS.11` |
| 25.x | 2025 | CEP 11 | `CSXS.11` |
| 26.x | 2026 | CEP 11/12* | `CSXS.11` / `CSXS.12`* |

*CEP 12 not yet released as of Oct 2026. Add `CSXS.12` entry if needed.

---

## Uninstall / Cleanup

```powershell
Remove-Item "$env:APPDATA\Adobe\CEP\extensions\com.stylecut.panel" -Force
Remove-ItemProperty "HKCU:\Software\Adobe\CSXS.11" -Name "PlayerDebugMode"
```

---

## Next Step

**Step 2 - Engine Server**: FastAPI on 127.0.0.1:7820 exposing `/jobs/captions`,
`/jobs/:id/progress`, `/jobs/:id/result`. The panel's **"Ping Engine"** button will
then return a real version response instead of the current "offline" message.

The panel architecture is already prepared: `panel.js` has `pingEngine()` and
`ENGINE_BASE` wired up. Just start the server.
