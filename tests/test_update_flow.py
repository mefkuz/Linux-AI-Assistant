"""Exercise release UI and updater against isolated local Git fixtures."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class UpdateFlowTests(unittest.TestCase):
    def test_old_install_upgrade_and_real_qt_completion(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix='linux-ai-update-test-') as output:
            result = subprocess.run(
                [sys.executable, str(root / 'tests/manual/simulate_update.py'), '--output', output],
                cwd=root, env=dict(os.environ, QT_QPA_PLATFORM='offscreen', PYNPUT_BACKEND='dummy', LC_ALL='C'),
                capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            report = json.loads(Path(output, 'report.json').read_text())
            for key, value in report.items():
                if key != 'simulated_upgrade':
                    self.assertEqual(value, 'PASS', key)
            self.assertTrue(Path(output, 'update-Şimdi Güncelle.png').is_file())


if __name__ == '__main__':
    unittest.main()
