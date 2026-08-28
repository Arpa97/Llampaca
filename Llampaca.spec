# -*- mode: python ; coding: utf-8 -*-

import os
import sys
from pathlib import Path

block_cipher = None

project_root = Path(os.getcwd()).resolve()

datas = []
gui_dir = project_root / "llampaca" / "gui"
if gui_dir.exists():
    datas.append((str(gui_dir), "llampaca/gui"))

skills_dir = project_root / "llampaca" / "skills"
if skills_dir.exists():
    datas.append((str(skills_dir), "llampaca/skills"))

try:
    import certifi
    datas.append((certifi.where(), "certifi"))
except Exception:
    pass

hidden_imports = [
    "uvicorn",
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.lifespan.on",
    "uvicorn.lifespan.off",
    "fastapi",
    "starlette",
    "starlette.routing",
    "pydantic",
    "webview",
    "PIL",
    "sqlite3",
    "click",
    "httpx",
    "jinja2",
    "asyncio",
    "multiprocessing",
]

a = Analysis(
    ['llampaca_entry.py'],
    pathex=[str(project_root)],
    binaries=[],
    datas=datas,
    hiddenimports=hidden_imports,
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

exe_icon = None
if sys.platform == "win32":
    win_icon = project_root / "build" / "Llampaca.ico"
    if win_icon.exists():
        exe_icon = str(win_icon)
elif sys.platform.startswith("linux"):
    linux_icon = project_root / "llampaca" / "gui" / "logo.png"
    if linux_icon.exists():
        exe_icon = str(linux_icon)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Llampaca',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=True if sys.platform == "darwin" else False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=exe_icon,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Llampaca',
)

if sys.platform == 'darwin':
    icon_path = str(project_root / "build" / "Llampaca.icns")
    if not os.path.exists(icon_path):
        icon_path = None

    app = BUNDLE(
        coll,
        name='Llampaca.app',
        icon=icon_path,
        bundle_identifier='com.llampaca.app',
        info_plist={
            'CFBundleName': 'Llampaca',
            'CFBundleDisplayName': 'Llampaca',
            'CFBundleExecutable': 'Llampaca',
            'CFBundlePackageType': 'APPL',
            'CFBundleShortVersionString': '0.1.0',
            'CFBundleVersion': '0.1.0',
            'NSHighResolutionCapable': 'True',
            'LSMinimumSystemVersion': '10.15.0',
        },
    )
