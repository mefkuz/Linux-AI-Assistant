"""Regression coverage for problems found during real runtime verification."""
from pathlib import Path
import os
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.llm.client import LLMClient
from src.tools.executor import ToolExecutor, read_screen_via_ocr
from src.tools import os_input


class RuntimeRegressionTests(unittest.TestCase):
    def test_ydotool_client_uses_the_same_socket_as_its_daemon(self):
        with patch.dict(os.environ, {"YDOTOOL_SOCKET": "/tmp/custom-ydotool-test.sock"}), \
                patch.object(os_input, "ensure_ydotoold", return_value=True), \
                patch("shutil.which", return_value="/usr/bin/ydotool"), \
                patch("subprocess.run", return_value=Mock(returncode=0)) as run:
            self.assertTrue(os_input.send_key_combination("ctrl+v")[0])
        self.assertEqual(run.call_args.kwargs["env"]["YDOTOOL_SOCKET"], "/tmp/custom-ydotool-test.sock")
        self.assertIn("--key-delay=50", run.call_args.args[0])

    def test_ocr_tries_installed_backend_when_grim_is_missing(self):
        def capture(command, **kwargs):
            Path(command[-1]).write_bytes(b"fixture")
            return Mock(returncode=0)
        with patch("src.tools.executor.shutil.which", side_effect=lambda c: c if c == "spectacle" else None), \
                patch("subprocess.run", side_effect=capture) as run, \
                patch("src.tools.executor._ocr_image", return_value="SCREEN VERIFICATION 73921"):
            self.assertIn("73921", read_screen_via_ocr())
        self.assertEqual(run.call_args.args[0][0], "spectacle")
        self.assertIn("timeout", run.call_args.kwargs)
        self.assertFalse(Path(run.call_args.args[0][-1]).exists())

    def test_ocr_timeout_does_not_prevent_fallback(self):
        paths = []
        def capture(command, **kwargs):
            paths.append(command[-1])
            if command[0] == "grim":
                raise subprocess.TimeoutExpired(command, 15)
            Path(command[-1]).write_bytes(b"fixture")
            return Mock(returncode=0)
        with patch("src.tools.executor.shutil.which", side_effect=lambda c: c), \
                patch("subprocess.run", side_effect=capture), \
                patch("src.tools.executor._ocr_image", return_value="SCREEN VERIFICATION 73921"):
            self.assertIn("73921", read_screen_via_ocr())
        self.assertEqual(len(paths), 2)
        self.assertFalse(Path(paths[-1]).exists())

    def test_failed_capture_cannot_read_stale_image(self):
        def capture(command, **kwargs):
            self.assertFalse(Path(command[-1]).exists())
            Path(command[-1]).write_bytes(b"partial")
            return Mock(returncode=1)
        with patch("src.tools.executor.shutil.which", side_effect=lambda c: c), \
                patch("subprocess.run", side_effect=capture), \
                patch("src.tools.executor._ocr_image") as ocr, patch("time.sleep"):
            self.assertEqual(read_screen_via_ocr(), "")
        ocr.assert_not_called()

    def test_browser_key_is_forwarded_to_extension(self):
        sender = Mock(return_value=True)
        ex = ToolExecutor({"require_confirm_on_tool": False}, browser_sender=sender)
        ex.execute_tool("browser_action", {"action": "press_key", "key": "Tab"})
        self.assertEqual(sender.call_args.args[0]["params"]["key"], "Tab")

    def test_model_error_is_recorded_and_cleared_on_next_request(self):
        client = LLMClient({"llm_mode": "local"})
        with patch.object(client, "_call_local", side_effect=RuntimeError("login required")):
            result = client.generate_response("system", "question")
        self.assertIn("login required", result)
        self.assertEqual(client.last_error, "login required")
        self.assertEqual(client.history, [])
        with patch.object(client, "_call_local", return_value="working"):
            self.assertEqual(client.generate_response("system", "question"), "working")
        self.assertIsNone(client.last_error)

    def test_api_and_codex_use_identical_response_window_and_show_errors(self):
        code = '''
from unittest.mock import Mock, patch
from PyQt6.QtWidgets import QApplication, QLabel
from src.gui.app import AppManager, ResponseWindow
app = QApplication([])
window = ResponseWindow('[LLM Hatası (cli)]: login required')
assert 'tamamlanamadı' in window.findChildren(QLabel)[0].text()
assert 'login required' in window.text_browser.toPlainText()
manager = AppManager.__new__(AppManager)
manager.settings = {'enable_tool_calling': True}
manager._is_listening = True
manager._context_queue = []
manager.comm = Mock()
manager.audio = Mock()
manager.audio.listen_and_transcribe.return_value = 'hello'
manager.router = Mock()
manager.router.parse_and_route.return_value = '[LLM Hatası (cli)]: login required'
manager.router.llm.last_error = 'login required'
with patch('src.gui.app.time.sleep'):
    manager._process('')
manager.comm.show_response.emit.assert_called_once_with('[LLM Hatası (cli)]: login required')
assert not any('tamamlandı' in str(c) for c in manager.comm.update_text.emit.call_args_list)
assert not manager._is_listening
manager._is_listening = True
manager.settings['optimize_dictation'] = True
manager.router.llm.generate_response.return_value = '[LLM Hatası (cli)]: login required'
with patch('src.gui.app.time.sleep'):
    manager._process('')
assert manager.router.parse_and_route.call_args.args[0] == 'hello'
manager._is_listening = True
manager.settings['optimize_dictation'] = False
manager.router.llm.last_error = None
manager.router.llm.last_cancelled = True
manager.router.parse_and_route.return_value = 'Yanıt verilmedi; işlem durduruldu. [EKRANDA_GOSTER]'
manager.comm.reset_mock()
with patch('src.gui.app.time.sleep'):
    manager._process('')
manager.comm.show_response.emit.assert_not_called()
assert any('iptal edildi' in str(c) for c in manager.comm.update_text.emit.call_args_list)
assert not any('arka planda tamamlandı' in str(c) for c in manager.comm.update_text.emit.call_args_list)
'''
        result = subprocess.run([sys.executable, "-c", code],
                                env=dict(os.environ, QT_QPA_PLATFORM="offscreen", PYNPUT_BACKEND="dummy"),
                                cwd=Path(__file__).resolve().parents[1],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
