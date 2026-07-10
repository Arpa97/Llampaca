import os
import sys
import platform
import zipfile
import tempfile
import shutil
import requests
from pathlib import Path
from tqdm import tqdm
from huggingface_hub import hf_hub_download
from llampaca.config import BIN_DIR, MODELS_DIR

GITHUB_API_URL = "https://api.github.com/repos/ggml-org/llama.cpp/releases/latest"

def get_platform_details():
    """Detect current OS and architecture."""
    os_name = sys.platform
    arch = platform.machine().lower()
    
    # Map architectures to standard terms
    if arch in ["x86_64", "amd64"]:
        arch_clean = "x64"
    elif arch in ["arm64", "aarch64"]:
        arch_clean = "arm64"
    else:
        arch_clean = arch
        
    return os_name, arch_clean

def find_matching_asset(assets, os_name, arch):
    """
    Find the most suitable asset for the current OS and architecture.
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
        # Look for a standard CPU/LLVM build or simple x64
        # CUDA build can also be searched, but LLVM is the most universal CPU fallback
        # Let's search in order of preference
        preferences = ["win-llvm-x64", "win-sycl-x64", "win-x64", "win-llvm"]
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
        for asset in assets:
            name = asset["name"].lower()
            # Typically named llama-<version>-bin-ubuntu-x64.zip or bin-linux-x64.zip
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
    os_name, arch = get_platform_details()
    print(f"Detecting system: OS={os_name}, Architecture={arch}")
    
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
    asset = find_matching_asset(assets, os_name, arch)
    
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
