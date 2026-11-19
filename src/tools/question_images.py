"""Private, display-only question attachments; never fetch remote images."""
from pathlib import Path
import shutil
import subprocess
import tempfile

from PyQt6.QtCore import QBuffer, QByteArray, QIODevice
from PyQt6.QtGui import QImageReader
from src.core.i18n import tr

MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_IMAGE_PIXELS = 16_000_000


def decode_image(data):
    if not isinstance(data, bytes) or not data or len(data) > MAX_IMAGE_BYTES:
        raise ValueError(tr("Görsel boş veya 8 MB sınırını aşıyor."))
    buffer = QBuffer()
    buffer.setData(QByteArray(data))
    buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    reader = QImageReader(buffer)
    reader.setAutoTransform(True)
    if bytes(reader.format()).lower() not in (b'png', b'jpg', b'jpeg', b'webp', b'gif', b'bmp'):
        raise ValueError(tr("Desteklenmeyen görsel biçimi."))
    size = reader.size()
    if (size.width() <= 0 or size.height() <= 0 or max(size.width(), size.height()) > 16384
            or size.width() * size.height() > MAX_IMAGE_PIXELS):
        raise ValueError(tr("Görsel boyutları geçersiz veya çok büyük."))
    image = reader.read()
    if image.isNull():
        raise ValueError(tr("Görsel okunamadı."))
    return image


def load_attachment(path, name=None):
    path = Path(path)
    if not path.is_file():
        raise ValueError(tr("Görsel dosyası bulunamadı."))
    with path.open('rb') as handle:
        data = handle.read(MAX_IMAGE_BYTES + 1)
    decode_image(data)
    return {"name": name or path.name, "data": data}


def capture_screenshot():
    with tempfile.TemporaryDirectory(prefix="linux-ai-question-") as directory:
        path = Path(directory, 'screen.png')
        for command in (["grim", str(path)], ["spectacle", "-b", "-n", "-f", "-o", str(path)],
                        ["gnome-screenshot", "-f", str(path)]):
            if shutil.which(command[0]) is None:
                continue
            try:
                path.unlink(missing_ok=True)
                result = subprocess.run(command, capture_output=True, timeout=15)
                if result.returncode == 0 and path.is_file():
                    return load_attachment(path, name=tr("Ekran görüntüsü"))
            except (OSError, ValueError, subprocess.TimeoutExpired):
                continue
    raise ValueError(tr("Ekran görüntüsü alınamadı."))
