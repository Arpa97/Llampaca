import sys
import unittest
from llampaca.engine.server import check_gpu_vram_gb


class TestGpuVramCheck(unittest.TestCase):

    def test_mac_returns_inf(self):
        """On macOS, check_gpu_vram_gb should return float('inf') as Apple Silicon uses Unified Memory."""
        if sys.platform == "darwin":
            self.assertEqual(check_gpu_vram_gb(), float("inf"))
        else:
            vram = check_gpu_vram_gb()
            self.assertIsInstance(vram, float)
            self.assertGreaterEqual(vram, 0.0)


if __name__ == "__main__":
    unittest.main()
