import os
import time
from pathlib import Path
from typing import Optional, Dict, Any, List

from llampaca.config import resolve_user_output_path, IMAGE_PRESETS
from llampaca.engine.image_generator import get_installed_image_models, generate_image_file


def generate_image(prompt: str, output_directory: Optional[str] = None, quality: str = "fast") -> str:
    """
    Generates a new image from a text prompt using a local Stable Diffusion GGUF model. Call this tool IMMEDIATELY whenever the user asks to generate, draw, or create an image or visual.

    Args:
        prompt: Detailed description of the image to generate in English or Italian.
        output_directory: Optional custom folder path where to save the image (e.g. ~/Desktop or ./output). If omitted, defaults to ~/Documents/LlampacaDocs/Images/.
        quality: Image quality/speed preset: 'fast' (1-2 steps) or 'high' (4+ steps).

    Returns:
        String describing the result, file location URL, or onboarding message if no model is installed.
    """
    installed_models = get_installed_image_models()

    # 1. Onboarding logic: if no image generation model is installed, explain and suggest presets
    if not installed_models:
        return (
            "Posso generare questa immagine per te! Tuttavia, sembra che non ci sia ancora un modello per la generazione di immagini installato in Llampaca.\n\n"
            "Puoi scaricare facilmente un modello usando il comando:\n"
            "  `llampaca models download <nome_modello>` (o dalla sezione Modelli nella Dashboard GUI).\n\n"
            "Ecco i modelli consigliati che puoi scegliere:\n"
            "⚡ **`sdxl-turbo-q4`**: Consigliato se desideri generazioni *Flash / Veloci* in pochissimi secondi (1.6 GB).\n"
            "🎨 **`flux-schnell-q4`**: Consigliato se desideri la *Massima Qualità Visiva* e dettagli fotorealistici (3.2 GB)."
        )

    # 2. Select model based on requested quality
    selected_model_id = None
    steps = 2 if quality == "fast" else 4

    for m in installed_models:
        if quality == "fast" and m.get("quality_preset") == "fast":
            selected_model_id = m["id"]
            steps = m.get("default_steps", 2)
            break
        elif quality == "high" and m.get("quality_preset") == "high":
            selected_model_id = m["id"]
            steps = m.get("default_steps", 4)
            break

    if not selected_model_id:
        selected_model_id = installed_models[0]["id"]
        steps = installed_models[0].get("default_steps", 4)

    # 3. Resolve destination file path
    filename = f"llampaca_image_{int(time.time())}.png"
    destination_path = resolve_user_output_path(filename, custom_dir=output_directory, subfolder="Images")

    # 4. Generate image on-demand
    try:
        res = generate_image_file(
            prompt=prompt,
            output_path=destination_path,
            model_name_or_path=selected_model_id,
            steps=steps
        )
        saved_path = res["output_path"]
        media_url = f"/api/media?path={saved_path}"
        return (
            f"Immagine generata con successo!\n\n"
            f"![{prompt}]({media_url})\n\n"
            f"📁 [Apri file originale ({destination_path.name})](file://{saved_path})"
        )
    except Exception as e:
        return f"Errore durante la generazione dell'immagine: {e}"


def register_image_tools(registry) -> None:
    """Registers image generation tools on the given ToolRegistry."""
    registry.register(generate_image)


def get_image_tools() -> List[Dict[str, Any]]:
    """Returns tool definitions for image generation."""
    return [
        {
            "name": "generate_image",
            "description": "Generates a new image from a prompt using local Stable Diffusion C++ engine. Saves output to ~/Documents/LlampacaDocs/Images/ or user's requested directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "Detailed text prompt describing the image to generate."
                    },
                    "output_directory": {
                        "type": "string",
                        "description": "Optional custom directory path where to save the image (e.g. ~/Desktop or ./out). If not provided, defaults to ~/Documents/LlampacaDocs/Images/."
                    },
                    "quality": {
                        "type": "string",
                        "enum": ["fast", "high"],
                        "description": "Preset quality: 'fast' (flash generation in 2 steps) or 'high' (detailed generation in 4 steps). Default 'fast'."
                    }
                },
                "required": ["prompt"]
            },
            "fn": generate_image
        }
    ]
