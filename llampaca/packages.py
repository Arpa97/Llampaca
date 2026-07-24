"""
Package Installer: Isolated, tracked installation of Python (venv) and Node.js (local ~/.llampaca/node_modules) packages.
"""

import os
import sys
import json
import re
import subprocess
from pathlib import Path
from typing import Dict, Any, List

from llampaca.config import LLAMPACA_DIR

INSTALLED_PACKAGES_FILE = LLAMPACA_DIR / "installed_packages.json"
NODE_DIR = LLAMPACA_DIR / "node_modules"

_SAFE_PKG_NAME = re.compile(r"^[a-zA-Z0-9_\-\.@\/]+$")


def _ensure_package_log_file() -> None:
    LLAMPACA_DIR.mkdir(parents=True, exist_ok=True)
    if not INSTALLED_PACKAGES_FILE.exists():
        initial = {"python": [], "node": []}
        INSTALLED_PACKAGES_FILE.write_text(json.dumps(initial, indent=2), encoding="utf-8")


def log_installation(ecosystem: str, package_name: str) -> None:
    """Record an installed package in ~/.llampaca/installed_packages.json."""
    _ensure_package_log_file()
    try:
        data = json.loads(INSTALLED_PACKAGES_FILE.read_text(encoding="utf-8"))
    except Exception:
        data = {"python": [], "node": []}

    eco_list = data.setdefault(ecosystem, [])
    if package_name not in eco_list:
        eco_list.append(package_name)
    
    INSTALLED_PACKAGES_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def list_installed_packages() -> Dict[str, List[str]]:
    """Return dict of tracked python and node packages."""
    _ensure_package_log_file()
    try:
        return json.loads(INSTALLED_PACKAGES_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"python": [], "node": []}


def install_python_package(package_name: str) -> str:
    """
    Install a Python package strictly inside the active venv/python environment.
    """
    pkg = package_name.strip()
    if not pkg or not _SAFE_PKG_NAME.match(pkg):
        raise ValueError(f"Nome del pacchetto Python non valido: '{package_name}'")

    cmd = [sys.executable, "-m", "pip", "install", pkg]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)

    if res.returncode != 0:
        err_msg = res.stderr.strip() or res.stdout.strip()
        raise RuntimeError(f"Impossibile installare il pacchetto Python '{pkg}': {err_msg}")

    log_installation("python", pkg)
    return f"Pacchetto Python '{pkg}' installato con successo nel venv ({sys.executable})."


def install_node_package(package_name: str) -> str:
    """
    Install a Node.js package locally inside ~/.llampaca/node_modules/ without system pollution.
    """
    pkg = package_name.strip()
    if not pkg or not _SAFE_PKG_NAME.match(pkg):
        raise ValueError(f"Nome del pacchetto Node non valido: '{package_name}'")

    LLAMPACA_DIR.mkdir(parents=True, exist_ok=True)
    pkg_json = LLAMPACA_DIR / "package.json"
    if not pkg_json.exists():
        pkg_json.write_text('{"name": "llampaca-local-modules", "private": true}', encoding="utf-8")

    cmd = ["npm", "install", "--prefix", str(LLAMPACA_DIR), pkg]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=180)

    if res.returncode != 0:
        err_msg = res.stderr.strip() or res.stdout.strip()
        raise RuntimeError(f"Impossibile installare il pacchetto Node '{pkg}': {err_msg}")

    log_installation("node", pkg)
    return f"Pacchetto Node '{pkg}' installato con successo in {LLAMPACA_DIR / 'node_modules'}."
