#!/usr/bin/env python3
import os
import sys
import shutil
import tarfile
import subprocess
from pathlib import Path

def build_deb_package(project_root: Path, dist_dir: Path, app_dir: Path) -> bool:
    """
    Build native Debian/Ubuntu package (.deb) using dpkg-deb if available.
    """
    dpkg_deb = shutil.which("dpkg-deb")
    if not dpkg_deb:
        print("Tip: 'dpkg-deb' tool not found. Install dpkg to generate '.deb' installer package.")
        return False

    print("[4/4] Building Debian package (Llampaca-Installer-amd64.deb)...")
    deb_build_dir = project_root / "build" / "deb_package"
    if deb_build_dir.exists():
        shutil.rmtree(deb_build_dir, ignore_errors=True)

    # 1. Create directory structure
    opt_dir = deb_build_dir / "opt" / "llampaca"
    bin_dir = deb_build_dir / "usr" / "bin"
    apps_dir = deb_build_dir / "usr" / "share" / "applications"
    icons_dir = deb_build_dir / "usr" / "share" / "icons" / "hicolor" / "256x256" / "apps"
    debian_dir = deb_build_dir / "DEBIAN"

    for d in [opt_dir, bin_dir, apps_dir, icons_dir, debian_dir]:
        d.mkdir(parents=True, exist_ok=True)

    # 2. Copy PyInstaller bundle into /opt/llampaca
    shutil.copytree(app_dir, opt_dir, dirs_exist_ok=True)

    # 3. Create /usr/bin/llampaca launcher script
    launcher_file = bin_dir / "llampaca"
    launcher_file.write_text("#!/bin/sh\nexec /opt/llampaca/Llampaca \"$@\"\n", encoding="utf-8")
    launcher_file.chmod(0o755)

    # 4. Copy app icon
    logo_png = project_root / "llampaca" / "gui" / "logo.png"
    if logo_png.exists():
        shutil.copy(logo_png, icons_dir / "llampaca.png")

    # 5. Create Desktop entry (.desktop)
    desktop_content = """[Desktop Entry]
Name=Llampaca
Comment=Llampaca Local LLM Agent & Interface
Exec=/opt/llampaca/Llampaca %U
Icon=llampaca
Terminal=false
Type=Application
Categories=Utility;Development;AI;
StartupWMClass=llampaca
"""
    (apps_dir / "llampaca.desktop").write_text(desktop_content, encoding="utf-8")

    # 6. Create DEBIAN/control
    control_content = """Package: llampaca
Version: 0.1.0
Section: utils
Priority: optional
Architecture: amd64
Maintainer: Llampaca Team <info@llampaca.app>
Description: Llampaca Local LLM Agent & Desktop GUI
 Privacy-focused local AI workstation powered by llama.cpp.
"""
    (debian_dir / "control").write_text(control_content, encoding="utf-8")

    # 7. Run dpkg-deb
    final_deb = dist_dir / "Llampaca-Installer-amd64.deb"
    if final_deb.exists():
        final_deb.unlink()

    try:
        subprocess.run(["dpkg-deb", "--build", str(deb_build_dir), str(final_deb)], check=True)
        print(f"-> Successfully created Debian package: {final_deb}")
        return True
    except Exception as e:
        print(f"Error building .deb package: {e}", file=sys.stderr)
        return False
    finally:
        if deb_build_dir.exists():
            shutil.rmtree(deb_build_dir, ignore_errors=True)

def main():
    print("=" * 60)
    print("   Llampaca Linux Application & Package Builder")
    print("=" * 60)

    if not sys.platform.startswith("linux"):
        print("Warning: Running build_linux.py on non-Linux OS. PyInstaller step requires Linux environment.", file=sys.stderr)

    project_root = Path(__file__).resolve().parent
    venv_py = project_root / ".venv" / "bin" / "python"
    venv_pip = project_root / ".venv" / "bin" / "pip"
    venv_pyinstaller = project_root / ".venv" / "bin" / "pyinstaller"

    python_exec = str(venv_py) if venv_py.exists() else sys.executable
    pyinstaller_exec = str(venv_pyinstaller) if venv_pyinstaller.exists() else "pyinstaller"

    # 1. Check PyInstaller
    print("[1/4] Checking build dependencies...")
    try:
        subprocess.run([python_exec, "-m", "PyInstaller", "--version"], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pip_exec = str(venv_pip) if venv_pip.exists() else "pip"
        print("Installing pyinstaller...")
        subprocess.run([pip_exec, "install", "pyinstaller"], check=True)

    # 2. Run PyInstaller
    print("[2/4] Building Linux App binary package...")
    spec_file = project_root / "Llampaca.spec"
    subprocess.run([pyinstaller_exec, str(spec_file), "--noconfirm", "--clean"], check=True)

    dist_dir = project_root / "dist"
    app_dir = dist_dir / "Llampaca"
    if not app_dir.exists():
        print(f"Error: Build failed, {app_dir} was not created.", file=sys.stderr)
        sys.exit(1)

    # 3. Create portable .tar.gz archive
    print("[3/4] Creating portable tarball archive (Llampaca-Linux-x64.tar.gz)...")
    tarball_path = dist_dir / "Llampaca-Linux-x64.tar.gz"
    with tarfile.open(tarball_path, "w:gz") as tar:
        tar.add(app_dir, arcname="Llampaca")
    print(f"-> Portable tarball created: {tarball_path}")

    # 4. Build Debian package (.deb)
    build_deb_package(project_root, dist_dir, app_dir)

    print("\n" + "=" * 60)
    print("   Linux Build Finished Successfully!")
    print("   Outputs in:", dist_dir)
    print("=" * 60)

if __name__ == "__main__":
    main()
