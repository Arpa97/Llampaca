import os
import sys
import shutil
import subprocess
from pathlib import Path
from typing import Optional, List, Dict, Any

from llampaca.config import BIN_DIR, MODELS_DIR, IMAGE_PRESETS, resolve_user_output_path
from llampaca.engine.downloader import download_sd_binary


def resolve_sd_binary() -> Optional[Path]:
    """Finds the stable-diffusion.cpp (sd / sd-cli) binary executable."""
    candidate_names = ["sd-cli.exe", "sd.exe", "sd-cli", "sd"] if sys.platform == "win32" else ["sd-cli", "sd"]
    for b_name in candidate_names:
        local_path = BIN_DIR / b_name
        if local_path.exists() and (sys.platform == "win32" or os.access(local_path, os.X_OK)):
            return local_path

    for b_name in candidate_names:
        system_sd = shutil.which(b_name)
        if system_sd:
            return Path(system_sd)

    return None


def get_installed_image_models() -> List[Dict[str, Any]]:
    """Returns a list of installed image generation GGUF models in MODELS_DIR."""
    installed = []
    MODELS_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Check known presets
    for preset_id, info in IMAGE_PRESETS.items():
        filename = info.get("file")
        if filename:
            file_path = MODELS_DIR / filename
            if file_path.exists():
                installed.append({
                    "id": preset_id,
                    "name": filename,
                    "preset_id": preset_id,
                    "path": str(file_path),
                    "description": info.get("description", ""),
                    "default_steps": info.get("default_steps", 4),
                    "quality_preset": info.get("quality_preset", "fast")
                })

    # 2. Check for other local GGUF models with image-related keywords
    for file in MODELS_DIR.glob("*.gguf"):
        fname_lower = file.name.lower()
        if any(kw in fname_lower for kw in ["sdxl", "sd1", "sd2", "sd3", "flux", "stable-diffusion", "diffusion"]):
            if not any(i["name"] == file.name for i in installed):
                installed.append({
                    "id": file.stem,
                    "name": file.name,
                    "preset_id": None,
                    "path": str(file),
                    "description": f"Modello immagine locale {file.name}",
                    "default_steps": 4,
                    "quality_preset": "custom"
                })

    return installed


def generate_image_file(
    prompt: str,
    output_path: Optional[Path] = None,
    model_name_or_path: Optional[str] = None,
    steps: Optional[int] = None,
    width: int = 512,
    height: int = 512,
    seed: int = -1
) -> Dict[str, Any]:
    """
    Generates an image from prompt using stable-diffusion.cpp (sd) binary.
    Runs on-demand and frees memory immediately after generation.
    """
    # 1. Ensure binary exists
    sd_bin = resolve_sd_binary()
    if not sd_bin:
        print("[ImageEngine] Binario 'sd' non trovato. Avvio download automatico...")
        if not download_sd_binary():
            raise RuntimeError("Impossibile trovare o scaricare il binario 'sd' per stable-diffusion.cpp.")
        sd_bin = resolve_sd_binary()

    # 2. Resolve model file
    installed_models = get_installed_image_models()
    selected_model_path = None
    default_steps = 4

    if model_name_or_path:
        # Check direct path
        p = Path(model_name_or_path).expanduser().resolve()
        if p.exists() and p.is_file():
            selected_model_path = p
        else:
            # Check presets or filenames
            for m in installed_models:
                if m["id"] == model_name_or_path or m["name"] == model_name_or_path or m["preset_id"] == model_name_or_path:
                    selected_model_path = Path(m["path"])
                    default_steps = m.get("default_steps", 4)
                    break
    
    if not selected_model_path and installed_models:
        selected_model_path = Path(installed_models[0]["path"])
        default_steps = installed_models[0].get("default_steps", 4)

    if not selected_model_path or not selected_model_path.exists():
        raise FileNotFoundError("Nessun modello per la generazione di immagini trovato.")

    final_steps = steps if steps is not None and steps > 0 else default_steps

    # 3. Resolve destination path
    if output_path is None:
        filename = f"image_{int(os.times().elapsed * 1000)}.png"
        output_path = resolve_user_output_path(filename, subfolder="Images")
    else:
        output_path = Path(output_path).expanduser().resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)

    # 4. Build command line
    cmd = [
        str(sd_bin),
        "-m", str(selected_model_path),
        "-p", prompt,
        "-o", str(output_path),
        "--steps", str(final_steps),
        "-W", str(width),
        "-H", str(height)
    ]
    if seed >= 0:
        cmd.extend(["--seed", str(seed)])

    print(f"[ImageEngine] Generazione immagine in corso: '{prompt[:40]}...'")
    print(f"[ImageEngine] Esecuzione: {' '.join(cmd)}")

    startupinfo = None
    if sys.platform == "win32":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

    res = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        startupinfo=startupinfo
    )

    if res.returncode != 0:
        print(f"[ImageEngine] Errore durante la generazione: {res.stdout}")
        raise RuntimeError(f"Generazione immagine fallita (exit code {res.returncode}): {res.stdout[-300:]}")

    if not output_path.exists():
        raise FileNotFoundError(f"Il file di output {output_path} non è stato generato.")

    print(f"[ImageEngine] Immagine salvata con successo in: {output_path}")

    return {
        "output_path": str(output_path),
        "prompt": prompt,
        "model": selected_model_path.name,
        "steps": final_steps,
        "width": width,
        "height": height
    }
