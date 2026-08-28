#!/usr/bin/env python3
import os
import sys
import shutil
import subprocess
from pathlib import Path

def generate_win_icon(png_path: Path, ico_output: Path) -> bool:
    """
    Generate Windows .ico file from logo.png using Pillow (PIL).
    """
    ico_output.parent.mkdir(parents=True, exist_ok=True)
    try:
        from PIL import Image
        img = Image.open(png_path)
        img.save(ico_output, format="ICO", sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
        print(f"-> Successfully generated Windows ICO icon at {ico_output}")
        return True
    except Exception as e:
        print(f"Warning: Could not convert icon with Pillow ({e}).")
        return False

def build_inno_setup(project_root: Path, dist_dir: Path, app_dir: Path) -> bool:
    """
    Generate Inno Setup script (.iss) and compile it into Llampaca-Setup-x64.exe if ISCC is installed.
    """
    iss_content = f"""[Setup]
AppName=Llampaca
AppVersion=0.1.0
DefaultDirName={{autopf}}\\Llampaca
DefaultGroupName=Llampaca
UninstallDisplayIcon={{app}}\\Llampaca.exe
Compression=lzma2
SolidCompression=yes
OutputDir={dist_dir.resolve()}
OutputBaseFilename=Llampaca-Setup-x64
SetupIconFile={(project_root / 'build' / 'Llampaca.ico').resolve()}

[Tasks]
Name: "desktopicon"; Description: "{{cm:CreateDesktopIcon}}"; GroupDescription: "{{cm:AdditionalIcons}}"

[Files]
Source: "{app_dir.resolve()}\\*"; DestDir: "{{app}}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{{group}}\\Llampaca"; Filename: "{{app}}\\Llampaca.exe"
Name: "{{autodesktop}}\\Llampaca"; Filename: "{{app}}\\Llampaca.exe"; Tasks: desktopicon

[Run]
Filename: "{{app}}\\Llampaca.exe"; Description: "{{cm:LaunchProgram,Llampaca}}"; Flags: nowait postinstall skipifsilent
"""
    iss_file = project_root / "build" / "LlampacaSetup.iss"
    iss_file.parent.mkdir(parents=True, exist_ok=True)
    iss_file.write_text(iss_content, encoding="utf-8")
    print(f"-> Inno Setup script created at {iss_file}")

    # Search for Inno Setup compiler (ISCC.exe)
    iscc_path = shutil.which("iscc") or shutil.which("ISCC.exe")
    if not iscc_path:
        default_paths = [
            Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"),
            Path(r"C:\Program Files\Inno Setup 6\ISCC.exe")
        ]
        for p in default_paths:
            if p.exists():
                iscc_path = str(p)
                break

    if iscc_path:
        print(f"[4/4] Compiling Windows Installer executable via Inno Setup ({iscc_path})...")
        try:
            subprocess.run([iscc_path, str(iss_file)], check=True)
            setup_exe = dist_dir / "Llampaca-Setup-x64.exe"
            print(f"-> Successfully created Windows Setup Installer: {setup_exe}")
            return True
        except Exception as e:
            print(f"Warning: Inno Setup compilation failed: {e}", file=sys.stderr)
            return False
    else:
        print("Tip: Install Inno Setup (https://jrsoftware.org/isdl.php) to automatically generate 'Llampaca-Setup-x64.exe'.")
        return False

def main():
    print("=" * 60)
    print("   Llampaca Windows Application & Installer Builder")
    print("=" * 60)

    if sys.platform != "win32":
        print("Error: build_win.py must be run on Windows.", file=sys.stderr)
        sys.exit(1)

    project_root = Path(__file__).resolve().parent
    venv_py = project_root / ".venv" / "Scripts" / "python.exe"
    venv_pip = project_root / ".venv" / "Scripts" / "pip.exe"
    venv_pyinstaller = project_root / ".venv" / "Scripts" / "pyinstaller.exe"

    python_exec = str(venv_py) if venv_py.exists() else sys.executable
    pyinstaller_exec = str(venv_pyinstaller) if venv_pyinstaller.exists() else "pyinstaller"

    # 1. Check/Install PyInstaller and Pillow
    print("[1/4] Checking build dependencies...")
    try:
        subprocess.run([python_exec, "-c", "import PyInstaller, PIL"], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pip_exec = str(venv_pip) if venv_pip.exists() else "pip"
        print("Installing pyinstaller and pillow...")
        subprocess.run([pip_exec, "install", "pyinstaller", "pillow"], check=True)

    # 2. Generate Windows ICO icon
    print("[2/4] Generating Windows icon (Llampaca.ico)...")
    png_icon = project_root / "llampaca" / "gui" / "logo.png"
    out_ico = project_root / "build" / "Llampaca.ico"
    generate_win_icon(png_icon, out_ico)

    # 3. Run PyInstaller
    print("[3/4] Building Windows App bundle...")
    spec_file = project_root / "Llampaca.spec"
    subprocess.run([pyinstaller_exec, str(spec_file), "--noconfirm", "--clean"], check=True)

    dist_dir = project_root / "dist"
    app_dir = dist_dir / "Llampaca"
    if not app_dir.exists():
        print(f"Error: Build failed, {app_dir} was not created.", file=sys.stderr)
        sys.exit(1)

    # Create standalone ZIP distribution
    zip_path = dist_dir / "Llampaca-Windows-x64.zip"
    print(f"Creating portable ZIP package ({zip_path.name})...")
    shutil.make_archive(str(zip_path).replace(".zip", ""), 'zip', root_dir=dist_dir, base_dir="Llampaca")
    print(f"-> Portable ZIP created: {zip_path}")

    # 4. Generate Inno Setup installer if available
    build_inno_setup(project_root, dist_dir, app_dir)

    print("\n" + "=" * 60)
    print("   Windows Build Finished Successfully!")
    print("   Outputs in:", dist_dir)
    print("=" * 60)

if __name__ == "__main__":
    main()
