#!/usr/bin/env python3
import os
import sys
import shutil
import subprocess
from pathlib import Path

def main():
    print("=" * 60)
    print("   Llampaca macOS Application & DMG Installer Builder")
    print("=" * 60)

    if sys.platform != "darwin":
        print("Error: build_mac.py must be run on macOS.", file=sys.stderr)
        sys.exit(1)

    project_root = Path(__file__).resolve().parent
    venv_py = project_root / ".venv" / "bin" / "python"
    venv_pip = project_root / ".venv" / "bin" / "pip"
    venv_pyinstaller = project_root / ".venv" / "bin" / "pyinstaller"

    python_exec = str(venv_py) if venv_py.exists() else sys.executable
    pyinstaller_exec = str(venv_pyinstaller) if venv_pyinstaller.exists() else "pyinstaller"

    # 1. Ensure pyinstaller is installed
    try:
        subprocess.run([python_exec, "-m", "PyInstaller", "--version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    except Exception:
        print("[1/4] Installing PyInstaller in virtual environment...")
        pip_exec = str(venv_pip) if venv_pip.exists() else "pip"
        subprocess.run([pip_exec, "install", "pyinstaller"], check=True)

    # 2. Generate macOS .icns icon
    print("[2/4] Generating macOS icon (Llampaca.icns)...")
    create_icns_script = project_root / "scripts" / "create_icns.py"
    subprocess.run([python_exec, str(create_icns_script)], check=True)

    # 3. Run PyInstaller
    print("[3/4] Building macOS App bundle (Llampaca.app)...")
    spec_file = project_root / "Llampaca.spec"
    pyinstaller_bin = project_root / ".venv" / "bin" / "pyinstaller"
    pyinstaller_exec = str(pyinstaller_bin) if pyinstaller_bin.exists() else "pyinstaller"
    subprocess.run([pyinstaller_exec, str(spec_file), "--noconfirm", "--clean"], check=True)

    app_bundle = project_root / "dist" / "Llampaca.app"
    if not app_bundle.exists():
        print(f"Error: Build failed, {app_bundle} was not created.", file=sys.stderr)
        sys.exit(1)

    print(f"-> Successfully built: {app_bundle}")

    # 4. Generate macOS .dmg installer image using native hdiutil
    print("[4/4] Generating macOS installer image (Llampaca-macOS.dmg)...")
    dmg_file = project_root / "dist" / "Llampaca-macOS.dmg"
    if dmg_file.exists():
        dmg_file.unlink()

    hdiutil_cmd = [
        "hdiutil", "create",
        "-volname", "Llampaca",
        "-srcfolder", str(app_bundle),
        "-ov",
        "-format", "UDZO",
        str(dmg_file)
    ]
    subprocess.run(hdiutil_cmd, check=True)

    print("\n" + "=" * 60)
    print(" BUILD SUCCESSFUL!")
    print("=" * 60)
    print(f" macOS App Bundle: {app_bundle}")
    print(f" macOS DMG Installer: {dmg_file}")
    print("=" * 60 + "\n")

if __name__ == "__main__":
    main()
