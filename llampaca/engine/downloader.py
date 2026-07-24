import os
import sys
import platform
import zipfile
import tempfile
import shutil
import requests
import subprocess
from pathlib import Path
from tqdm import tqdm
# pyrefly: ignore [missing-import]
from huggingface_hub import hf_hub_download
from llampaca.config import BIN_DIR, MODELS_DIR

GITHUB_API_URL = "https://api.github.com/repos/ggml-org/llama.cpp/releases/latest"

def detect_gpu_type(os_name: str) -> str:
    """Detect if the system has an AMD, Nvidia, or Apple Silicon/Intel GPU."""
    if os_name == "darwin":
        return "metal"
        
    if os_name == "win32":
        try:
            out = subprocess.check_output(
                ["powershell", "-Command", "Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name"],
                stderr=subprocess.DEVNULL,
                text=True
            )
            out_lower = out.lower()
            if "amd" in out_lower or "radeon" in out_lower:
                return "amd"
            if "nvidia" in out_lower or "geforce" in out_lower:
                return "nvidia"
        except Exception:
            pass
            
    elif os_name.startswith("linux"):
        try:
            out = subprocess.check_output(["lspci"], stderr=subprocess.DEVNULL, text=True)
            out_lower = out.lower()
            if "amd" in out_lower or "radeon" in out_lower or "ati" in out_lower:
                return "amd"
            if "nvidia" in out_lower:
                return "nvidia"
        except Exception:
            pass
            
    return "cpu"

def get_platform_details():
    """Detect current OS, architecture, and GPU type."""
    os_name = sys.platform
    arch = platform.machine().lower()
    
    # Map architectures to standard terms
    if arch in ["x86_64", "amd64"]:
        arch_clean = "x64"
    elif arch in ["arm64", "aarch64"]:
        arch_clean = "arm64"
    else:
        arch_clean = arch
        
    gpu_type = detect_gpu_type(os_name)
    return os_name, arch_clean, gpu_type

def find_matching_asset(assets, os_name, arch, gpu_type):
    """
    Find the most suitable asset for the current OS, architecture, and GPU.
    Supports both .zip and .tar.gz.
    """
    valid_extensions = (".zip", ".tar.gz", ".tgz")
    
    # macOS
    if os_name == "darwin":
        if arch == "arm64":
            # Search for macos-arm64
            for asset in assets:
                name = asset["name"].lower()
                if "macos" in name and "arm64" in name and name.endswith(valid_extensions):
                    return asset
        # Intel mac or fallback
        for asset in assets:
            name = asset["name"].lower()
            if "macos" in name and ("x64" in name or "x86_64" in name) and name.endswith(valid_extensions):
                return asset
                
    # Windows
    elif os_name == "win32":
        # Look for GPU-specific packages first if detected
        preferences = []
        if gpu_type == "amd":
            preferences.extend(["win-hip-x64", "win-hip"])
        elif gpu_type == "nvidia":
            preferences.extend(["win-cuda-x64", "win-cuda"])
            
        # Standard CPU/LLVM fallbacks
        preferences.extend(["win-llvm-x64", "win-sycl-x64", "win-x64", "win-llvm"])
        
        for pref in preferences:
            for asset in assets:
                name = asset["name"].lower()
                if pref in name and name.endswith(valid_extensions):
                    return asset
        # Fallback to any win asset
        for asset in assets:
            name = asset["name"].lower()
            if "win" in name and name.endswith(valid_extensions):
                return asset
                
    # Linux
    elif os_name.startswith("linux"):
        preferences = []
        if gpu_type == "amd":
            preferences.extend(["rocm", "ubuntu-rocm"])
        elif gpu_type == "nvidia":
            preferences.extend(["cuda", "ubuntu-cuda"])
            
        for pref in preferences:
            for asset in assets:
                name = asset["name"].lower()
                if ("ubuntu" in name or "linux" in name) and pref in name and "x64" in name and name.endswith(valid_extensions):
                    return asset
                    
        # Standard fallback Linux assets
        for asset in assets:
            name = asset["name"].lower()
            if ("ubuntu" in name or "linux" in name) and "x64" in name and name.endswith(valid_extensions):
                return asset
        # Fallback to any linux/ubuntu asset
        for asset in assets:
            name = asset["name"].lower()
            if ("ubuntu" in name or "linux" in name) and name.endswith(valid_extensions):
                return asset
                
    return None

def download_file(url: str, dest_path: Path):
    """Download a file with a progress bar."""
    response = requests.get(url, stream=True)
    response.raise_for_status()
    total_size = int(response.headers.get("content-length", 0))
    
    with open(dest_path, "wb") as f, tqdm(
        desc=dest_path.name,
        total=total_size,
        unit="B",
        unit_scale=True,
        unit_divisor=1024,
    ) as bar:
        for chunk in response.iter_content(chunk_size=8192):
            if chunk:
                f.write(chunk)
                bar.update(len(chunk))

