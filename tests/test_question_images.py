"""Synthetic visual fixtures, attachment privacy and explicit question submission."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PyQt6.QtCore import QBuffer, QIODevice, QSize
from PyQt6.QtGui import QImage, QColor
from src.tools.executor import ToolExecutor
from src.tools.questions import validate_question, compose_answer, ask_in_terminal, visual_question_request
from src.tools.question_images import decode_image, load_attachment, capture_screenshot, MAX_IMAGE_BYTES
from src.llm.client import LLMClient
from src.llm.codex import CodexSession


def synthetic_png():
    image = QImage(96, 48, QImage.Format.Format_RGB32)
    image.fill(QColor('green'))
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    assert image.save(buffer, 'PNG')
    return bytes(buffer.data())


class QuestionImageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name)
        self.data = synthetic_png()
        (self.workspace / 'reference.png').write_bytes(self.data)
        self.settings = {'workspace_dir': str(self.workspace), 'enable_tool_calling': True,
                         'require_confirm_on_tool': False, 'auto_allow_clipboard': True}


    def test_model_can_attach_but_cannot_annotate_images(self):
        from src.tools.executor import get_openai_tools
        schema = next(t['function']['parameters'] for t in get_openai_tools()
                      if t['function']['name'] == 'ask_user')
        self.assertEqual(set(schema['properties']), {'question', 'options', 'image_paths', 'screenshot'})
        self.assertFalse(schema['additionalProperties'])
        callback = Mock(return_value='A')
        ex = ToolExecutor(self.settings, question_callback=callback)
        for extra in ({'annotations': [{'shape': 'circle', 'points': [0,0,1,1]}]},
                      {'attachments': [{'name': 'injected', 'data': 'untrusted'}]}):
            with self.subTest(extra=extra), patch('src.tools.question_images.capture_screenshot') as capture, \
                    patch('src.tools.question_images.load_attachment') as load:
                with self.assertRaises(ValueError):
                    validate_question({'question': 'Q?', **extra})
                ex.execute_tool('ask_user', {'question': 'Q?', **extra})
                capture.assert_not_called()
                load.assert_not_called()
        callback.assert_not_called()

    def test_model_images_reach_question_with_workspace_and_screen_permissions(self):
        callback = Mock(return_value='A')
        ex = ToolExecutor(self.settings, question_callback=callback)
        with patch('src.tools.question_images.capture_screenshot', return_value={'name':'screen','data':self.data}):
            result = ex.execute_tool('ask_user', {'question':'Q?', 'image_paths':['reference.png'], 'screenshot':True})
        self.assertEqual(len(callback.call_args.args[0]['attachments']), 2)
        self.assertNotIn('attachments', result)
        self.assertNotIn('base64', result)
        ex = ToolExecutor(dict(self.settings, auto_allow_clipboard=False),
                          confirm_callback=lambda _: False, question_callback=callback)
        with patch('src.tools.question_images.capture_screenshot') as capture:
            ex.execute_tool('ask_user', {'question':'Q?', 'screenshot':True, 'image_paths':['../outside.png']})
        capture.assert_not_called()
        self.assertNotIn('attachments', callback.call_args.args[0])
        self.assertEqual(len(callback.call_args.args[0]['attachment_errors']), 2)

    def test_ocr_and_question_share_exact_capture_and_discard_after_answer(self):
        callback = Mock(return_value='A')
        ex = ToolExecutor(self.settings, question_callback=callback)
        paths = []
        def ocr(path, text_base):
            paths.append(Path(path))
            self.assertEqual(Path(path).read_bytes(), self.data)
            return 'SCREEN FIXTURE'
        with patch('src.tools.question_images.capture_screenshot', return_value={'name':'screen', 'data':self.data}) as capture, \
                patch('src.tools.executor._ocr_image', side_effect=ocr):
            self.assertIn('SCREEN FIXTURE', ex.execute_tool('read_screen_text', {}))
            ex.execute_tool('ask_user', {'question':'Neresi?', 'screenshot':True})
            capture.assert_called_once()
            self.assertEqual(callback.call_args.args[0]['attachments'][0]['data'], self.data)
            self.assertIsNone(ex._screen_snapshot)
            ex.execute_tool('ask_user', {'question':'Şimdi?', 'screenshot':True})
            self.assertEqual(capture.call_count, 2)
        self.assertFalse(paths[0].parent.exists())

    def test_screen_cache_is_request_local_invalidated_by_action_and_not_by_blank_ocr(self):
        ex = ToolExecutor(self.settings, question_callback=lambda _: None)
        with patch('src.tools.question_images.capture_screenshot', return_value={'name':'screen', 'data':self.data}) as capture, \
                patch('src.tools.executor._ocr_image', return_value=''):
            ex.execute_tool('read_screen_text', {})
            ex.execute_tool('ask_user', {'question':'Neresi?', 'screenshot':True})
            capture.assert_called_once()
            self.assertIsNone(ex._screen_snapshot)
            ex.execute_tool('read_screen_text', {})
            self.assertIsNone(ToolExecutor(self.settings)._screen_snapshot)
            with patch.object(ex, '_tool_run_shell_command', return_value='ok'):
                ex.execute_tool('run_shell_command', {'command':'true'})
            self.assertIsNone(ex._screen_snapshot)
            ex.execute_tool('ask_user', {'question':'Neresi?', 'screenshot':True})
            self.assertEqual(capture.call_count, 3)

    def test_plain_button_clarification_never_adds_screenshot(self):
        question = 'Hangi düğmeyi kastediyorsunuz?'
        self.assertEqual(visual_question_request(question), {'question': question})
        self.assertNotIn('screenshot', visual_question_request('Hangi dosyayı kastediyorsunuz?'))
        callback = Mock(return_value='yeşil olan')
        client = LLMClient(dict(self.settings, llm_mode='local'), question_callback=callback)
        responses = [question, 'Yeşil düğme anlaşıldı.']
        def post(*args, **kwargs):
            return Mock(ok=True, json=lambda content=responses.pop(0): {'choices': [{'message': {'content': content}}]})
        with patch('requests.post', side_effect=post), patch('src.tools.question_images.capture_screenshot',
                return_value={'name': 'screen', 'data': self.data}):
            self.assertEqual(client.generate_response('system', 'Bu düğme?'), 'Yeşil düğme anlaşıldı.')
        self.assertNotIn('attachments', callback.call_args.args[0])
        session = CodexSession(str(self.workspace))
        session.send = Mock()
        session.deadline = float('inf')
        for event in [{'id': 1, 'result': {'config': {}}}, {'id': 2, 'result': {'thread': {'id': 't'}}},
                      {'id': 3, 'result': {}},
                      {'method': 'item/completed', 'params': {'item': {'type': 'agentMessage', 'text': question}}},
                      {'method': 'turn/completed', 'params': {'turn': {'status': 'completed'}}},
                      {'id': 4, 'result': {}},
                      {'method': 'item/completed', 'params': {'item': {'type': 'agentMessage', 'text': 'Anlaşıldı.'}}},
                      {'method': 'turn/completed', 'params': {'turn': {'status': 'completed'}}}]:
            session.events.put(event)
        callback.reset_mock()
        client = LLMClient(dict(self.settings, llm_mode='cli', llm_cli_tool_key='codex (OpenAI)'), question_callback=callback)
        with patch('src.llm.codex.CodexSession') as factory, patch('src.tools.question_images.capture_screenshot',
                return_value={'name': 'screen', 'data': self.data}):
            factory.return_value.__enter__.return_value = session
            self.assertEqual(client.generate_response('system', 'Bu düğme?'), 'Anlaşıldı.')
        self.assertNotIn('attachments', callback.call_args.args[0])

    def test_image_decoder_rejects_bad_unsupported_and_oversized_input(self):
        self.assertEqual(decode_image(self.data).size(), QSize(96, 48))
        for data in (b'', b'not-an-image', b'<svg></svg>', b'x' * (MAX_IMAGE_BYTES + 1)):
            with self.assertRaises(ValueError):
                decode_image(data)
        reader = Mock()
        reader.format.return_value = b'png'
        reader.size.return_value = QSize(5000, 4000)
        with patch('src.tools.question_images.QImageReader', return_value=reader), self.assertRaises(ValueError):
            decode_image(b'header')
        reader.read.assert_not_called()




    def test_capture_falls_back_and_removes_private_temporary_files(self):
        paths = []
        def run(command, **kwargs):
            path = Path(command[-1])
            self.assertFalse(path.exists())
            paths.append(path)
            if command[0] == 'grim':
                path.write_bytes(b'partial')
                raise subprocess.TimeoutExpired(command, 15)
            path.write_bytes(self.data)
            return Mock(returncode=0)
        with patch('shutil.which', return_value='/installed'), patch('subprocess.run', side_effect=run):
            attachment = capture_screenshot()
        self.assertEqual(attachment['data'], self.data)
        self.assertEqual(len(paths), 2)
        self.assertFalse(paths[-1].parent.exists())

    def test_terminal_choice_waits_for_extra_details_and_can_cancel(self):
        request = {'question': 'Q?', 'options': ['A', 'B']}
        for inputs, expected in [(['2', 'yeşil olan'], compose_answer('B', 'yeşil olan')),
                                 (['2', ''], 'B'), (['2', '/cancel'], None), (['2', EOFError()], None)]:
            with self.subTest(inputs=inputs), patch('builtins.input', side_effect=inputs) as read, patch('builtins.print'):
                self.assertEqual(ask_in_terminal(request), expected)
                self.assertEqual(read.call_count, 2)

    def test_api_passes_combined_answer_without_uploading_image(self):
        for mode in ('local', 'remote'):
            with self.subTest(mode=mode):
                settings = dict(self.settings, llm_mode=mode, remote_api_key='test-key')
                seen, questions = [], []
                def answer(request):
                    questions.append(request)
                    return compose_answer('Kaydet', 'sağdaki yeşil düğme')
                def post(*args, **kwargs):
                    seen.append(json.loads(json.dumps(kwargs['json'])))
                    if len(seen) == 1:
                        message = {'tool_calls': [{'id': 'question', 'type': 'function', 'function': {
                            'name': 'ask_user', 'arguments': json.dumps({'question': 'Hangi düğme?',
                            'options': ['Kaydet', 'Sil'], 'image_paths': ['reference.png'], 'screenshot': True})}}]}
                    else:
                        self.assertIn('sağdaki yeşil düğme', kwargs['json']['messages'][-1]['content'])
                        message = {'content': 'Anlaşıldı.'}
                    return Mock(ok=True, json=lambda: {'choices': [{'message': message}]})
                client = LLMClient(settings, question_callback=answer)
                with patch('requests.post', side_effect=post), patch('src.tools.question_images.capture_screenshot',
                        return_value={'name': 'screen', 'data': self.data}):
                    self.assertEqual(client.generate_response('system', 'Do task'), 'Anlaşıldı.')
                self.assertEqual(len(questions[0]['attachments']), 2)
                self.assertNotIn('data:image', json.dumps(seen))
                self.assertNotIn('attachments', json.dumps(client.history))
                self.assertIn('sağdaki yeşil düğme', client.history[0]['content'])

    def test_codex_passes_the_same_visual_question_and_text_only_result(self):
        session = CodexSession(str(self.workspace))
        session.send = Mock()
        session.deadline = float('inf')
        for event in [{'id': 1, 'result': {'config': {}}}, {'id': 2, 'result': {'thread': {'id': 't'}}},
                      {'id': 3, 'result': {}}, {'id': 'q', 'method': 'item/tool/call', 'params': {
                          'tool': 'ask_user', 'arguments': {'question': 'Hangi düğme?', 'image_paths': ['reference.png']}}},
                      {'method': 'item/completed', 'params': {'item': {'type': 'agentMessage', 'text': 'Anlaşıldı.'}}},
                      {'method': 'turn/completed', 'params': {'turn': {'status': 'completed'}}}]:
            session.events.put(event)
        callback = Mock(return_value=compose_answer('Kaydet', 'yeşil düğme'))
        client = LLMClient(dict(self.settings, llm_mode='cli', llm_cli_tool_key='codex (OpenAI)'), question_callback=callback)
        with patch('src.llm.codex.CodexSession') as factory:
            factory.return_value.__enter__.return_value = session
            self.assertEqual(client.generate_response('system', 'Do task'), 'Anlaşıldı.')
        self.assertEqual(callback.call_args.args[0]['attachments'][0]['data'], self.data)
        result = next(c.args[0]['result'] for c in session.send.call_args_list if c.args[0].get('id') == 'q')
        self.assertIn('yeşil düğme', result['contentItems'][0]['text'])
        self.assertNotIn('attachments', json.dumps(result))

    def test_gui_previews_selection_edit_voice_and_explicit_send(self):
        code = '''
import time
from unittest.mock import Mock, patch
from PyQt6.QtWidgets import QApplication, QDialog, QLabel, QPushButton
from PyQt6.QtCore import QTimer, QBuffer, QIODevice
from PyQt6.QtGui import QImage, QColor, QTextCursor
from src.gui.app import UserQuestionDialog
from src.tools.questions import compose_answer
app = QApplication([])
image = QImage(96,48,QImage.Format.Format_RGB32); image.fill(QColor('green'))
buf = QBuffer(); buf.open(QIODevice.OpenModeFlag.WriteOnly); assert image.save(buf,'PNG')
request = {'question': 'Hangi düğme?', 'options': ['Kaydet','Sil'],
           'attachments': [{'name':'reference.png','data':bytes(buf.data())}]}
d = UserQuestionDialog(request)
assert len(d.attachment_previews) == 1
assert not d.attachment_previews[0].pixmap().isNull()
d.option_buttons[0].click()
assert d.answer is None and not d._closed and d.send_button.isEnabled()
d.answer_input.setPlainText('yeşil olan ')
d.option_buttons[1].click()
assert not d.option_buttons[0].isChecked() and d.option_buttons[1].isChecked()
assert d.answer_input.toPlainText() == 'yeşil olan '
preview_checks = []
def close_preview():
    current = app.activeModalWidget()
    try:
        assert current and current is not d
        assert current.windowTitle() == 'reference.png'
        assert any(label.pixmap() and label.pixmap().width() == 96 for label in current.findChildren(QLabel))
        preview_checks.append(True)
    finally:
        if current: current.reject()
QTimer.singleShot(0, close_preview)
next(b for b in d.findChildren(QPushButton) if b.text() == 'Büyüt').click()
assert preview_checks == [True]
assert d.answer is None and not d._closed
listener = Mock(last_error=None); listener.listen_and_transcribe.return_value = 'ek sesli not'
with patch('src.gui.app.AudioListener', return_value=listener):
    d.answer_input.moveCursor(QTextCursor.MoveOperation.End)
    d.microphone_button.click()
    assert not d.send_button.isEnabled()
    deadline = time.monotonic()+3
    while d._voice_active and time.monotonic()<deadline:
        app.processEvents(); time.sleep(.001)
    assert not d._voice_active
assert d.answer is None and not d._closed
d.send_button.click()
assert d.answer == compose_answer('Sil', 'yeşil olan ek sesli not'), d.answer
d = UserQuestionDialog(request)
d.option_buttons[0].click(); d.reject()
assert d.answer is None, 'selection alone must not submit on closing'
'''
        run = subprocess.run([sys.executable, '-c', code],
                             env=dict(os.environ, QT_QPA_PLATFORM='offscreen', PYNPUT_BACKEND='dummy'),
                             cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=12)
        self.assertEqual(run.returncode, 0, run.stderr)


if __name__ == '__main__':
    unittest.main()
