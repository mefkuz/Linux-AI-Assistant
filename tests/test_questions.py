"""Clarification parity, cancellation, memory, terminal and actual Qt tests."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.llm.client import LLMClient
from src.llm.codex import CodexSession
from src.tools.executor import ToolExecutor, get_openai_tools
from src.tools.questions import validate_question, ask_in_terminal


def response(message):
    return Mock(ok=True, json=lambda: {"choices": [{"message": message}]})


def tool_call(name, arguments, ident="call-1"):
    return {"id": ident, "type": "function", "function": {
        "name": name, "arguments": json.dumps(arguments, ensure_ascii=False)}}


class QuestionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.settings = {"llm_mode": "local", "enable_tool_calling": True,
                         "workspace_dir": self.tmp.name, "require_confirm_on_tool": False,
                         "history_max_turns": 3, "tool_max_iterations": 5,
                         "remote_api_key": "test-key"}

    def test_validation_rejects_unusable_questions_before_callback(self):
        callback = Mock()
        ex = ToolExecutor(self.settings, question_callback=callback)
        for arguments in ({}, {"question": " "}, {"question": "x" * 2001},
                          {"question": "Q", "options": "not-a-list"},
                          {"question": "Q", "options": [" "]},
                          {"question": "Q", "options": list("1234567")}):
            with self.subTest(arguments=arguments):
                result = ex.execute_tool("ask_user", arguments)
                self.assertIn("hata", result.lower())
        callback.assert_not_called()
        self.assertEqual(validate_question({"question": " Q ", "options": [" A ", "A"]}),
                         {"question": "Q", "options": ["A"]})

    def test_question_does_not_ask_for_a_second_confirmation(self):
        self.settings["require_confirm_on_tool"] = True
        confirm = Mock(side_effect=AssertionError("no permission dialog for questions"))
        callback = Mock(return_value=" Kendi Türkçe yanıtım ")
        result = ToolExecutor(self.settings, confirm_callback=confirm,
                              question_callback=callback).execute_tool(
                                  "ask_user", {"question": "Hangi dosya?", "options": ["A", "B"]})
        self.assertEqual(json.loads(result), {"status": "answered", "answer": "Kendi Türkçe yanıtım"})
        confirm.assert_not_called()
        callback.assert_called_once_with({"question": "Hangi dosya?", "options": ["A", "B"]})

    def test_empty_cancelled_or_broken_callback_cannot_invent_answer(self):
        for value in (None, " ", False, RuntimeError("dialog unavailable")):
            callback = Mock(side_effect=value) if isinstance(value, Exception) else Mock(return_value=value)
            result = ToolExecutor(self.settings, question_callback=callback).execute_tool("ask_user", {"question": "Q?"})
            self.assertEqual(json.loads(result), {"status": "cancelled", "answer": None})

    def test_terminal_options_free_text_and_cancellation(self):
        request = {"question": "Q?", "options": ["A", "B"]}
        for inputs, expected in [(["2", ""], "B"), (["", "Özel yanıt"], "Özel yanıt"),
                                 (["/cancel"], None), ([EOFError()], None), ([KeyboardInterrupt()], None)]:
            with self.subTest(inputs=inputs), patch("builtins.input", side_effect=inputs), patch("builtins.print"):
                self.assertEqual(ask_in_terminal(request), expected)

    def test_terminal_entry_point_exits_cleanly_on_eof(self):
        root = Path(__file__).resolve().parents[1]
        run = subprocess.run([sys.executable, str(root / "main.py")], input="",
                             capture_output=True, text=True, timeout=5, cwd=root)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertNotIn("Kritik Hata", run.stdout)

    def test_api_uses_answer_for_real_file_and_remembers_it(self):
        for mode in ("local", "remote"):
            with self.subTest(mode=mode):
                self.settings["llm_mode"] = mode
                callback = Mock(return_value="İstanbul cevabı")
                client = LLMClient(self.settings, question_callback=callback)
                filename = mode + ".txt"
                seen = []
                def post(url, **kwargs):
                    messages = kwargs["json"]["messages"]
                    seen.append(json.loads(json.dumps(messages)))
                    if len(seen) == 1:
                        return response({"tool_calls": [tool_call("ask_user", {"question": "İçerik?"})]})
                    if len(seen) == 2:
                        answer = json.loads(messages[-1]["content"])["answer"]
                        return response({"tool_calls": [tool_call("write_file", {"path": filename, "content": answer})]})
                    return response({"content": "Tamam."})
                with patch("requests.post", side_effect=post):
                    self.assertEqual(client.generate_response("system", "Not oluştur."), "Tamam.")
                self.assertEqual(Path(self.tmp.name, filename).read_text(), "İstanbul cevabı")
                callback.assert_called_once()
                self.assertEqual(client.last_tools_used, ["ask_user", "write_file"])
                self.assertIn("İstanbul cevabı", client.history[0]["content"])
                client.clear_history()
                self.assertEqual(client.history, [])
                self.assertEqual(client._clarifications, [])

    def test_api_cancel_stops_later_calls_in_same_model_batch(self):
        client = LLMClient(self.settings, question_callback=lambda request: None)
        calls = [tool_call("write_file", {"path": "must-not-exist", "content": "bad"}, "call-2"),
                 tool_call("ask_user", {"question": "Q?"})]
        with patch("requests.post", return_value=response({"tool_calls": calls})) as post:
            reply = client.generate_response("system", "Do task")
        self.assertIn("durduruldu", reply)
        self.assertIsNone(client.last_error)
        self.assertTrue(client.last_cancelled)
        self.assertEqual(client.history, [])
        self.assertFalse(Path(self.tmp.name, "must-not-exist").exists())
        self.assertEqual(client.last_tools_used, ["ask_user"])
        post.assert_called_once()

    def test_api_internal_dictation_and_disabled_tools_cannot_open_questions(self):
        for mode in ("local", "remote"):
            for skip_injection, enabled in ((True, True), (False, False)):
                with self.subTest(mode=mode, skip_injection=skip_injection, enabled=enabled):
                    self.settings.update(llm_mode=mode, enable_tool_calling=enabled)
                    callback = Mock(side_effect=AssertionError("must not ask"))
                    client = LLMClient(self.settings, question_callback=callback)
                    with patch("requests.post", return_value=response({"content": "Düzeltilmiş metin"})) as post:
                        client.generate_response("Clean up dictation only", "raw text", skip_injection=skip_injection)
                    self.assertNotIn("tools", post.call_args.kwargs["json"])
                    callback.assert_not_called()

    def test_api_defers_guessed_batch_actions_until_answer_is_used(self):
        client = LLMClient(self.settings, question_callback=lambda request: "correct.txt")
        first = [tool_call("write_file", {"path": "guessed.txt", "content": "wrong"}, "write-1"),
                 tool_call("ask_user", {"question": "Filename?"}, "ask-1")]
        def post(url, **kwargs):
            messages = kwargs["json"]["messages"]
            if len(messages) == 2:
                return response({"tool_calls": first})
            self.assertFalse(Path(self.tmp.name, "guessed.txt").exists())
            self.assertIn("deferred", messages[-1]["content"])
            self.assertIn("correct.txt", messages[-2]["content"])
            return response({"content": "The user selected correct.txt"})
        with patch("requests.post", side_effect=post):
            client.generate_response("system", "Create a file after asking me its name")
        self.assertEqual(client.last_tools_used, ["ask_user"])
        self.assertFalse(Path(self.tmp.name, "guessed.txt").exists())

    def codex_session(self, calls):
        session = CodexSession(self.tmp.name)
        session.send = Mock()
        session.deadline = float("inf")
        for event in [
            {"id": 1, "result": {"config": {}}},
            {"id": 2, "result": {"thread": {"id": "thread"}}},
            {"id": 3, "result": {"turn": {"id": "turn"}}},
        ]:
            session.events.put(event)
        for i, (name, args) in enumerate(calls):
            session.events.put({"id": f"server-{i}", "method": "item/tool/call",
                                "params": {"tool": name, "arguments": args}})
        session.events.put({"method": "item/completed", "params": {
            "item": {"type": "agentMessage", "phase": "final_answer", "text": "Tamam."}}})
        session.events.put({"method": "turn/completed", "params": {"turn": {"status": "completed"}}})
        return session

    def test_codex_question_answer_and_cancellation_share_api_contract(self):
        self.settings.update(llm_mode="cli", llm_cli_tool_key="codex (OpenAI)")
        for answer in ("Türkçe yanıt", None):
            with self.subTest(answer=answer):
                filename = "codex-answered" if answer else "codex-cancelled"
                session = self.codex_session([
                    ("ask_user", {"question": "Q?", "options": ["A", "B"]}),
                    ("write_file", {"path": filename, "content": answer or "bad"}),
                ])
                client = LLMClient(self.settings, question_callback=lambda request: answer)
                with patch("src.llm.codex.CodexSession") as factory:
                    factory.return_value.__enter__.return_value = session
                    reply = client.generate_response("system", "Do task")
                    factory.return_value.__exit__.assert_called_once()
                self.assertIsNone(client.last_error)
                path = Path(self.tmp.name, filename)
                if answer:
                    self.assertEqual(path.read_text(), answer)
                    self.assertIn(answer, client.history[0]["content"])
                else:
                    self.assertFalse(path.exists())
                    self.assertIn("durduruldu", reply)
                    self.assertEqual(client.last_tools_used, ["ask_user"])
                    self.assertEqual(client.history, [])

    def test_question_gui_choices_free_text_cancel_and_worker_bridge(self):
        # Tests the real Qt dialog and queued thread bridge without exposing
        # windows or injecting any keyboard input into the user's desktop.
        code = '''
import threading
from unittest.mock import Mock, patch
from PyQt6.QtWidgets import QApplication, QDialog
from PyQt6.QtCore import QTimer, Qt
from src.gui.app import UserQuestionDialog, ConfirmDialog, AppManager, Communicate
app = QApplication([])
request = {'question': 'Hangi dosya?', 'options': ['A', 'B']}
d = UserQuestionDialog(request)
baseline = ConfirmDialog('Title', 'Question')
assert d.styleSheet() == baseline.styleSheet()
assert d.layout().contentsMargins() == baseline.layout().contentsMargins()
assert d.layout().spacing() == baseline.layout().spacing()
assert not d.send_button.isEnabled()
assert d.answer is None
def select_then_send():
    d.option_buttons[1].click()
    assert d.answer is None and not d._closed
    assert d.send_button.isEnabled()
    d.send_button.click()
QTimer.singleShot(0, select_then_send)
assert d.exec() == QDialog.DialogCode.Accepted
assert d.answer == 'B'
d = UserQuestionDialog(request)
d.answer_input.setPlainText('Özel çok satırlı\\nyanıt')
assert d.send_button.isEnabled()
QTimer.singleShot(0, d.send_button.click)
assert d.exec() == QDialog.DialogCode.Accepted
assert d.answer == 'Özel çok satırlı\\nyanıt'
d = UserQuestionDialog(request)
QTimer.singleShot(0, d.reject)
assert d.exec() == QDialog.DialogCode.Rejected
assert d.answer is None
manager = AppManager.__new__(AppManager)
manager.comm = Communicate()
manager._play_notification_sound = Mock()
manager.comm.ask_question.connect(manager._ask_question_gui, Qt.ConnectionType.QueuedConnection)
for cancel in (False, True):
    answers = []
    worker = threading.Thread(target=lambda: answers.append(manager._question_callback(request)))
    def respond():
        current = app.activeModalWidget()
        if not isinstance(current, UserQuestionDialog):
            QTimer.singleShot(10, respond)
            return
        if cancel: current.reject()
        else:
            current.option_buttons[0].click()
            assert current.answer is None and worker.is_alive()
            current.send_button.click()
    QTimer.singleShot(20, respond)
    worker.start()
    while worker.is_alive(): app.processEvents()
    worker.join(timeout=1)
    assert answers == [None if cancel else 'A'], answers
event = threading.Event()
out = [None]
with patch('src.gui.app.UserQuestionDialog', side_effect=RuntimeError('window failed')):
    manager._ask_question_gui(request, out, event)
assert event.is_set() and out == [None]
'''
        run = subprocess.run([sys.executable, "-c", code],
                             env=dict(os.environ, QT_QPA_PLATFORM="offscreen", PYNPUT_BACKEND="dummy"),
                             cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=20)
        self.assertEqual(run.returncode, 0, run.stderr)


if __name__ == "__main__":
    unittest.main()
