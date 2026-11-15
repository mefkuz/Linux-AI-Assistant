"""Reported plain-text clarification regression and voice reply lifecycle."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.audio.listener import AudioListener
from src.llm.client import LLMClient
from src.llm.codex import CodexSession
from src.tools.questions import clarification_question
import speech_recognition as sr

CLARIFICATION_EXAMPLE = (
    "Neyi kastettiğinizi tam olarak belirtmediniz; ekrandaki videolardan biri mi "
    "(örneğin birinci kanalın proje incelemesi, ikinci kanalın eğitim videosu "
    "ya da üçüncü kanalın teknoloji analizleri gibi) yoksa bambaşka bir konu hakkında mı "
    "fikrimi merak ediyorsunuz?"
)


def response(content):
    return Mock(ok=True, json=lambda: {"choices": [{"message": {"content": content}}]})


class ClarificationFallbackTests(unittest.TestCase):
    def test_detects_reported_question_without_converting_general_answers(self):
        for text in (CLARIFICATION_EXAMPLE, "Hangi dosyayı kastediyorsunuz?", "Which file would you like?"):
            self.assertEqual(clarification_question(text + " [EKRANDA_GOSTER]"), text)
        for text in ("İstanbul 1453'te fethedildi. Daha fazlasını ister misin?",
                     "What else would you like to know?", "The answer is 42.",
                     '> Hangi dosyayı kastediyorsunuz?', '"Hangi dosyayı kastediyorsunuz?"',
                     "```python\nprint('Hangi dosyayı kastediyorsunuz?')\n```"):
            self.assertIsNone(clarification_question(text), text)

    def test_api_plain_question_opens_callback_then_continues_with_answer(self):
        for mode in ("local", "remote"):
            with self.subTest(mode=mode):
                callback = Mock(return_value="Örnek video incelemesini kastediyorum.")
                client = LLMClient({"llm_mode": mode, "remote_api_key": "test-key",
                                    "enable_tool_calling": True}, question_callback=callback)
                seen = []
                def post(*args, **kwargs):
                    seen.append(json.loads(json.dumps(kwargs["json"])))
                    return response(CLARIFICATION_EXAMPLE if len(seen) == 1 else "Örnek video incelemesi hakkında yanıt.")
                with patch("requests.post", side_effect=post):
                    reply = client.generate_response("system", "Bunun hakkında ne düşünüyorsun?", context="VIDEO_CONTEXT")
                callback.assert_called_once_with({"question": CLARIFICATION_EXAMPLE, "options": []})
                self.assertEqual(reply, "Örnek video incelemesi hakkında yanıt.")
                self.assertEqual(client.last_tools_used, ["ask_user"])
                messages = seen[1]["messages"]
                self.assertIn("VIDEO_CONTEXT", messages[1]["content"])
                self.assertEqual(messages[-2]["content"], CLARIFICATION_EXAMPLE)
                self.assertIn("Örnek video incelemesini", messages[-1]["content"])
                self.assertIn("Örnek video incelemesini", client.history[0]["content"])

    def test_plain_question_cancellation_does_not_request_another_response(self):
        client = LLMClient({"llm_mode": "local", "enable_tool_calling": True}, question_callback=lambda request: None)
        with patch("requests.post", return_value=response(CLARIFICATION_EXAMPLE)) as post:
            reply = client.generate_response("system", "Bunun hakkında ne düşünüyorsun?")
        self.assertTrue(client.last_cancelled)
        self.assertIn("durduruldu", reply)
        post.assert_called_once()

    def test_repeated_plain_questions_are_bounded(self):
        client = LLMClient({"llm_mode": "local", "enable_tool_calling": True,
                            "tool_max_iterations": 1}, question_callback=lambda request: "Örnek video")
        with patch("requests.post", return_value=response(CLARIFICATION_EXAMPLE)) as post:
            client.generate_response("system", "Bunun hakkında ne düşünüyorsun?")
        self.assertEqual(post.call_count, 2)
        self.assertIn("limiti", client.last_error)

    def test_codex_plain_question_continues_in_same_thread(self):
        with tempfile.TemporaryDirectory() as workspace:
            session = CodexSession(workspace)
            session.send = Mock()
            session.deadline = float('inf')
            for event in [
                {"id": 1, "result": {"config": {}}},
                {"id": 2, "result": {"thread": {"id": "same-thread"}}},
                {"id": 3, "result": {}},
                {"method": "item/completed", "params": {"item": {"type": "agentMessage", "text": CLARIFICATION_EXAMPLE}}},
                {"method": "turn/completed", "params": {"turn": {"status": "completed"}}},
                {"id": 4, "result": {}},
                {"method": "item/completed", "params": {"item": {"type": "agentMessage", "text": "Örnek video yanıtı."}}},
                {"method": "turn/completed", "params": {"turn": {"status": "completed"}}},
            ]:
                session.events.put(event)
            callback = Mock(return_value="Örnek video incelemesi")
            client = LLMClient({"llm_mode": "cli", "llm_cli_tool_key": "codex (OpenAI)",
                                "workspace_dir": workspace, "enable_tool_calling": True}, question_callback=callback)
            with patch("src.llm.codex.CodexSession") as factory:
                factory.return_value.__enter__.return_value = session
                reply = client.generate_response("system", "Bu konuda ne düşünüyorsun?")
            self.assertEqual(reply, "Örnek video yanıtı.")
            callback.assert_called_once()
            turns = [c.args[0]["params"] for c in session.send.call_args_list if c.args[0].get("method") == "turn/start"]
            self.assertEqual([t["threadId"] for t in turns], ["same-thread", "same-thread"])
            self.assertIn("Örnek video incelemesi", turns[1]["input"][0]["text"])
            self.assertEqual(client.last_tools_used, ["ask_user"])

    def test_codex_plain_question_cancel_stops_before_another_turn(self):
        with tempfile.TemporaryDirectory() as workspace:
            session = CodexSession(workspace)
            session.send = Mock()
            session.deadline = float('inf')
            for event in [
                {"id": 1, "result": {"config": {}}},
                {"id": 2, "result": {"thread": {"id": "thread"}}},
                {"id": 3, "result": {}},
                {"method": "item/completed", "params": {"item": {"type": "agentMessage", "text": CLARIFICATION_EXAMPLE}}},
                {"method": "turn/completed", "params": {"turn": {"status": "completed"}}},
            ]:
                session.events.put(event)
            client = LLMClient({"llm_mode": "cli", "llm_cli_tool_key": "codex (OpenAI)",
                                "workspace_dir": workspace, "enable_tool_calling": True}, question_callback=lambda req: None)
            with patch("src.llm.codex.CodexSession") as factory:
                factory.return_value.__enter__.return_value = session
                client.generate_response("system", "Ne düşünüyorsun?")
            self.assertTrue(client.last_cancelled)
            self.assertEqual(client.last_tools_used, ["ask_user"])
            self.assertEqual(sum(c.args[0].get("method") == "turn/start" for c in session.send.call_args_list), 1)


class CancellableAudioTests(unittest.TestCase):
    def setup_audio(self):
        recognizer = Mock()
        stream = Mock()
        stream.read.return_value = b"audio"
        source = SimpleNamespace(stream=stream)
        mic = MagicMock()
        mic.__enter__.return_value = source
        return recognizer, stream, source, mic

    def test_microphone_is_released_before_transcription_and_timeout_is_bounded(self):
        recognizer, stream, source, mic = self.setup_audio()
        def transcribe(audio, language):
            mic.__exit__.assert_called_once()
            self.assertEqual(language, "tr-TR")
            return "Üçüncü video"
        recognizer.recognize_google.side_effect = transcribe
        with patch("src.audio.listener.sr.Recognizer", return_value=recognizer), \
                patch("src.audio.listener.sr.Microphone", return_value=mic):
            listener = AudioListener({"language": "tr-TR"})
            self.assertEqual(listener.listen_and_transcribe(cancel_event=threading.Event()), "Üçüncü video")
        self.assertEqual(recognizer.operation_timeout, 15)
        self.assertIsNone(listener.last_error)

    def test_cancel_during_recording_prevents_transcription(self):
        recognizer, stream, source, mic = self.setup_audio()
        cancel = threading.Event()
        def listen(source, **kwargs):
            cancel.set()
            source.stream.read(10)
        recognizer.listen.side_effect = listen
        with patch("src.audio.listener.sr.Recognizer", return_value=recognizer), \
                patch("src.audio.listener.sr.Microphone", return_value=mic):
            listener = AudioListener()
            self.assertIsNone(listener.listen_and_transcribe(cancel_event=cancel))
        recognizer.recognize_google.assert_not_called()
        mic.__exit__.assert_called_once()
        self.assertIsNone(listener.last_error)

    def test_no_speech_and_service_errors_are_visible(self):
        for error in (sr.WaitTimeoutError(), sr.UnknownValueError(), sr.RequestError("offline")):
            recognizer, stream, source, mic = self.setup_audio()
            recognizer.listen.side_effect = error
            with patch("src.audio.listener.sr.Recognizer", return_value=recognizer), \
                    patch("src.audio.listener.sr.Microphone", return_value=mic):
                listener = AudioListener()
                self.assertIsNone(listener.listen_and_transcribe())
                self.assertTrue(listener.last_error)


class VoiceDialogTests(unittest.TestCase):
    def test_plain_question_to_dialog_voice_manual_send_and_model_continuation(self):
        code = '''
import json, threading, time
from unittest.mock import Mock, patch
from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtWidgets import QApplication
from src.gui.app import AppManager, Communicate, UserQuestionDialog
from src.llm.client import LLMClient
app = QApplication([])
manager = AppManager.__new__(AppManager)
manager.settings = {'llm_mode': 'local', 'enable_tool_calling': True, 'language': 'tr-TR'}
manager.comm = Communicate()
manager._play_notification_sound = Mock()
manager.comm.ask_question.connect(manager._ask_question_gui, Qt.ConnectionType.QueuedConnection)
client = LLMClient(manager.settings, question_callback=manager._question_callback)
calls = []
def post(*args, **kwargs):
    calls.append(kwargs['json'])
    if len(calls) == 1:
        content = 'Neyi kastettiğinizi belirtmediniz; hangi videoyu kastediyorsunuz?'
    else:
        assert 'Örnek video incelemesi' in kwargs['json']['messages'][-1]['content']
        content = 'Örnek video hakkında nihai yanıt.'
    return Mock(ok=True, json=lambda: {'choices': [{'message': {'content': content}}]})
listener = Mock(last_error=None)
listener.listen_and_transcribe.return_value = 'Örnek video incelemesi'
results = []
state = {'recorded': False, 'submitted': False}
def interact():
    current = app.activeModalWidget()
    if isinstance(current, UserQuestionDialog):
        if not state['recorded']:
            state['recorded'] = True
            current.microphone_button.click()
        elif not current._voice_active and current.answer_input.toPlainText():
            assert current.answer is None, 'must wait for explicit Send'
            assert current.answer_input.toPlainText() == 'Örnek video incelemesi'
            state['submitted'] = True
            current.send_button.click()
    if not results: QTimer.singleShot(10, interact)
with patch('requests.post', side_effect=post), patch('src.gui.app.AudioListener', return_value=listener):
    worker = threading.Thread(target=lambda: results.append(client.generate_response('system', 'Bu konuda ne düşünüyorsun?')))
    QTimer.singleShot(10, interact)
    worker.start()
    deadline = time.monotonic() + 5
    while worker.is_alive() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.001)
    worker.join(timeout=.1)
    assert not worker.is_alive()
assert state['submitted']
assert results == ['Örnek video hakkında nihai yanıt.'], results
assert client.last_tools_used == ['ask_user']
assert 'Örnek video incelemesi' in client.history[0]['content']
'''
        run = subprocess.run([sys.executable, "-c", code],
                             env=dict(os.environ, QT_QPA_PLATFORM="offscreen", PYNPUT_BACKEND="dummy"),
                             cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=12)
        self.assertEqual(run.returncode, 0, run.stderr)

    def test_icon_voice_insertion_manual_send_cancel_close_and_error(self):
        code = '''
import threading, time
from unittest.mock import Mock, patch
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QTextCursor
from src.gui.app import UserQuestionDialog
app = QApplication([])
def pump(predicate):
    end = time.monotonic() + 3
    while not predicate() and time.monotonic() < end:
        app.processEvents()
        time.sleep(.001)
    assert predicate(), 'worker timeout'
request = {'question': 'Hangi video?', 'options': ['Birinci', 'İkinci']}
main_thread = threading.get_ident()
threads = []
listener = Mock(last_error=None)
def transcribe(**kwargs):
    threads.append(threading.get_ident())
    kwargs['status_callback']('listening')
    kwargs['status_callback']('transcribing')
    return 'üçüncü video'
listener.listen_and_transcribe.side_effect = transcribe
with patch('src.gui.app.AudioListener', return_value=listener) as factory:
    d = UserQuestionDialog(request, settings={'language': 'tr-TR'})
    factory.assert_not_called()
    assert d.microphone_button.text() == ''
    assert not d.microphone_button.icon().isNull()
    d.answer_input.setPlainText('Benim yanıtım: ')
    d.answer_input.moveCursor(QTextCursor.MoveOperation.End)
    d.microphone_button.click()
    assert not d.send_button.isEnabled()
    pump(lambda: not d._voice_active)
    assert threads and threads[0] != main_thread
    assert d.answer_input.toPlainText() == 'Benim yanıtım: üçüncü video'
    assert d.answer is None and not d._closed, 'voice must not auto-submit'
    d.answer_input.setPlainText('Düzeltilmiş yanıt')
    d.send_button.click()
    assert d.answer == 'Düzeltilmiş yanıt'
    assert d._voice_cancel.is_set()
for close_dialog in (False, True):
    listener = Mock(last_error=None)
    started = threading.Event()
    def waiting(**kwargs):
        started.set()
        assert kwargs['cancel_event'].wait(2)
        return 'GEÇ YANIT EKLENMEMELİ'
    listener.listen_and_transcribe.side_effect = waiting
    with patch('src.gui.app.AudioListener', return_value=listener):
        d = UserQuestionDialog(request)
        d.answer_input.setPlainText('Korunacak metin')
        d.microphone_button.click()
        pump(started.is_set)
        assert not d.option_buttons[0].isEnabled()
        if close_dialog: d.reject()
        else: d.microphone_button.click()
        d._voice_thread.join(timeout=2)
        assert not d._voice_thread.is_alive()
        app.processEvents()
        assert d.answer_input.toPlainText() == 'Korunacak metin'
        assert d.answer is None
        if not close_dialog:
            pump(lambda: not d._voice_active)
            assert d.send_button.isEnabled()
            assert d.microphone_button.isEnabled()
listener = Mock(last_error='Mikrofon kullanılamadı')
listener.listen_and_transcribe.return_value = None
with patch('src.gui.app.AudioListener', return_value=listener):
    d = UserQuestionDialog(request)
    d.answer_input.setPlainText('Metin korunur')
    d.microphone_button.click()
    pump(lambda: not d._voice_active)
    assert d.voice_status_label.text() == 'Mikrofon kullanılamadı'
    assert d.answer_input.toPlainText() == 'Metin korunur'
    assert d.answer is None and d.send_button.isEnabled()
'''
        run = subprocess.run([sys.executable, "-c", code],
                             env=dict(os.environ, QT_QPA_PLATFORM="offscreen", PYNPUT_BACKEND="dummy"),
                             cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=15)
        self.assertEqual(run.returncode, 0, run.stderr)


if __name__ == '__main__':
    unittest.main()
