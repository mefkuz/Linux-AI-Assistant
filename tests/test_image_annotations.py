"""Synthetic annotation fixtures; no real desktop, credentials or uploads."""
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PyQt6.QtGui import QImage, QColor
from src.tools.image_annotations import (validate_annotations, encode_image, render_annotations,
    prepare_visual_answer, ocr_rows)
from src.tools.questions import VisualAnswer
from src.tools.executor import ToolExecutor
from src.llm.client import LLMClient
from src.llm.codex import CodexSession


MARK = {'image': 0, 'shape': 'circle', 'points': [.1,.2,.6,.8], 'label': 'burası'}


def fixture():
    image = QImage(400, 200, QImage.Format.Format_RGB32)
    image.fill(QColor('white'))
    return image, {'name': 'fixture.png', 'data': encode_image(image)}


def rows():
    return [{'page_num':'1', 'block_num':'1', 'par_num':'1', 'line_num':'1',
             'text':'Kaydet', 'left':'40', 'top':'50', 'width':'80', 'height':'20'}]


class AnnotationTests(unittest.TestCase):
    def test_annotations_are_bounded_non_executable_geometry(self):
        self.assertEqual(validate_annotations([MARK]), [MARK])
        for extra in ({'points':[0,0,float('nan'),1]}, {'points':[0,0,2,1]},
                      {'points':[0,0,0,1]}, {'image':-1}, {'shape':'python'}, {'label':'a'*81}):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                validate_annotations([{**MARK, **extra}])
        with self.assertRaises(ValueError):
            validate_annotations([MARK]*13)

    def test_render_works_on_a_copy_and_preserves_original(self):
        image, original = fixture()
        for shape in ('circle','box','arrow'):
            rendered = render_annotations(image, [{**MARK, 'shape':shape}])
            self.assertNotEqual(encode_image(rendered), original['data'])
            self.assertEqual(encode_image(image), original['data'])

    def test_region_ocr_is_private_temporary_and_uses_crop(self):
        image, _ = fixture()
        paths = []
        def run(command, **kwargs):
            paths.append(Path(command[1]))
            self.assertTrue(paths[-1].is_file())
            return Mock(returncode=0, stdout='text\tleft\ttop\twidth\theight\nKaydet\t0\t0\t10\t10\n')
        with patch('subprocess.run', side_effect=run):
            self.assertEqual(ocr_rows(image)[0]['text'], 'Kaydet')
        self.assertFalse(paths[0].parent.exists())

    def test_text_mode_sends_regions_and_ocr_without_image_bytes(self):
        _, attachment = fixture()
        answer = VisualAnswer('Burası', [attachment], [MARK])
        with patch('src.tools.image_annotations.ocr_rows', return_value=rows()) as read:
            text, images = prepare_visual_answer(answer)
            self.assertEqual(prepare_visual_answer(answer), (text,images))
        read.assert_called_once()
        self.assertIn('Kaydet', text)
        self.assertIn('0.1', text)
        self.assertEqual(images, [])
        self.assertNotIn('base64', text)
        self.assertLess(read.call_args.args[0].width(), 400)

    def test_vision_mode_sends_only_explicitly_selected_copy(self):
        _, attachment = fixture()
        answer = VisualAnswer('Burası', [attachment], [MARK], True)
        with patch('src.tools.image_annotations.ocr_rows', return_value=rows()):
            text, images = prepare_visual_answer(answer)
        self.assertEqual(len(images), 1)
        data = base64.b64decode(images[0].split(',',1)[1])
        self.assertNotEqual(data, attachment['data'])
        self.assertNotIn('base64', text)
        ex = ToolExecutor(question_callback=lambda request: answer)
        result = ex.execute_tool('ask_user', {'question':'Neresi?'})
        self.assertEqual(ex.pending_answer_images, images)
        self.assertNotIn('base64', result)
        ex.question_callback = lambda request: None
        ex.execute_tool('ask_user', {'question':'Neresi?'})
        self.assertEqual(ex.pending_answer_images, [])

    def test_api_vision_and_text_modes_preserve_tool_protocol_and_memory(self):
        for send_images in (False, True):
            for plain_question in (False, True):
                with self.subTest(vision=send_images, plain=plain_question):
                    _, attachment = fixture()
                    answer = VisualAnswer('Burası', [attachment], [MARK], send_images)
                    client = LLMClient({'llm_mode':'local','enable_tool_calling':True}, question_callback=lambda req: answer)
                    seen = []
                    def post(*args, **kwargs):
                        seen.append(json.loads(json.dumps(kwargs['json'])))
                        if len(seen)==1:
                            message = {'content':'Neyi kastediyorsunuz?'} if plain_question else {'tool_calls':[
                                {'id':'q','type':'function','function':{'name':'ask_user','arguments':'{"question":"Neresi?"}'}}]}
                        else:
                            message = {'content':'Kaydet düğmesini anladım.'}
                        return Mock(ok=True,json=lambda:{'choices':[{'message':message}]})
                    with patch('requests.post',side_effect=post), patch('src.tools.image_annotations.ocr_rows',return_value=rows()):
                        self.assertIn('anladım',client.generate_response('system','Do task'))
                    wire = json.dumps(seen[-1])
                    self.assertEqual('image_url' in wire, send_images)
                    self.assertIn('Kaydet', wire)
                    self.assertNotIn('base64',json.dumps(client.history))
                    self.assertIn('Kaydet',client.history[0]['content'])
                    if send_images:
                        self.assertEqual(seen[-1]['messages'][-1]['role'],'user')

    def test_api_nonvision_server_can_fall_back_to_ocr_without_reasking(self):
        _, attachment = fixture()
        callback = Mock(return_value=VisualAnswer('Burası',[attachment],[MARK],True))
        client = LLMClient({'llm_mode':'local','enable_tool_calling':True},question_callback=callback)
        seen = []
        def post(*args,**kwargs):
            payload = json.loads(json.dumps(kwargs['json'])); seen.append(payload)
            if len(seen)==1:
                msg = {'tool_calls':[{'id':'q','type':'function','function':{'name':'ask_user','arguments':'{"question":"Q?"}'}}]}
                return Mock(ok=True,json=lambda:{'choices':[{'message':msg}]})
            if len(seen)==2:
                return Mock(ok=False,status_code=400,text='image input is not supported')
            self.assertNotIn('image_url',json.dumps(payload))
            return Mock(ok=True,json=lambda:{'choices':[{'message':{'content':'OCR ile anlaşıldı.'}}]})
        with patch('requests.post',side_effect=post), patch('src.tools.image_annotations.ocr_rows',return_value=rows()):
            self.assertIn('anlaşıldı',client.generate_response('system','Do task'))
        callback.assert_called_once()

    def test_codex_native_and_plain_clarification_deliver_images_on_correct_channel(self):
        for plain in (False,True):
            with self.subTest(plain=plain), tempfile.TemporaryDirectory() as workspace:
                _, attachment = fixture()
                session = CodexSession(workspace); session.send=Mock();session.deadline=float('inf')
                events=[{'id':1,'result':{'config':{}}},{'id':2,'result':{'thread':{'id':'t'}}},{'id':3,'result':{}}]
                if plain:
                    events += [{'method':'item/completed','params':{'item':{'type':'agentMessage','text':'Neyi kastediyorsunuz?'}}},
                               {'method':'turn/completed','params':{'turn':{'status':'completed'}}},{'id':4,'result':{}}]
                else:
                    events += [{'id':'q','method':'item/tool/call','params':{'tool':'ask_user','arguments':{'question':'Q?'}}}]
                events += [{'method':'item/completed','params':{'item':{'type':'agentMessage','text':'Anlaşıldı.'}}},
                           {'method':'turn/completed','params':{'turn':{'status':'completed'}}}]
                for event in events:session.events.put(event)
                client=LLMClient({'llm_mode':'cli','llm_cli_tool_key':'codex (OpenAI)','workspace_dir':workspace,'enable_tool_calling':True},
                                 question_callback=lambda req:VisualAnswer('Burası',[attachment],[MARK],True))
                with patch('src.llm.codex.CodexSession') as factory, patch('src.tools.image_annotations.ocr_rows',return_value=rows()):
                    factory.return_value.__enter__.return_value=session
                    self.assertEqual(client.generate_response('system','Do task'),'Anlaşıldı.')
                if plain:
                    turns=[c.args[0]['params'] for c in session.send.call_args_list if c.args[0].get('method')=='turn/start']
                    self.assertEqual(turns[-1]['input'][-1]['type'],'image')
                    self.assertTrue(turns[-1]['input'][-1]['url'].startswith('data:image/'))
                else:
                    result=next(c.args[0]['result'] for c in session.send.call_args_list if c.args[0].get('id')=='q')
                    self.assertEqual(result['contentItems'][1]['type'],'inputImage')
                self.assertNotIn('base64',json.dumps(client.history))

    def test_canvas_geometry_editor_cancel_and_manual_send(self):
        code = '''
from unittest.mock import patch
from PyQt6.QtCore import Qt,QPoint,QTimer
from PyQt6.QtGui import QImage,QColor
from PyQt6.QtWidgets import QApplication,QDialog
from PyQt6.QtTest import QTest
from src.gui.image_editor import AnnotationCanvas,ImageAnnotationDialog
from src.gui.app import UserQuestionDialog
from src.tools.questions import VisualAnswer
from src.tools.image_annotations import encode_image
app=QApplication([])
image=QImage(400,200,QImage.Format.Format_RGB32);image.fill(QColor('white'))
original=encode_image(image)
canvas=AnnotationCanvas(image);canvas.resize(600,600);canvas.show();app.processEvents()
rect=canvas.image_rect()
a=QPoint(round(rect.left()+rect.width()*.2),round(rect.top()+rect.height()*.3))
b=QPoint(round(rect.left()+rect.width()*.7),round(rect.top()+rect.height()*.8))
QTest.mousePress(canvas,Qt.MouseButton.LeftButton,pos=a)
QTest.mouseMove(canvas,b)
QTest.mouseRelease(canvas,Qt.MouseButton.LeftButton,pos=b)
assert len(canvas.marks)==1
assert all(abs(x-y)<.01 for x,y in zip(canvas.marks[0]['points'],[.2,.3,.7,.8]))
saved=list(canvas.marks)
canvas.resize(900,400);app.processEvents()
assert canvas.marks==saved
assert encode_image(canvas.original)==original
canvas.undo();assert canvas.marks==[]
canvas.marks=saved;canvas.clear();assert canvas.marks==[]
d=UserQuestionDialog({'question':'Neresi?','options':['A','B'],'attachments':[{'name':'fixture','data':original}]})
def edit_mark():
    current=app.activeModalWidget()
    assert isinstance(current,ImageAnnotationDialog)
    from PyQt6.QtWidgets import QLabel
    assert any('görsel destekleyen' in w.text() for w in current.findChildren(QLabel))
    current.canvas.marks=saved
    current.accept()
QTimer.singleShot(0,edit_mark);d._edit_image(0)
assert d._user_marks[0]==saved and d.answer is None and not d._closed
d.option_buttons[1].click();d.answer_input.setPlainText('burası')
assert d.answer is None and not hasattr(d, 'send_images_check') and not hasattr(d, 'mode_note')
d.send_button.click()
assert isinstance(d.answer,VisualAnswer) and d.answer.send_images
assert 'B' in d.answer.text and d.answer.marks
assert d.answer.attachments[0]['data']==original
d=UserQuestionDialog({'question':'Q?','attachments':[{'name':'fixture','data':original}]})
d._user_marks[0]=saved;d.reject()
assert d.answer is None
d=UserQuestionDialog({'question':'Q?'})
assert not hasattr(d, 'add_image_button') and not hasattr(d, 'add_screen_button')
assert not hasattr(d, '_add_local_image') and not hasattr(d, '_capture_reply_image')
assert not d._attachments and not d.send_button.isEnabled()
'''
        result=subprocess.run([sys.executable,'-c',code],env=dict(os.environ,QT_QPA_PLATFORM='offscreen',PYNPUT_BACKEND='dummy'),
                              cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stderr)


if __name__=='__main__':unittest.main()