def download_llama_binaries() -> bool:
    """
    Download the latest precompiled llama-server and llama-cli binaries.
    Returns True if successful, False otherwise.
    """
    os_name, arch, gpu_type = get_platform_details()
    print(f"Detecting system: OS={os_name}, Architecture={arch}, GPU={gpu_type}")
    
    print("Fetching latest llama.cpp release metadata from GitHub...")
    try:
        # Use headers to avoid rate limits where possible
        headers = {"Accept": "application/vnd.github+json"}
        r = requests.get(GITHUB_API_URL, headers=headers)
        r.raise_for_status()
        release_data = r.json()
    except Exception as e:
        print(f"Error fetching release metadata from GitHub: {e}")
        return False
        
    assets = release_data.get("assets", [])
    asset = find_matching_asset(assets, os_name, arch, gpu_type)
    
    if not asset:
        print("Could not find a precompiled binary asset matching your platform on GitHub.")
        print("Please check your internet connection or install llama.cpp manually.")
        return False
        
    download_url = asset["browser_download_url"]
    asset_name = asset["name"]
    print(f"Found matching asset: {asset_name}")
    print(f"Downloading from: {download_url}")
    
    # Create temp directory
    with tempfile.TemporaryDirectory() as tmpdir:
        temp_zip = Path(tmpdir) / asset_name
        try:
            download_file(download_url, temp_zip)
        except Exception as e:
            print(f"Failed to download asset: {e}")
            return False
            
        print("Extracting binaries...")
        extract_dir = Path(tmpdir) / "extracted"
        extract_dir.mkdir()
        
        try:
            if asset_name.endswith((".tar.gz", ".tgz")):
                import tarfile
                with tarfile.open(temp_zip, "r:gz") as tar_ref:
                    tar_ref.extractall(extract_dir)
            else:
                with zipfile.ZipFile(temp_zip, "r") as zip_ref:
                    zip_ref.extractall(extract_dir)
        except Exception as e:
            print(f"Failed to extract archive file: {e}")
            return False
            
        # Search recursively for the directory containing llama-server
        server_dir = None
        server_names = ["llama-server", "llama-server.exe"]
        for root, _, files in os.walk(extract_dir):
            for file in files:
                if file in server_names:
                    server_dir = Path(root)
                    break
            if server_dir:
                break
                
        if not server_dir:
            print("Could not find 'llama-server' executable in the downloaded archive.")
            return False
            
        # Ensure bin dir exists
        BIN_DIR.mkdir(parents=True, exist_ok=True)
        
        # Copy all files from server_dir to BIN_DIR (this includes dynamic libraries)
        for item in server_dir.iterdir():
            if item.is_file():
                dest_path = BIN_DIR / item.name
                if dest_path.exists():
                    try:
                        dest_path.unlink()
                    except Exception:
                        pass # Ignore if locked or in use, but attempt copy
                        
                shutil.copy2(item, dest_path)
                
                # Make executable if it's not a library or text file and not on Windows
                if sys.platform != "win32":
                    is_lib = item.name.endswith((".dylib", ".so", ".dll", ".json", ".txt"))
                    is_license = item.name == "LICENSE"
                    if not is_lib and not is_license:
                        os.chmod(dest_path, 0o755)
                
                print(f"Installed {item.name} to {dest_path}")
            
    print("llama.cpp binaries and libraries installed successfully!")
    return True


SD_GITHUB_API_URL = "https://api.github.com/repos/leejet/stable-diffusion.cpp/releases/latest"

