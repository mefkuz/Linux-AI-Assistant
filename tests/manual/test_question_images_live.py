"""Opt-in API/Codex visual-question test using synthetic images only."""
import argparse
import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from PyQt6.QtCore import QBuffer, QIODevice
from PyQt6.QtGui import QImage, QColor
from src.core.settings import SettingsManager
from src.llm.client import LLMClient
from src.tools.questions import compose_answer


def verify(provider):
    image = QImage(96, 48, QImage.Format.Format_RGB32)
    image.fill(QColor('green'))
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    assert image.save(buffer, 'PNG')
    data = bytes(buffer.data())
    with tempfile.TemporaryDirectory(prefix='linux-ai-image-live-') as workspace:
        Path(workspace, 'reference.png').write_bytes(data)
        settings = SettingsManager().settings.copy()
        settings.update(workspace_dir=workspace, enable_tool_calling=True, auto_allow_clipboard=True)
        if provider == 'codex':
            settings.update(llm_mode='cli', llm_cli_tool_key='codex (OpenAI)', llm_cli_model='', llm_cli_extra_args='')
        else:
            settings['llm_mode'] = 'remote'
        questions = []
        def answer(request):
            questions.append(request)
            assert len(request['attachments']) == 2
            assert all(a['data'] == data for a in request['attachments'])
            assert request['options'] == ['Kaydet', 'Sil']
            return compose_answer('Kaydet', 'Yalnızca yeşil düğme')
        client = LLMClient(settings, question_callback=answer)
        with patch('src.tools.question_images.capture_screenshot', return_value={'name': 'screen fixture', 'data': data}):
            reply = client.generate_response(
                'Bu bir soru aracı testi. Sadece istenen soruyu sor; başka işlem yapma.',
                'ask_user ile "Hangi düğme?" sorusunu sor. options tam olarak ["Kaydet", "Sil"], '
                'image_paths=["reference.png"] ve screenshot=true kullan. '
                'Yanıtı aldıktan sonra seçimi ve ek açıklamayı Türkçe özetle. Başka araç kullanma.')
        assert client.last_error is None, client.last_error
        assert len(questions) == 1, len(questions)
        assert client.last_tools_used == ['ask_user'], client.last_tools_used
        assert 'Kaydet' in reply and 'yeşil' in reply, reply
        assert 'attachments' not in json.dumps(client.history)
        assert 'data:image' not in json.dumps(client.history)
        print(json.dumps({'provider': provider, 'visual_question': 'PASS',
                          'choice_plus_details': 'PASS', 'text_only_history': 'PASS'}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--provider', choices=['both', 'codex', 'remote'], default='both')
    provider = parser.parse_args().provider
    for name in (['codex', 'remote'] if provider == 'both' else [provider]):
        verify(name)
