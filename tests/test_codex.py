"""Protocol and parity tests. No account, network or desktop actions required."""
import io
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.llm.client import LLMClient
from src.llm.codex import CodexSession
from src.llm.cli_registry import KNOWN_CLI_TOOLS
from src.tools.executor import ToolExecutor, get_openai_tools


class CodexTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.settings = {"llm_mode": "cli", "llm_cli_tool_key": "codex (OpenAI)",
                         "workspace_dir": self.tmp.name, "enable_tool_calling": True,
                         "history_max_turns": 2, "tool_max_iterations": 8,
                         "require_confirm_on_tool": False, "require_confirm_on_write": True}

    def session(self, calls=()):
        s = CodexSession(self.tmp.name)
        s.send = Mock()
        s.deadline = float("inf")
        s.events.put({"id": 1, "result": {"config": {"mcp_servers": {"external": {}}}}})
        s.events.put({"id": 2, "result": {"thread": {"id": "thread"}}})
        # Exercise server requests arriving BEFORE turn/start's response.
        for index, (name, arguments) in enumerate(calls):
            s.events.put({"id": f"tool-{index}", "method": "item/tool/call", "params": {
                "tool": name, "arguments": arguments, "callId": str(index),
                "threadId": "thread", "turnId": "turn"}})
        s.events.put({"id": 3, "result": {"turn": {"id": "turn"}}})
        s.events.put({"method": "item/completed", "params": {"item": {
            "type": "agentMessage", "phase": "commentary", "text": "Thinking"}}})
        s.events.put({"method": "item/completed", "params": {"item": {
            "type": "agentMessage", "phase": "final_answer", "text": "Tamam [EKRANDA_GOSTER]"}}})
        s.events.put({"method": "turn/completed", "params": {"turn": {"status": "completed"}}})
        return s

    def generate(self, s, executor=None, tools=None, limit=20):
        used = []
        output = s.generate("SYSTEM", "USER", [{"role": "user", "content": "Önceki"}],
                            "test-model", get_openai_tools() if tools is None else tools,
                            executor or ToolExecutor(self.settings), used, limit)
        return output, used

    def results(self, session):
        return [call.args[0]["result"]["contentItems"][0]["text"]
                for call in session.send.call_args_list
                if "result" in call.args[0] and "contentItems" in call.args[0]["result"]]

    def test_every_api_schema_is_registered_without_loss(self):
        s = self.session()
        output, used = self.generate(s)
        start = next(c.args[0]["params"] for c in s.send.call_args_list
                     if c.args[0].get("method") == "thread/start")
        self.assertEqual(len(start["dynamicTools"]), 13)
        for source, target in zip(get_openai_tools(), start["dynamicTools"]):
            self.assertEqual(source["function"]["name"], target["name"])
            self.assertEqual(source["function"]["parameters"], target["inputSchema"])
            self.assertEqual(source["function"]["description"], target["description"])
        self.assertTrue(start["ephemeral"])
        self.assertEqual(start["model"], "test-model")
        self.assertFalse(start["config"]['mcp_servers."external".enabled'])
        self.assertFalse(start["config"]["features.shell_tool"])
        self.assertNotIn("Thinking", output)
        self.assertEqual(used, [])

    def test_real_file_shell_and_browser_tools_roundtrip(self):
        browser = Mock(return_value=True)
        ex = ToolExecutor(self.settings, browser_sender=browser)
        s = self.session([
            ("write_file", {"path": "not.txt", "content": "Türkçe"}),
            ("append_file", {"path": "not.txt", "content": " devam"}),
            ("read_file", {"path": "not.txt"}), ("list_directory", {"path": "."}),
            ("run_shell_command", {"command": "echo CODEX_SHELL"}),
            ("browser_action", {"action": "scroll_down"}),
        ])
        output, used = self.generate(s, ex)
        self.assertEqual(Path(self.tmp.name, "not.txt").read_text(), "Türkçe devam")
        results = self.results(s)
        self.assertIn("Türkçe devam", results[2])
        self.assertIn("not.txt", results[3])
        self.assertIn("CODEX_SHELL", results[4])
        browser.assert_called_once()
        self.assertEqual(len(used), 6)

    def test_all_tools_dispatch_to_same_executor(self):
        names = [t["function"]["name"] for t in get_openai_tools()]
        ex = Mock()
        ex.execute_tool.side_effect = lambda name, args: f"RESULT:{name}"
        s = self.session([(name, {"unicode": "İstanbul"}) for name in names])
        _, used = self.generate(s, ex)
        self.assertEqual(used, names)
        self.assertEqual(self.results(s), [f"RESULT:{name}" for name in names])
        for name, args in [call.args for call in ex.execute_tool.call_args_list]:
            self.assertEqual(json.loads(args), {"unicode": "İstanbul"})

    def test_sensitive_read_and_write_denials_do_not_execute(self):
        confirm = Mock(return_value=False)
        self.settings["require_confirm_on_tool"] = True
        ex = ToolExecutor(self.settings, confirm_callback=confirm)
        s = self.session([
            ("read_screen_text", {}), ("get_clipboard_text", {}),
            ("write_file", {"path": "blocked.txt", "content": "bad"}),
            ("read_file", {"path": "../outside"}),
            ("run_shell_command", {"command": "rm kept.txt"}),
        ])
        Path(self.tmp.name, "kept.txt").write_text("keep")
        with patch("src.tools.executor.read_screen_via_ocr") as screen, \
                patch("src.tools.executor.read_clipboard_subprocess") as clipboard:
            self.generate(s, ex)
        screen.assert_not_called()
        clipboard.assert_not_called()
        self.assertFalse(Path(self.tmp.name, "blocked.txt").exists())
        self.assertTrue(Path(self.tmp.name, "kept.txt").exists())
        self.assertEqual(confirm.call_count, 5)
        self.assertTrue(all("izin vermedi" in r or "reddetti" in r or "çalışma alanı" in r.lower()
                            for r in self.results(s)))

    def test_disabled_unknown_and_budget_exhausted_tools(self):
        ex = Mock()
        s = self.session([("read_file", {}), ("native_shell", {})])
        self.generate(s, ex, tools=[])
        ex.execute_tool.assert_not_called()
        self.assertTrue(all("devre dışı" in r for r in self.results(s)))
        ex.execute_tool.return_value = "ok"
        s = self.session([("read_file", {}), ("read_file", {})])
        self.generate(s, ex, limit=1)
        ex.execute_tool.assert_called_once()
        self.assertIn("limiti", self.results(s)[1])

    def test_native_approvals_are_declined(self):
        s = self.session()
        for method in ("item/commandExecution/requestApproval", "item/fileChange/requestApproval"):
            s.handle({"id": 99, "method": method, "params": {}})
            self.assertEqual(s.send.call_args.args[0]["result"], {"decision": "decline"})

    def test_errors_timeout_and_empty_output(self):
        s = self.session()
        s.events = queue.Queue()
        s.deadline = 0
        with self.assertRaises(TimeoutError):
            s.receive()
        s = self.session()
        with self.assertRaisesRegex(RuntimeError, "login"):
            s.handle({"method": "turn/completed", "params": {"turn": {
                "status": "failed", "error": {"message": "login required"}}}})
        s = self.session()
        s.events = queue.Queue()
        s.events.put({"id": 1, "error": {"message": "unsupported dynamicTools"}})
        with self.assertRaisesRegex(RuntimeError, "unsupported"):
            self.generate(s)
        s = self.session()
        events = []
        while not s.events.empty():
            event = s.events.get_nowait()
            if event.get("method") != "item/completed":
                events.append(event)
        for event in events:
            s.events.put(event)
        with self.assertRaisesRegex(RuntimeError, "son yanıt"):
            self.generate(s)

    def test_cli_history_context_and_dictation_are_isolated(self):
        client = LLMClient(self.settings)
        captures = []
        def generate(*args):
            captures.append(args)
            return "Yanıt [EKRANDA_GOSTER]"
        with patch("src.llm.codex.CodexSession") as cls:
            cls.return_value.__enter__.return_value.generate.side_effect = generate
            client.generate_response("SYSTEM", "ilk", context="PAGE")
            client.generate_response("SYSTEM", "ikinci")
            client.generate_response("SYSTEM", "dikte", skip_injection=True)
        self.assertIn("PAGE", captures[0][1])
        self.assertEqual(captures[1][2][0]["content"], "ilk")
        self.assertEqual(captures[2][2], [])
        self.assertEqual(captures[2][4], [])
        self.assertEqual(len(client.history), 4)
        client.clear_history()
        self.assertEqual(client.history, [])

    def test_extra_args_cannot_override_permissions(self):
        for value in ("--yolo", "-c sandbox_mode=danger-full-access", "-c features.shell_tool=true"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                CodexSession(self.tmp.name, value)
        s = CodexSession(self.tmp.name, '-c model_reasoning_effort="low"')
        self.assertEqual(s.extra_args, ["-c", "model_reasoning_effort=low"])

    def test_process_is_reaped_on_handshake_failure(self):
        process = Mock()
        process.stdout = io.StringIO('{"id":1,"error":{"message":"bad version"}}\n')
        process.poll.return_value = None
        with patch("src.llm.codex.subprocess.Popen", return_value=process):
            with self.assertRaisesRegex(RuntimeError, "bad version"):
                with CodexSession(self.tmp.name):
                    pass
        process.terminate.assert_called_once()
        process.wait.assert_called_once()

    def test_gui_selects_and_saves_codex_and_model(self):
        # Offscreen Qt and dummy hotkeys avoid acquiring the user's desktop.
        code = '''
from unittest.mock import Mock, patch
from PyQt6.QtWidgets import QApplication
from src.gui.app import SettingsWindow
from src.core.settings import DEFAULT_SETTINGS
class Settings:
    def __init__(self): self.data = DEFAULT_SETTINGS.copy()
    def get(self, key, default=None): return self.data.get(key, default)
    def set(self, key, value): self.data[key] = value
s = Settings()
app = QApplication([])
w = SettingsWindow(s, Mock())
w.llm_mode_combo.setCurrentIndex(w.llm_mode_combo.findData('cli'))
w.cli_tool_combo.setCurrentText('agy (Antigravity)')
assert not w.cli_model_combo.isEnabled()
w.cli_tool_combo.setCurrentText('codex (OpenAI)')
assert w.cli_model_combo.isEnabled()
w.cli_model_combo.setCurrentText('test-model')
with patch('src.gui.app.QMessageBox.information'):
    w._save()
assert s.get('llm_cli_tool_key') == 'codex (OpenAI)'
assert s.get('llm_cli_model') == 'test-model'
assert s.get('llm_mode') == 'cli'
'''
        env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYNPUT_BACKEND="dummy")
        result = subprocess.run([sys.executable, "-c", code], env=env,
                                cwd=Path(__file__).resolve().parents[1],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
