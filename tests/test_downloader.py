import unittest
from llampaca.engine.downloader import find_matching_asset

class TestDownloader(unittest.TestCase):
    def test_find_matching_asset_macos(self):
        assets = [
            {"name": "llama-b1000-bin-macos-arm64.zip"},
            {"name": "llama-b1000-bin-macos-x64.zip"},
            {"name": "llama-b1000-bin-win-hip-x64.zip"},
            {"name": "llama-b1000-bin-win-cuda-x64.zip"},
        ]
        asset = find_matching_asset(assets, "darwin", "arm64", "metal")
        self.assertEqual(asset["name"], "llama-b1000-bin-macos-arm64.zip")

    def test_find_matching_asset_windows_amd(self):
        assets = [
            {"name": "llama-b1000-bin-win-hip-x64.zip"},
            {"name": "llama-b1000-bin-win-cuda-x64.zip"},
            {"name": "llama-b1000-bin-win-llvm-x64.zip"},
        ]
        asset = find_matching_asset(assets, "win32", "x64", "amd")
        self.assertEqual(asset["name"], "llama-b1000-bin-win-hip-x64.zip")

    def test_find_matching_asset_windows_nvidia(self):
        assets = [
            {"name": "llama-b1000-bin-win-hip-x64.zip"},
            {"name": "llama-b1000-bin-win-cuda-x64.zip"},
            {"name": "llama-b1000-bin-win-llvm-x64.zip"},
        ]
        asset = find_matching_asset(assets, "win32", "x64", "nvidia")
        self.assertEqual(asset["name"], "llama-b1000-bin-win-cuda-x64.zip")

    def test_find_matching_asset_windows_cpu(self):
        assets = [
            {"name": "llama-b1000-bin-win-hip-x64.zip"},
            {"name": "llama-b1000-bin-win-cuda-x64.zip"},
            {"name": "llama-b1000-bin-win-llvm-x64.zip"},
        ]
        asset = find_matching_asset(assets, "win32", "x64", "cpu")
        self.assertEqual(asset["name"], "llama-b1000-bin-win-llvm-x64.zip")

    def test_find_matching_asset_linux_amd(self):
        assets = [
            {"name": "llama-b1000-bin-ubuntu-rocm-x64.tar.gz"},
            {"name": "llama-b1000-bin-ubuntu-cuda-x64.tar.gz"},
            {"name": "llama-b1000-bin-ubuntu-x64.tar.gz"},
        ]
        asset = find_matching_asset(assets, "linux", "x64", "amd")
        self.assertEqual(asset["name"], "llama-b1000-bin-ubuntu-rocm-x64.tar.gz")

if __name__ == "__main__":
    unittest.main()
