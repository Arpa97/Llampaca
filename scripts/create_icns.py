import os
import sys
import subprocess
from pathlib import Path

def create_icns(png_path: Path, output_icns: Path) -> bool:
    """
    Convert a PNG image into a macOS .icns file using native sips and iconutil commands.
    """
    if not png_path.exists():
        print(f"Error: PNG icon file not found at {png_path}", file=sys.stderr)
        return False

    iconset_dir = output_icns.parent / "Llampaca.iconset"
    iconset_dir.mkdir(parents=True, exist_ok=True)

    sizes = [16, 32, 64, 128, 256, 512]
    try:
        for size in sizes:
            # 1x resolution
            out_file = iconset_dir / f"icon_{size}x{size}.png"
            subprocess.run(["sips", "-z", str(size), str(size), str(png_path), "--out", str(out_file)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
            # 2x resolution (Retina)
            size2x = size * 2
            out_file2x = iconset_dir / f"icon_{size}x{size}@2x.png"
            subprocess.run(["sips", "-z", str(size2x), str(size2x), str(png_path), "--out", str(out_file2x)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

        # Convert iconset directory to .icns
        output_icns.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["iconutil", "-c", "icns", str(iconset_dir), "-o", str(output_icns)], check=True)
        print(f"Successfully generated macOS ICNS icon at {output_icns}")
        return True
    except Exception as e:
        print(f"Warning: Failed to generate .icns using iconutil: {e}", file=sys.stderr)
        return False
    finally:
        # Cleanup temporary iconset files
        if iconset_dir.exists():
            import shutil
            shutil.rmtree(iconset_dir, ignore_errors=True)

if __name__ == "__main__":
    project_root = Path(__file__).resolve().parent.parent
    png_icon = project_root / "llampaca" / "gui" / "app_icon_mac.png"
    out_icns = project_root / "build" / "Llampaca.icns"
    create_icns(png_icon, out_icns)
