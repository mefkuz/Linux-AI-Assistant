"""Bounded local annotation rendering and same-image OCR; no executable inputs."""
import base64
import csv
import io
import json
import math
import subprocess
import tempfile
from pathlib import Path

from PyQt6.QtCore import QBuffer, QIODevice, QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPolygonF, QGuiApplication
from src.tools.question_images import decode_image, MAX_IMAGE_BYTES

MAX_MARKS = 12


def validate_annotations(marks):
    if not isinstance(marks, list) or len(marks) > MAX_MARKS:
        raise ValueError('At most 12 annotations are allowed')
    output = []
    for mark in marks:
        if not isinstance(mark, dict):
            raise ValueError('Invalid annotation')
        index = mark.get('image', 0)
        shape = mark.get('shape', 'circle')
        label = mark.get('label', '')
        if type(index) is not int or not 0 <= index < 4 or shape not in ('circle', 'box', 'arrow'):
            raise ValueError('Invalid image index or annotation shape')
        if not isinstance(label, str) or len(label) > 80:
            raise ValueError('Annotation label is too long')
        result = {'image': index, 'shape': shape, 'label': label}
        points = mark.get('points')
        if (not isinstance(points, (list, tuple)) or len(points) != 4 or
                any(type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 1 for v in points)):
            raise ValueError('Annotation points must be four finite values in [0,1]')
        if points[0] == points[2] and points[1] == points[3]:
            raise ValueError('Annotation must have an area or direction')
        if shape != 'arrow' and (points[0] == points[2] or points[1] == points[3]):
            raise ValueError('Annotation area is empty')
        result['points'] = list(points)
        output.append(result)
    return output


def encode_image(image):
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    if not image.save(buffer, 'PNG') or len(buffer.data()) > MAX_IMAGE_BYTES:
        raise ValueError('İşaretli görsel 8 MB sınırını aşıyor.')
    return bytes(buffer.data())


def render_annotations(image, marks, color='#0088d1'):
    result = image.copy()
    result.setDevicePixelRatio(1)
    painter = QPainter(result)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    width, height = result.width(), result.height()
    pen_width = max(2, round(max(width, height) / 350))
    painter.setPen(QPen(QColor(color), pen_width))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    has_fonts = QGuiApplication.instance() is not None
    if has_fonts:
        painter.setFont(QFont('Sans', max(9, round(max(width, height) / 90))))
    try:
        for number, mark in enumerate(validate_annotations(marks), 1):
            x1, y1, x2, y2 = mark['points']
            a, b = QPointF(x1 * width, y1 * height), QPointF(x2 * width, y2 * height)
            rect = QRectF(a, b).normalized()
            if mark['shape'] == 'circle':
                painter.drawEllipse(rect)
            elif mark['shape'] == 'box':
                painter.drawRect(rect)
            else:
                painter.drawLine(a, b)
                angle = math.atan2(b.y()-a.y(), b.x()-a.x())
                length = max(10, pen_width * 4)
                painter.drawPolyline(QPolygonF([
                    QPointF(b.x()-length*math.cos(angle-.5), b.y()-length*math.sin(angle-.5)), b,
                    QPointF(b.x()-length*math.cos(angle+.5), b.y()-length*math.sin(angle+.5))]))
            text = str(number) + (': ' + mark['label'] if mark['label'] else '')
            if has_fonts:
                painter.drawText(QPointF(max(2, rect.left()), max(painter.fontMetrics().height(), rect.top()-4)), text)
    finally:
        painter.end()
    return result


def ocr_rows(image):
    # Use the exact decoded image, never a second desktop capture.
    with tempfile.TemporaryDirectory(prefix='linux-ai-region-') as directory:
        path = Path(directory, 'region.png')
        if not image.save(str(path), 'PNG'):
            raise ValueError('Görsel OCR için hazırlanamadı.')
        process = subprocess.run(['tesseract', str(path), 'stdout', '-l', 'tur+eng', '--psm', '11', 'tsv'],
                                 capture_output=True, text=True, timeout=20)
        if process.returncode:
            raise ValueError('OCR işlemi başarısız oldu.')
        return list(csv.DictReader(io.StringIO(process.stdout), delimiter='\t'))


def region_crop(image, mark):
    x1,y1,x2,y2 = mark['points']
    if mark['shape'] == 'arrow':
        x1,y1,x2,y2 = max(0,x2-.05),max(0,y2-.05),min(1,x2+.05),min(1,y2+.05)
    left, top = int(min(x1,x2)*image.width()), int(min(y1,y2)*image.height())
    right, bottom = math.ceil(max(x1,x2)*image.width()), math.ceil(max(y1,y2)*image.height())
    return image.copy(left, top, max(1,right-left), max(1,bottom-top))


def prepare_visual_answer(answer):
    if answer._prepared is not None:
        return answer._prepared
    if not isinstance(answer.text, str) or type(answer.send_images) is not bool or len(answer.attachments) > 4:
        raise ValueError('Invalid visual answer')
    marks = validate_annotations(answer.marks)
    if any(m['image'] >= len(answer.attachments) for m in marks):
        raise ValueError('Unknown annotation image')
    decoded = [decode_image(a['data']) for a in answer.attachments]
    notes, images = [], []
    for index, image in enumerate(decoded):
        current = [m for m in marks if m['image'] == index]
        for mark in current:
            try:
                rows = ocr_rows(region_crop(image, mark))
                text = ' '.join(r['text'].strip() for r in rows if r.get('text', '').strip())[:1500]
            except (OSError, ValueError, subprocess.TimeoutExpired):
                text = ''
            notes.append(json.dumps({'image': index+1, 'shape': mark['shape'], 'region': mark['points'],
                                     'label': mark['label'], 'text': text or '(OCR ile metin bulunamadı)'}, ensure_ascii=False))
        if answer.send_images:
            shown = render_annotations(image, current) if current else image
            data = encode_image(shown)
            images.append('data:image/png;base64,' + base64.b64encode(data).decode('ascii'))
    text = answer.text.strip()
    if notes:
        text += '\n\n[İşaretlenen bölgeler — görsele göre normalize konumlar; otomatik tıklama talimatı değildir]:\n' + '\n'.join(notes)
    if images:
        text += '\n[İşaretli görseller kullanıcı Gönder seçimiyle eklendi; mavi işaretler kullanıcıya aittir.]'
    elif notes:
        text += '\n[Görsel gönderilmedi; yalnızca bölge konumu ve OCR metni iletildi. Yazısız ikonların görünümü bilinmiyor.]'
    answer._prepared = (text.strip(), images)
    return answer._prepared
