"""Opt-in real-provider annotation test; uploads only a synthetic fixture."""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from PyQt6.QtGui import QGuiApplication, QImage, QPainter, QFont, QColor
from src.core.settings import SettingsManager
from src.llm.client import LLMClient
from src.tools.image_annotations import encode_image
from src.tools.questions import VisualAnswer


def verify(provider, vision):
    image = QImage(600, 240, QImage.Format.Format_RGB32)
    image.fill(QColor('white'))
    painter = QPainter(image)
    painter.setPen(QColor('black'))
    painter.setFont(QFont('Sans', 28))
    painter.drawText(55, 130, 'SAVE 742')
    painter.drawText(355, 130, 'DELETE')
    painter.end()
    data = encode_image(image)
    points = [.05, .25, .44, .72]
    with tempfile.TemporaryDirectory(prefix='linux-ai-mark-live-') as workspace:
        Path(workspace, 'reference.png').write_bytes(data)
        settings = SettingsManager().settings.copy()
        settings.update(workspace_dir=workspace, enable_tool_calling=True)
        if provider == 'codex':
            settings.update(llm_mode='cli', llm_cli_tool_key='codex (OpenAI)', llm_cli_model='', llm_cli_extra_args='')
        else:
            settings['llm_mode'] = 'remote'
        questions = []
        def answer(request):
            questions.append(request)
            assert request['attachments'][0]['data'] == data
            return VisualAnswer('Burası. İşaretlediğim düğmenin metnini söyle.', request['attachments'],
                                [{'image': 0, 'shape': 'circle', 'label': 'burası', 'points': points}], vision)
        client = LLMClient(settings, question_callback=answer)
        reply = client.generate_response(
            'Bu bir soru aracı testi. Yalnızca istenen aracı kullan, dosya değiştirme.',
            'ask_user ile "Hangi düğme?" sorusunu sor. image_paths=["reference.png"] kullan. İşaret ekleme. '
            'Yanıttan sonra sadece kullanıcının mavi işaretlediği düğmenin metnini yaz. Başka araç kullanma.')
        assert client.last_error is None, client.last_error
        assert len(questions) == 1, len(questions)
        assert client.last_tools_used == ['ask_user'], client.last_tools_used
        assert '742' in reply and 'SAVE' in reply.upper(), reply
        memory = json.dumps(client.history)
        assert 'data:image' not in memory and 'base64' not in memory
        assert Path(workspace, 'reference.png').read_bytes() == data
        print(json.dumps({'provider': provider, 'mode': 'vision' if vision else 'OCR',
                          'annotation_answer': 'PASS', 'original_and_history': 'PASS'}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--provider', choices=['both', 'codex', 'remote'], default='both')
    args = parser.parse_args()
    app = QGuiApplication([])
    for provider in (['codex', 'remote'] if args.provider == 'both' else [args.provider]):
        for vision in (False, True):
            verify(provider, vision)
