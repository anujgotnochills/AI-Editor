# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for StyleCut Engine (engine.exe)."""

from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

repo_root = Path.cwd()

datas = [
    (str(repo_root / "config.yaml"), "."),
]

# Collect any data files needed by faster-whisper or ctranslate2
try:
    datas += collect_data_files("faster_whisper")
    datas += collect_data_files("ctranslate2")
except Exception:
    pass

hiddenimports = [
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    "fastapi",
    "pydantic",
    "src.server.app",
    "src.server.jobs",
    "src.server.models",
    "src.captions",
    "src.transcribe",
    "src.hinglish",
    "src.cutter",
    "src.ingest",
    "src.project",
    "src.utils",
    "src.llm.base",
    "src.llm.gemini",
    "src.llm.ollama",
]

a = Analysis(
    ['engine.py'],
    pathex=[str(repo_root)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='engine',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)
