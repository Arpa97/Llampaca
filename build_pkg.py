#!/usr/bin/env python3
import os
import sys
import subprocess
from pathlib import Path

def main():
    print("=" * 60)
    print("   Llampaca macOS Native Installer Wizard (.pkg) Builder")
    print("=" * 60)

    if sys.platform != "darwin":
        print("Error: build_pkg.py must be run on macOS.", file=sys.stderr)
        sys.exit(1)

    project_root = Path(__file__).resolve().parent
    venv_py = project_root / ".venv" / "bin" / "python"
    python_exec = str(venv_py) if venv_py.exists() else sys.executable

    # 1. Build Llampaca.app first
    print("[1/3] Ensuring Llampaca.app bundle is built...")
    build_mac_script = project_root / "build_mac.py"
    subprocess.run([python_exec, str(build_mac_script)], check=True)

    app_bundle = project_root / "dist" / "Llampaca.app"
    if not app_bundle.exists():
        print(f"Error: {app_bundle} missing.", file=sys.stderr)
        sys.exit(1)

    scripts_dir = project_root / "scripts" / "mac_pkg"
    postinstall = scripts_dir / "postinstall"
    if not postinstall.exists():
        print(f"Error: {postinstall} missing.", file=sys.stderr)
        sys.exit(1)

    build_dir = project_root / "build"
    build_dir.mkdir(parents=True, exist_ok=True)
    component_pkg = build_dir / "LlampacaComponent.pkg"
    final_pkg = project_root / "dist" / "Llampaca-Installer.pkg"

    if component_pkg.exists():
        component_pkg.unlink()
    if final_pkg.exists():
        final_pkg.unlink()

    # 2. Build component package with pkgbuild
    print("[2/3] Building component package with pkgbuild (including postinstall script)...")
    pkgbuild_cmd = [
        "pkgbuild",
        "--root", str(app_bundle),
        "--install-location", "/Applications/Llampaca.app",
        "--scripts", str(scripts_dir),
        "--identifier", "com.llampaca.app.pkg",
        "--version", "0.1.0",
        str(component_pkg)
    ]
    subprocess.run(pkgbuild_cmd, check=True)

    # 3. Build final installer distribution with productbuild
    print("[3/3] Building final macOS installer distribution with productbuild...")
    productbuild_cmd = [
        "productbuild",
        "--package", str(component_pkg),
        str(final_pkg)
    ]
    subprocess.run(productbuild_cmd, check=True)

    print("\n" + "=" * 60)
    print(" INSTALLER BUILD SUCCESSFUL!")
    print("=" * 60)
    print(f" macOS App Bundle:       {app_bundle}")
    print(f" macOS PKG Installer:    {final_pkg}")
    print("=" * 60 + "\n")

if __name__ == "__main__":
    main()