def download_sd_binary():
    """
    Downloads the precompiled stable-diffusion.cpp (sd) executable
    suited for the current OS and GPU architecture into BIN_DIR.
    """
    target_name = "sd.exe" if sys.platform == "win32" else "sd"
    target_path = BIN_DIR / target_name
    if target_path.exists() and os.access(target_path, os.X_OK if sys.platform != "win32" else os.F_OK):
        return True

    print(f"Checking for stable-diffusion.cpp binaries for {sys.platform}...")
    headers = {"User-Agent": "Llampaca-App"}

    try:
        res = requests.get(SD_GITHUB_API_URL, headers=headers, timeout=10)
        res.raise_for_status()
        release_data = res.json()
        assets = release_data.get("assets", [])
    except Exception as e:
        print(f"Failed to fetch GitHub release metadata for stable-diffusion.cpp: {e}")
        return False

    os_name, arch, gpu_type = get_platform_details()
    target_asset = find_matching_asset(assets, os_name, arch, gpu_type)

    if not target_asset:
        # Fallback search for any binary zip in assets
        for asset in assets:
            if "bin" in asset["name"].lower() and asset["name"].endswith(".zip"):
                target_asset = asset
                break

    if not target_asset:
        print("Could not find suitable stable-diffusion.cpp binary asset for this platform.")
        return False

    download_url = target_asset["browser_download_url"]
    asset_name = target_asset["name"]

    print(f"Downloading stable-diffusion.cpp binary from {download_url}...")

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_dir_path = Path(temp_dir)
        temp_zip = temp_dir_path / asset_name

        try:
            with requests.get(download_url, headers=headers, stream=True, timeout=60) as response:
                response.raise_for_status()
                total_size = int(response.headers.get("content-length", 0))
                with open(temp_zip, "wb") as f, tqdm(
                    desc=asset_name,
                    total=total_size,
                    unit="iB",
                    unit_scale=True,
                    unit_divisor=1024,
                ) as bar:
                    for chunk in response.iter_content(chunk_size=8192):
                        size = f.write(chunk)
                        bar.update(size)
        except Exception as e:
            print(f"Failed to download stable-diffusion.cpp archive: {e}")
            return False

        extract_dir = temp_dir_path / "extracted"
        extract_dir.mkdir()

        try:
            with zipfile.ZipFile(temp_zip, "r") as zip_ref:
                zip_ref.extractall(extract_dir)
        except Exception as e:
            print(f"Failed to extract stable-diffusion.cpp zip: {e}")
            return False

        sd_executable = None
        sd_names = ["sd-cli", "sd-cli.exe", "sd", "sd.exe", "stable-diffusion"]
        for root, _, files in os.walk(extract_dir):
            for file in files:
                if file in sd_names:
                    sd_executable = Path(root) / file
                    break
            if sd_executable:
                break

        BIN_DIR.mkdir(parents=True, exist_ok=True)
        if sd_executable and sd_executable.exists():
            # Copy all contents of the directory (including dylib / dll / so files)
            bin_source_dir = sd_executable.parent
            for item in bin_source_dir.iterdir():
                if item.is_file():
                    dest_path = BIN_DIR / item.name
                    shutil.copy2(item, dest_path)
                    if sys.platform != "win32" and not item.name.endswith((".dylib", ".so", ".txt")):
                        os.chmod(dest_path, 0o755)

            # Ensure 'sd' or 'sd.exe' exists as a symlink or copy to 'sd-cli'
            if target_path != sd_executable and not target_path.exists():
                shutil.copy2(sd_executable, target_path)
                if sys.platform != "win32":
                    os.chmod(target_path, 0o755)

            print(f"Installed {target_name} binary to {target_path}")
            return True
        else:
            print("Executable 'sd' not found in downloaded archive.")
            return False


import threading

downloads_lock = threading.Lock()
active_downloads = {}  # key: filename, value: progress info dict

def download_hf_model_async(repo_id: str, filename: str):
    """Starts the Hugging Face model download in a background thread."""
    thread = threading.Thread(
        target=_download_hf_model_thread,
        args=(repo_id, filename),
        daemon=True
    )
    thread.start()

def _download_hf_model_thread(repo_id: str, filename: str):
    try:
        MODELS_DIR.mkdir(parents=True, exist_ok=True)
        dest_path = MODELS_DIR / filename
        url = f"https://huggingface.co/{repo_id}/resolve/main/{filename}"
        
        with downloads_lock:
            active_downloads[filename] = {
                "repo_id": repo_id,
                "filename": filename,
                "downloaded_bytes": 0,
                "total_bytes": 0,
                "progress": 0,
                "status": "downloading"
            }
            
        print(f"[Downloader] Starting background download of {filename} from {repo_id}...")
        response = requests.get(url, stream=True, allow_redirects=True)
        response.raise_for_status()
        
        total_size = int(response.headers.get('content-length', 0))
        with downloads_lock:
            active_downloads[filename]["total_bytes"] = total_size
            
        downloaded = 0
        with open(dest_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=1024 * 1024): # 1MB chunks
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    progress = int((downloaded / total_size) * 100) if total_size else 0
                    
                    with downloads_lock:
                        if filename in active_downloads:
                            active_downloads[filename]["downloaded_bytes"] = downloaded
                            active_downloads[filename]["progress"] = progress
                            
        with downloads_lock:
            if filename in active_downloads:
                active_downloads[filename]["status"] = "completed"
                active_downloads[filename]["progress"] = 100
                
        print(f"[Downloader] Background download of {filename} completed successfully.")
        
    except Exception as e:
        print(f"[Downloader] Error in background download of {filename}: {e}")
        # Clean up partial file on failure if exists
        try:
            partial_file = MODELS_DIR / filename
            if partial_file.exists():
                partial_file.unlink()
        except Exception:
            pass
            
        with downloads_lock:
            if filename in active_downloads:
                active_downloads[filename]["status"] = "failed"
                active_downloads[filename]["error"] = str(e)


def download_hf_model(repo_id: str, filename: str) -> Path:
    """
    Download a GGUF model from Hugging Face.
    Returns the path to the downloaded file.
    """
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Downloading model {filename} from HF repository {repo_id}...")
    
    # hf_hub_download handles progress bar and caching.
    # We download directly to the MODELS_DIR folder.
    local_path = hf_hub_download(
        repo_id=repo_id,
        filename=filename,
        local_dir=MODELS_DIR,
        local_dir_use_symlinks=False
    )
    
    # hf_hub_download returns a string path, convert it to Path
    downloaded_path = Path(local_path)
    print(f"Model downloaded and saved to: {downloaded_path}")
    return downloaded_path
