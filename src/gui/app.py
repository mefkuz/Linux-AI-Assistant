import sys
import os
import threading
import struct
import math
import time
import logging
import fcntl
import warnings
import contextlib

# PyQt6 DeprecationWarning gizle
warnings.filterwarnings("ignore", category=DeprecationWarning, message=".*sipPyTypeDict.*")

@contextlib.contextmanager
def suppress_c_stderr():
    try:
        devnull = os.open(os.devnull, os.O_WRONLY)
        old_stderr = os.dup(2)
        sys.stderr.flush()
        os.dup2(devnull, 2)
        os.close(devnull)
    except Exception:
        yield
        return
    try:
        yield
    finally:
        sys.stderr.flush()
        os.dup2(old_stderr, 2)
        os.close(old_stderr)

# Wayland'da popup pencereler transient-parent olmadan çalışmıyor.
# XCB/XWayland modu tüm Qt pencere türlerini sorunsuz destekler.
os.environ.setdefault('QT_QPA_PLATFORM', 'xcb')

import pyaudio
from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QLineEdit, QPushButton, QSystemTrayIcon, QMenu, QMessageBox,
    QTabWidget, QSpinBox, QCheckBox, QFormLayout, QSizePolicy,
    QListWidget, QListWidgetItem, QAbstractItemView, QStackedWidget,
    QFileDialog, QTextEdit, QDialog, QDialogButtonBox
)
from PyQt6.QtCore import Qt, pyqtSignal, QObject, QTimer, QEvent
from PyQt6.QtGui import QIcon, QAction, QFont, QPainter, QColor, QPixmap, QPen, QKeyEvent, QKeySequence

from src.core.settings import SettingsManager
from src.audio.hotkeys import HotkeyManager
from src.audio.listener import AudioListener
from src.llm.router import Router
from src.llm.client import LLMClient
from src.llm.cli_registry import KNOWN_CLI_TOOLS
from src.context.helper import get_active_contexts
from src.core.i18n import set_language as set_core_lang
from src.core import updater
from src.core.version import __version__
from src.core.settings import PROJECT_ROOT

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────
#  Thread-safe sinyal köprüsü
# ──────────────────────────────────────────────────────────

TRANSLATIONS = {
    "Linux AI Assistant — Ayarlar": "Linux AI Assistant — Settings",
    "Genel": "General",
    "Dinleme": "Listening",
    "Yapay Zeka": "AI Settings",
    "Sistem Promptu": "System Prompt",
    "Güvenlik": "Security",
    "Kısayol tuşu:": "Shortcut key:",
    "Atama yapmak için tıklayıp tuşlara basın.": "Click and press keys to bind.",
    "Bildirim konumu:": "Overlay position:",
    "Sağ Alt": "Bottom-Right",
    "Sol Alt": "Bottom-Left",
    "Sağ Üst": "Top-Right",
    "Sol Üst": "Top-Left",
    "Orta": "Center",
    "Konuşma dili:": "Speech language:",
    "Uygulama dili:": "App language:",
    "Çalışma alanı:": "Workspace:",
    "Gözat...": "Browse...",
    "Boş bırakırsanız home dizini kullanılır": "Leave empty to use home directory",
    "Kaybolma süresi (sn):": "Disappear timeout (s):",
    "Bekleme süresi (sn):": "Display timeout (s):",
    "Mikrofon Hassasiyeti:": "Mic Sensitivity:",
    "Bekleme eşiği (sn):": "Pause threshold (s):",
    "Cümle limit süresi (sn):": "Phrase limit (s):",
    "Yapay Zeka Modu:": "AI Mode:",
    "Uzak API (Örn: Groq/OpenAI)": "Remote API (e.g. Groq)",
    "Terminal Aracı (Örn: agy)": "CLI Tool (e.g. agy)",
    "Yerel API (Örn: LM Studio)": "Local API (e.g. LM Studio)",
    "API URL:": "API URL:",
    "Model adı:": "Model name:",
    "API Anahtarı:": "API Key:",
    "AI Aracı:": "AI Tool:",
    "Model:": "Model:",
    "Ek argümanlar:": "Extra args:",
    "Yapay Zekanın Davranışını Belirleyen Sistem Komutu:": "System Command determining AI behavior:",
    "Yapay Zeka yanıtlarını bana sormadan (otomatik) ekranda göstersin": "Show AI responses automatically without asking",
    "Akıllı Dikte Düzeltici (Yapay Zeka ile İyileştirme)": "Smart Dictation Optimizer (AI Enhanced)",
    "Bağlam Farkındalığı (Pano ve Ekran Okumaya Her Zaman İzin Ver)": "Context Awareness (Always allow Clipboard & Screen OCR)",
    "İptal": "Cancel",
    "Kaydet": "Save",
    "Ayarlar": "Settings",
    "Çıkış": "Exit",
    "Dinleniyor...": "Listening...",
    "Yapay Zekanın Cevabı Bekleniyor...": "Waiting for AI response...",
    "İşlem arka planda tamamlandı ✓": "Task completed in background ✓",
    "Yapay Zeka Yanıtı": "AI Response",
    "İşlem Tamamlandı.": "Task Completed.",
    "Kapat": "Close",
    "Tarayıcı": "Browser",
    "Kaydedildi": "Saved",
    "Ayarlar başarıyla kaydedildi.": "Settings saved successfully.",
    "Dil değişikliklerinin tamamen uygulanması için lütfen uygulamayı yeniden başlatın.": "Please restart the app for language changes to fully apply.",
    "Kısayol atamak için tıklayın...": "Click to assign a shortcut...",
    "Tuşlara basın... (İptal için ESC)": "Press keys... (ESC to cancel)",
    "Sustur": "Mute",
    "Evet": "Yes",
    "Hayır": "No",
    "Ekle": "Add",
    "Eklendi": "Added",
    "Onay": "Confirm",
    "Bildirimleri aktif masaüstüne yapıştır (Sticky Window)": "Pin notifications to active desktop (Sticky Window)",
    "Dinleme zaman aşımı:": "Listening timeout:",
    "Sonuç gösterim süresi:": "Result display time:",
    "Mikrofon hassasiyeti:": "Mic sensitivity:",
    "Düşük = daha hassas. Önerilen: 1000–4000": "Lower = more sensitive. Recommended: 1000–4000",
    "Sessizlik eşiği (cümle sonu):": "Silence threshold (sentence end):",
    "Maks. konuşma süresi:": "Max. speech duration:",
    "Tek bir konuşmada maksimum süre.": "Maximum duration of a single utterance.",
    " sn": " s",
    " × 0.1 sn": " × 0.1 s",
    "Mod:": "Mode:",
    "Yerel Sunucu (Local)": "Local Server",
    "Uzak Sunucu (Remote)": "Remote Server",
    "Terminal (CLI)": "Terminal (CLI)",
    "Model seçin veya yazın": "Select or type a model",
    "Opsiyonel ek argümanlar (örn: --temperature 0.7)": "Optional extra args (e.g. --temperature 0.7)",
    "Bu araç model seçimi desteklemiyor": "This tool doesn't support model selection",
    "Yanıtlar": "Responses",
    "Yanıtları sormadan otomatik göster": "Show responses automatically without asking",
    "Kapalıysa 'Cevabı görmek istiyor musun?' diye sorulur.": "If off, asks 'Do you want to see the answer?'.",
    "Akıllı Dikte Düzeltici": "Smart Dictation Fixer",
    "Okuma İzinleri": "Read Permissions",
    "Ekran ve pano okumaya her zaman izin ver": "Always allow screen & clipboard reading",
    "Araç Çağırma (Uzak/Yerel API)": "Tool Calling (Remote/Local API)",
    "Araç Çağırma aktif": "Tool Calling enabled",
    "Dosya yazma ve komut çalıştırmadan önce sor": "Ask before writing files & running commands",
    "Kapalı olsa bile tehlikeli komutlar (rm, sudo vb.) her zaman sorulur.": "Dangerous commands (rm, sudo, etc.) always ask, even if off.",
    "Maks. araç turu:": "Max. tool rounds:",
    "Üst üste kaç tur araç çağrılabilir (sonsuz döngü koruması).": "How many back-to-back tool rounds are allowed (infinite-loop guard).",
    "Hafıza": "Memory",
    "Hatırlanacak konuşma turu:": "Dialogue turns to remember:",
    "Son kaç soru-cevap turu modele gönderilir.\n0 = hafıza kapalı.": "How many recent Q&A turns are sent to the model.\n0 = memory off.",
    "Günlük": "Logging",
    "Her istek için günlük dosyası yaz": "Write a log file per request",
    "Her istek Loglar/ klasörüne günlüklenir (PC tarafında, ek ücret yok).": "Each request is logged under Loglar/ (locally, no extra cost).",
    "Çalışma Alanı Seç": "Select Workspace",
    "Seçili metin eklendi": "Selected text added",
    "Video eklendi": "Video added",
    "PDF belgesi eklendi": "PDF document added",
    "E-posta eklendi": "Email added",
    "Tarayıcı sekmesi eklendi": "Browser tab added",
    "Seni dinliyorum...": "Listening to you...",
    "Ses algılanamadı.": "No speech detected.",
    "Ses Kaydı Alındı.": "Voice recorded.",
    "Akıllı Dikte Düzeltici Çalışıyor...": "Smart Dictation Fixer running...",
    "Ekran Okunuyor (OCR)...": "Reading screen (OCR)...",
    "Video Altyazısı Okunuyor...": "Reading video subtitles...",
    "PDF İçeriği Okunuyor...": "Reading PDF content...",
    "Hafızayı Temizle": "Clear Memory",
    "Yapay zekanın hatırladığı konuşma geçmişini siler.": "Clears the conversation history the AI remembers.",
    "Konuşma geçmişi temizlendi.": "Conversation history cleared.",
    "Uygulama Zaten Çalışıyor": "Already Running",
    "Linux-AI-Assistant şu anda arka planda zaten açık!": "Linux-AI-Assistant is already running in the background!",
    "Yanıtı Göster": "Show Response",
    "Yapay zeka işlemini tamamladı ve size bir yanıt vermek istiyor.": "The AI finished and has a response for you.",
    "Cevabı ekranda görmek istiyor musunuz?": "Do you want to see the answer on screen?",
    "Bağlam Farkındalığı (Pano ve Ekran Erişimi)": "Context Awareness (Clipboard & Screen Access)",
    "Cümlenizde bağlam gerektiren kelimeler tespit edildi. Yapay zekaya panonuzdaki/ekranınızdaki metin de gönderilsin mi?": "Context-dependent words detected. Also send your clipboard/screen text to the AI?",
    "Güncellemeleri Denetle": "Check for Updates",
    "Güncelleme denetleniyor...": "Checking for updates...",
    "Kullanılabilir güncelleme yok.": "No updates available.",
    "Güncelleme denetimi başarısız.": "Update check failed.",
    "Yeni sürüm mevcut": "New version available",
    "Şimdi Güncelle": "Update Now",
    "Daha Sonra": "Later",
    "Bu Sürümü Atla": "Skip This Version",
    "Güncelleniyor, lütfen bekleyin...": "Updating, please wait...",
    "Güncelleme tamamlandı. Yeniden başlatılsın mı?": "Update complete. Restart now?",
    "Yeniden Başlat": "Restart",
    "Release Sayfasını Aç": "Open Release Page",
    "Açılışta güncellemeleri otomatik denetle": "Check for updates automatically on startup",
    "Şimdi denetle": "Check now",
    "Arka planda çalışıyor": "Running in the background",
    "Sistem çekmecesindeki ikona sağ tıklayarak menüyü açabilirsiniz.": "Right-click the system tray icon to open the menu.",
}

_APP_LANG = "tr"
def tr(text):
    if _APP_LANG == "en":
        return TRANSLATIONS.get(text, text)
    return text

class Communicate(QObject):
    show_overlay   = pyqtSignal()
    hide_overlay   = pyqtSignal()
    update_text    = pyqtSignal(str)
    start_waveform = pyqtSignal()
    stop_waveform  = pyqtSignal()
    ask_confirm       = pyqtSignal(object, object, object) # summary_dict, result_list, threading.Event
    ask_show_response = pyqtSignal(object, object) # result_list, threading.Event
    ask_clipboard     = pyqtSignal(object, object, str) # result_list, threading.Event, text
    show_response     = pyqtSignal(str) # text
    show_context_btns = pyqtSignal(object) # list of dicts
    update_checked = pyqtSignal(object, object) # info dict, silent bool
    hide_context_btns = pyqtSignal()

# ──────────────────────────────────────────────────────────
#  Kısayol Yakalayıcı (KDE-style)
# ──────────────────────────────────────────────────────────
class HotkeyCaptureWidget(QLineEdit):
    def __init__(self, current_hotkey=""):
        super().__init__(current_hotkey)
        self.setReadOnly(True)
        self.setPlaceholderText(tr("Kısayol atamak için tıklayın..."))
        self.capturing = False

    def mousePressEvent(self, event):
        self.capturing = True
        self.setText(tr("Tuşlara basın... (İptal için ESC)"))
        self.setStyleSheet("background-color: #2b5b84; color: white;")
        super().mousePressEvent(event)

    def keyPressEvent(self, event: QKeyEvent):
        if not self.capturing:
            super().keyPressEvent(event)
            return

        key = event.key()
        if key == Qt.Key.Key_Escape:
            self.capturing = False
            self.setStyleSheet("")
            self.setText("")
            return
        
        # Sadece modifier (Ctrl, Alt, vb.) basıldıysa bekle
        if key in (Qt.Key.Key_Control, Qt.Key.Key_Shift, Qt.Key.Key_Alt, Qt.Key.Key_Meta):
            return

        # Modifierları topla
        mods = event.modifiers()
        parts = []
        if mods & Qt.KeyboardModifier.ControlModifier:
            parts.append("<ctrl>")
        if mods & Qt.KeyboardModifier.AltModifier:
            parts.append("<alt>")
        if mods & Qt.KeyboardModifier.ShiftModifier:
            parts.append("<shift>")
        if mods & Qt.KeyboardModifier.MetaModifier:
            parts.append("<super>")

        # Ana tuşu pynput formatına çevir
        key_name = QKeySequence(key).toString().lower()
        if key_name == "space":
            parts.append("<space>")
        else:
            # Sadece tek harf ise (a, b, c vs) olduğu gibi, yoksa <tuş> formatında
            if len(key_name) == 1:
                parts.append(key_name)
            else:
                parts.append(f"<{key_name}>")
                
        self.setText("+".join(parts))
        self.capturing = False
        self.setStyleSheet("")

# ──────────────────────────────────────────────────────────
#  Ses dalgası widget
# ──────────────────────────────────────────────────────────
import pyaudio
import struct
import math

class WaveformWidget(QWidget):
    NUM_BARS = 12

    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self.setMinimumSize(214, 26)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._levels  = [0.0] * self.NUM_BARS
        self._targets = [0.0] * self.NUM_BARS
        with suppress_c_stderr():
            self._pa      = pyaudio.PyAudio()
        self._stream  = None
        self._timer   = QTimer(self)
        self._timer.timeout.connect(self._tick)

    def start(self):
        try:
            if self._stream is None:
                self._stream = self._pa.open(
                    format=pyaudio.paInt16, channels=1,
                    rate=16000, input=True,
                    frames_per_buffer=1024, start=False
                )
            if not self._stream.is_active():
                self._stream.start_stream()
            self._timer.start(40)
        except Exception as e:
            logger.error(f"Waveform açılamadı: {e}")

    def stop(self):
        self._timer.stop()
        if self._stream and self._stream.is_active():
            self._stream.stop_stream()
        self._levels  = [0.0] * self.NUM_BARS
        self._targets = [0.0] * self.NUM_BARS
        self.update()

    def cleanup(self):
        self.stop()
        if self._stream:
            self._stream.close()
        self._pa.terminate()

    def _tick(self):
        sensitivity = self.settings.get("mic_sensitivity", 3000)
        try:
            if self._stream and self._stream.is_active():
                data  = self._stream.read(1024, exception_on_overflow=False)
                count = len(data) // 2
                if count > 0:
                    shorts = struct.unpack(f"{count}h", data)
                    rms = math.sqrt(sum(s * s for s in shorts) / count)
                    vol = min(1.0, rms / max(sensitivity, 1))
                    self._targets.pop(0)
                    self._targets.append(vol)
        except Exception:
            pass
        for i in range(self.NUM_BARS):
            self._levels[i] += (self._targets[i] - self._levels[i]) * 0.4
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h  = self.width(), self.height()
        gap   = 4
        bar_w = (w - gap * (self.NUM_BARS - 1)) / self.NUM_BARS
        p.setPen(Qt.PenStyle.NoPen)
        for i, level in enumerate(self._levels):
            bar_h = max(4.0, level * h)
            x     = i * (bar_w + gap)
            y     = (h - bar_h) / 2.0
            alpha = int(160 + 95 * level)
            p.setBrush(QColor(255, 255, 255, alpha))
            p.drawRoundedRect(int(x), int(y), max(1, int(bar_w)), int(bar_h), 3, 3)

# ──────────────────────────────────────────────────────────
#  Overlay penceresi
# ──────────────────────────────────────────────────────────
class OverlayWindow(QWidget):
    stop_clicked = pyqtSignal()
    context_added = pyqtSignal(str, str) # title, url

    def __init__(self, settings, waveform):
        super().__init__()
        self.settings = settings
        self.waveform = waveform
        self.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, False)
        self._init_ui()

    def _init_ui(self):
        global _APP_LANG
        _APP_LANG = self.settings.get('app_language', 'tr')
        try:
            set_core_lang(_APP_LANG)
        except Exception:
            pass
        # Eğer sticky_window açıksa X11/Wayland üzerinde ToolTip bayrağı kullanılarak
        # pencerenin tüm masaüstlerinde ve monitörlerde yapışkan (sticky) kalması sağlanır.
        flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
        if self.settings.get("sticky_window", True):
            flags |= Qt.WindowType.ToolTip
        else:
            flags |= Qt.WindowType.Tool
        
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(10) # Aradaki boşluk

        # Context Buttons Container (Separate from main bubble)
        self.context_btns_container = QWidget()
        self.context_btns_layout = QVBoxLayout(self.context_btns_container)
        self.context_btns_layout.setContentsMargins(0, 0, 0, 0) # Hizalama düzeltildi
        self.context_btns_layout.setSpacing(6)
        
        outer.addWidget(self.context_btns_container)
        self.context_btns_container.hide()

        # --- Main Bubble ---
        container = QWidget(self)
        container.setObjectName("oc")
        container.setStyleSheet("""
            #oc {
                background: rgba(16, 16, 20, 235);
                border-radius: 16px;
                border: 1px solid rgba(255,255,255,30);
            }
        """)
        container.setFixedWidth(260)
        container.setMinimumHeight(60)
        
        inner = QVBoxLayout(container)
        inner.setContentsMargins(16, 16, 16, 16)
        inner.setSpacing(8)

        # Status Label
        self.label = QLabel(tr("Dinleniyor..."))
        self.label.setStyleSheet("color: rgba(255,255,255,200); font-size: 13px; background: transparent; border: none; font-weight: 500;")
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label.setFont(QFont("Sans Serif", 11))
        self.label.setWordWrap(True)
        
        # Waveform
        self.waveform.setStyleSheet("background: transparent; border: none;")
        self.waveform.setFixedHeight(32)
        
        inner.addWidget(self.label)
        inner.addWidget(self.waveform)

        # Stop Button
        self.stop_btn = QPushButton(tr("Sustur"))
        self.stop_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(220, 50, 50, 200);
                color: white;
                border-radius: 8px;
                padding: 6px;
                font-weight: bold;
                border: none;
            }
            QPushButton:hover { background-color: rgba(255, 70, 70, 255); }
        """)
        self.stop_btn.clicked.connect(self.stop_clicked.emit)
        self.stop_btn.hide()
        inner.addWidget(self.stop_btn)

        outer.addWidget(container)
        
        self.setMinimumWidth(260)
        self.setMaximumWidth(450)
        self.resize(260, 100)

    def reposition(self):
        from PyQt6.QtGui import QCursor
        
        # Eğer sticky_window aktifse mouse imlecinin olduğu ekrana yapışır,
        # değilse her zaman birincil ekranda (primary monitor) çıkar.
        is_sticky = self.settings.get("sticky_window", True)
        if is_sticky:
            screen_obj = QApplication.screenAt(QCursor.pos())
            if not screen_obj:
                screen_obj = QApplication.primaryScreen()
        else:
            screen_obj = QApplication.primaryScreen()
            
        screen = screen_obj.availableGeometry()
        
        pos    = self.settings.get("overlay_position", "Bottom-Right")
        m      = 28
        w, h   = self.width(), self.height()
        coords = {
            "Bottom-Right": (screen.x() + screen.width()  - w - m, screen.y() + screen.height() - h - m),
            "Bottom-Left":  (screen.x() + m,                        screen.y() + screen.height() - h - m),
            "Top-Right":    (screen.x() + screen.width()  - w - m, screen.y() + m),
            "Top-Left":     (screen.x() + m,                        screen.y() + m),
            "Center":       (screen.x() + (screen.width()  - w) // 2, screen.y() + (screen.height() - h) // 2),
        }
        x, y = coords.get(pos, coords["Bottom-Right"])
        self.move(x, y)

    def set_text(self, text):
        self.label.setText(text)
        # Önce küçültmeyi dene, ardından içeriğe göre otomatik boyuta ayarla
        self.resize(260, 100)
        self.adjustSize()
        self.reposition()

    def show_stop_button(self, show=True):
        if show:
            self.stop_btn.show()
        else:
            self.stop_btn.hide()
        self.adjustSize()
        self.reposition()

    def show_context_btns(self, contexts):
        # Clear existing
        while self.context_btns_layout.count():
            child = self.context_btns_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
                
        if not contexts:
            self.context_btns_container.hide()
            self.adjustSize()
            self.reposition()
            return
            
        for ctx in contexts:
            btn = QPushButton(f"{ctx['icon']} {ctx['label']} {tr('Ekle')}")
            btn.setStyleSheet("""
                QPushButton {
                    background-color: rgba(20, 20, 25, 180);
                    color: white;
                    border-radius: 12px;
                    padding: 8px 14px;
                    font-size: 12px;
                    font-weight: 600;
                    text-align: left;
                    border: 1px solid rgba(255,255,255,30);
                }
                QPushButton:hover { 
                    background-color: rgba(50, 50, 60, 220); 
                    border: 1px solid rgba(255,255,255,60);
                }
            """)

            # Use closure to capture ctx
            def make_callback(context_data, button):
                def callback():
                    button.setText(f"{context_data['icon']} {tr('Eklendi')} ✓")
                    button.setEnabled(False)
                    self.context_added.emit(context_data['title'], context_data['detail'] or "")
                return callback
                
            btn.clicked.connect(make_callback(ctx, btn))
            self.context_btns_layout.addWidget(btn)
            
        self.context_btns_container.show()
        self.adjustSize()
        self.reposition()
        
    def hide_context_btns(self):
        self.context_btns_container.hide()
        self.adjustSize()
        self.reposition()

# ──────────────────────────────────────────────────────────
#  Ayarlar penceresi  (Dialog YOK — sıradan QWidget)
#  Dialog bayrağı X11'de global keyboard grab yapıyor;
#  bu yüzden tamamen kaldırıldı.
# ──────────────────────────────────────────────────────────
# ──────────────────────────────────────────────────────────
#  Yapay Zeka Yanıt Penceresi (Özel QDialog)
# ──────────────────────────────────────────────────────────
from PyQt6.QtWidgets import QTextBrowser


class ConfirmDialog(QDialog):
    """Sade onay penceresi: başlık + tek soru + küçük gri detay (kayan)."""

    def __init__(self, title, question, detail=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        q_lbl = QLabel(question)
        q_lbl.setWordWrap(True)
        layout.addWidget(q_lbl)

        if detail:
            d_lbl = QLabel(detail)
            d_lbl.setWordWrap(True)
            d_lbl.setStyleSheet("color: #888; font-size: 11px;")
            # Uzun detaylar pencereyi şişirmesin: metin kayar
            d_lbl.setMaximumHeight(120)
            layout.addWidget(d_lbl)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Yes | QDialogButtonBox.StandardButton.No
        )
        buttons.button(QDialogButtonBox.StandardButton.Yes).setText(tr("Evet"))
        buttons.button(QDialogButtonBox.StandardButton.No).setText(tr("Hayır"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

class ResponseWindow(QDialog):
    def __init__(self, text, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("Yapay Zeka Yanıtı"))
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
        self.resize(500, 400)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)
        
        # Etiket
        lbl = QLabel("<b>" + tr("İşlem Tamamlandı.") + "</b>")
        layout.addWidget(lbl)
        
        # Yanıt Alanı (Kopyalanabilir, Markdown destekli QTextBrowser)
        self.text_browser = QTextBrowser()
        self.text_browser.setOpenExternalLinks(True)
        self.text_browser.setMarkdown(text)
        layout.addWidget(self.text_browser)
        
        # Kapat Butonu
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        close_btn = QPushButton(tr("Kapat"))
        close_btn.setMinimumWidth(100)
        close_btn.clicked.connect(self.accept)
        btn_layout.addWidget(close_btn)
        
        layout.addLayout(btn_layout)

# ──────────────────────────────────────────────────────────
class SettingsWindow(QWidget):
    def __init__(self, settings, hotkey_manager):
        super().__init__()
        self.settings       = settings
        self.hotkey_manager = hotkey_manager
        # Pencere kapansa bile uygulamadan çıkılmasın
        self.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, False)
        # Normal pencere — keyboard grab yapmaz
        self.setWindowFlags(
            Qt.WindowType.Window |
            Qt.WindowType.WindowCloseButtonHint |
            Qt.WindowType.WindowTitleHint
        )
        self._init_ui()

    def closeEvent(self, event):
        # X butonuna basılınca kapat değil, gizle
        event.ignore()
        self.hide()

    def _manual_update_check(self):
        """Ayarlar sekmesindeki 'Şimdi denetle' — sonucu durum satırına yazar."""
        self.upd_status_lbl.setText(tr("Güncelleme denetleniyor..."))
        self.upd_now_btn.setEnabled(False)

        def _bg():
            info = updater.check_for_updates()
            cur = (info.get("current_version") or __version__)
            if not info.get("ok"):
                msg = tr("Güncelleme denetimi başarısız.")
            elif info.get("update_available"):
                v = info.get("latest_version", "")
                msg = f"{tr('Yeni sürüm mevcut')}: v{v} (v{cur} → v{v})."
            else:
                msg = f"{tr('Kullanılabilir güncelleme yok.')} (v{cur})"
            QTimer.singleShot(0, lambda: (self.upd_status_lbl.setText(msg),
                                          self.upd_now_btn.setEnabled(True)))

        threading.Thread(target=_bg, daemon=True).start()

    def _init_ui(self):
        self.setWindowTitle(tr("Linux AI Assistant — Ayarlar"))
        self.setFixedSize(650, 480)

        root = QVBoxLayout(self)
        tabs = QTabWidget()

        # ── SEKME 1: Genel ────────────────────────────────
        tab_general = QWidget()
        fl = QFormLayout(tab_general)
        fl.setSpacing(12); fl.setContentsMargins(16, 16, 16, 16)

        self.hotkey_input = HotkeyCaptureWidget(self.settings.get("hotkey"))
        self.hotkey_input.setToolTip(tr("Atama yapmak için tıklayıp tuşlara basın."))
        fl.addRow(tr("Kısayol tuşu:"), self.hotkey_input)

        self.pos_combo = QComboBox()
        self.pos_combo.addItem(tr("Sağ Alt"), "Bottom-Right")
        self.pos_combo.addItem(tr("Sol Alt"), "Bottom-Left")
        self.pos_combo.addItem(tr("Sağ Üst"), "Top-Right")
        self.pos_combo.addItem(tr("Sol Üst"), "Top-Left")
        self.pos_combo.addItem(tr("Orta"), "Center")
        cur_pos = self.settings.get("overlay_position", "Bottom-Right")
        idx = self.pos_combo.findData(cur_pos)
        if idx >= 0: self.pos_combo.setCurrentIndex(idx)
        fl.addRow(tr("Bildirim konumu:"), self.pos_combo)

        self.lang_combo = QComboBox()
        langs = [("Türkçe", "tr-TR"), ("İngilizce", "en-US"), ("Almanca", "de-DE"),
                 ("Fransızca", "fr-FR"), ("İspanyolca", "es-ES")]
        for label, code in langs:
            self.lang_combo.addItem(label, code)
        cur = self.settings.get("language", "tr-TR")
        for i in range(self.lang_combo.count()):
            if self.lang_combo.itemData(i) == cur:
                self.lang_combo.setCurrentIndex(i); break
        fl.addRow(tr("Konuşma dili:"), self.lang_combo)

        self.app_lang_combo = QComboBox()
        app_langs = [("Türkçe", "tr"), ("English", "en")]
        for label, code in app_langs:
            self.app_lang_combo.addItem(label, code)
        cur_app = self.settings.get("app_language", "tr")
        for i in range(self.app_lang_combo.count()):
            if self.app_lang_combo.itemData(i) == cur_app:
                self.app_lang_combo.setCurrentIndex(i); break
        fl.addRow(tr("Uygulama dili:"), self.app_lang_combo)

        # Çalışma alanı dizini
        ws_row = QHBoxLayout()
        self.workspace_input = QLineEdit(self.settings.get("workspace_dir", ""))
        self.workspace_input.setPlaceholderText(tr("Boş bırakırsanız home dizini kullanılır"))
        ws_browse = QPushButton(tr("Gözat..."))
        ws_browse.setFixedWidth(70)
        ws_browse.clicked.connect(self._browse_workspace)
        ws_row.addWidget(self.workspace_input)
        ws_row.addWidget(ws_browse)
        fl.addRow(tr("Çalışma alanı:"), ws_row)

        self.sticky_cb = QCheckBox(tr("Bildirimleri aktif masaüstüne yapıştır (Sticky Window)"))
        self.sticky_cb.setChecked(self.settings.get("sticky_window", True))
        fl.addRow("", self.sticky_cb)

        tabs.addTab(tab_general, tr("Genel"))

        # ── SEKME 2: Dinleme ──────────────────────────────
        tab_listen = QWidget()
        ll = QFormLayout(tab_listen)
        ll.setSpacing(12); ll.setContentsMargins(16, 16, 16, 16)

        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(2, 30); self.timeout_spin.setSuffix(tr(" sn"))
        self.timeout_spin.setValue(self.settings.get("overlay_timeout_seconds", 5))
        ll.addRow(tr("Dinleme zaman aşımı:"), self.timeout_spin)

        self.display_spin = QSpinBox()
        self.display_spin.setRange(1, 10); self.display_spin.setSuffix(tr(" sn"))
        self.display_spin.setValue(self.settings.get("overlay_display_seconds", 3))
        ll.addRow(tr("Sonuç gösterim süresi:"), self.display_spin)

        self.sensitivity_spin = QSpinBox()
        self.sensitivity_spin.setRange(200, 10000)
        self.sensitivity_spin.setSingleStep(100)
        self.sensitivity_spin.setValue(self.settings.get("mic_sensitivity", 3000))
        self.sensitivity_spin.setToolTip(tr("Düşük = daha hassas. Önerilen: 1000–4000"))
        ll.addRow(tr("Mikrofon hassasiyeti:"), self.sensitivity_spin)

        self.pause_spin = QSpinBox()
        self.pause_spin.setRange(5, 50); self.pause_spin.setSuffix(tr(" × 0.1 sn"))
        self.pause_spin.setValue(int(self.settings.get("pause_threshold", 1.5) * 10))
        self.pause_spin.setToolTip(
            "Kaç saniyelik sessizlik 'konuşma bitti' sayılsın?\n"
            "Artırın → cümle ortasında kesilmez\n"
            "Azaltın → hızlı tepki verir (Varsayılan: 1.5 sn = 15)"
            if _APP_LANG == "tr" else
            "How much silence counts as 'speech ended'?\n"
            "Increase → won't cut mid-sentence\n"
            "Decrease → faster response (Default: 1.5 s = 15)"
        )
        ll.addRow(tr("Sessizlik eşiği (cümle sonu):"), self.pause_spin)

        self.phrase_limit_spin = QSpinBox()
        self.phrase_limit_spin.setRange(5, 120); self.phrase_limit_spin.setSuffix(tr(" sn"))
        self.phrase_limit_spin.setValue(self.settings.get("phrase_time_limit", 30))
        self.phrase_limit_spin.setToolTip(tr("Tek bir konuşmada maksimum süre."))
        ll.addRow(tr("Maks. konuşma süresi:"), self.phrase_limit_spin)

        tabs.addTab(tab_listen, tr("Dinleme"))

        # ── SEKME 3: Yapay Zeka ───────────────────────────
        tab_llm = QWidget()
        al = QVBoxLayout(tab_llm)
        al.setContentsMargins(16, 16, 16, 16); al.setSpacing(10)

        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel(tr("Mod:")))
        self.llm_mode_combo = QComboBox()
        self.llm_mode_combo.addItem(tr("Yerel Sunucu (Local)"), "local")
        self.llm_mode_combo.addItem(tr("Uzak Sunucu (Remote)"), "remote")
        self.llm_mode_combo.addItem(tr("Terminal (CLI)"), "cli")
        cur_mode = self.settings.get("llm_mode", "local")
        idx = self.llm_mode_combo.findData(cur_mode)
        if idx >= 0: self.llm_mode_combo.setCurrentIndex(idx)
        mode_row.addWidget(self.llm_mode_combo)
        al.addLayout(mode_row)

        # ── Modlara özel paneller (QStackedWidget) ──
        self.llm_stack = QStackedWidget()

        # local paneli
        local_panel = QWidget()
        lp = QFormLayout(local_panel)
        lp.setSpacing(10); lp.setContentsMargins(0, 0, 0, 0)
        self.local_url_input   = QLineEdit(self.settings.get("local_api_url"))
        self.local_model_input = QLineEdit(self.settings.get("local_model"))
        lp.addRow(tr("API URL:"), self.local_url_input)
        lp.addRow(tr("Model adı:"), self.local_model_input)
        self.llm_stack.addWidget(local_panel)   # index 0

        # remote paneli
        remote_panel = QWidget()
        rp = QFormLayout(remote_panel)
        rp.setSpacing(10); rp.setContentsMargins(0, 0, 0, 0)
        self.remote_url_input   = QLineEdit(self.settings.get("remote_api_url"))
        self.remote_model_input = QLineEdit(self.settings.get("remote_model"))
        self.api_key_input      = QLineEdit(self.settings.get("remote_api_key"))
        self.api_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        rp.addRow(tr("API URL:"), self.remote_url_input)
        rp.addRow(tr("Model adı:"), self.remote_model_input)
        rp.addRow(tr("API Anahtarı:"), self.api_key_input)
        self.llm_stack.addWidget(remote_panel)  # index 1

        # cli paneli — Registry tabanlı araç + model seçimi
        cli_llm_panel = QWidget()
        cp = QFormLayout(cli_llm_panel)
        cp.setSpacing(10); cp.setContentsMargins(0, 0, 0, 0)

        self.cli_tool_combo = QComboBox()
        for display_name, cfg in KNOWN_CLI_TOOLS.items():
            self.cli_tool_combo.addItem(display_name)
            self.cli_tool_combo.setItemData(
                self.cli_tool_combo.count() - 1, cfg["description"], Qt.ItemDataRole.ToolTipRole
            )
        cur_key = self.settings.get("llm_cli_tool_key", "agy (Antigravity)")
        idx = self.cli_tool_combo.findText(cur_key)
        if idx >= 0:
            self.cli_tool_combo.setCurrentIndex(idx)
        cp.addRow(tr("AI Aracı:"), self.cli_tool_combo)

        self.cli_model_combo = QComboBox()
        self.cli_model_combo.setEditable(True)  # Elle yazılabilsin
        self.cli_model_combo.setPlaceholderText(tr("Model seçin veya yazın"))
        cp.addRow(tr("Model:"), self.cli_model_combo)

        self.cli_extra_input = QLineEdit(self.settings.get("llm_cli_extra_args", ""))
        self.cli_extra_input.setPlaceholderText(tr("Opsiyonel ek argümanlar (örn: --temperature 0.7)"))
        cp.addRow(tr("Ek argümanlar:"), self.cli_extra_input)

        note = QLabel("Sesiniz metne çevrildikten sonra stdin üzerinden\n"
                       "seçilen araca gönderilir. Araç yanıtını stdout'a yazdırmalıdır."
                       if _APP_LANG == "tr" else
                       "After your voice is transcribed it is piped via stdin\n"
                       "to the selected tool. The tool must print its answer to stdout.")
        note.setStyleSheet("color: #888; font-size: 11px;")
        cp.addRow(note)
        self.llm_stack.addWidget(cli_llm_panel)  # index 2

        # Araç değişince model listesini güncelle
        self._update_cli_models(self.cli_tool_combo.currentText())
        self.cli_tool_combo.currentTextChanged.connect(self._update_cli_models)
        # Mevcut modeli seç
        cur_model = self.settings.get("llm_cli_model", "")
        if cur_model:
            self.cli_model_combo.setCurrentText(cur_model)

        al.addWidget(self.llm_stack)
        al.addStretch()

        # Mod değişince ilgili paneli göster
        self._update_llm_stack(self.llm_mode_combo.currentIndex())
        self.llm_mode_combo.currentIndexChanged.connect(self._update_llm_stack)

        tabs.addTab(tab_llm, tr("Yapay Zeka"))

        # ── SEKME 4: Sistem Promptu ───────────────────────
        tab_prompt = QWidget()
        pl = QVBoxLayout(tab_prompt)
        pl.setContentsMargins(16, 16, 16, 16); pl.setSpacing(8)

        pl.addWidget(QLabel(tr("Yapay Zekanın Davranışını Belirleyen Sistem Komutu:")))
        
        self.sys_prompt_input = QTextEdit()
        self.sys_prompt_input.setPlainText(self.settings.get("system_prompt", ""))
        self.sys_prompt_input.setPlaceholderText("Sen yetenekli bir asistan..." if _APP_LANG == "tr" else "You are a skilled assistant...")
        pl.addWidget(self.sys_prompt_input)

        tabs.addTab(tab_prompt, tr("Sistem Promptu"))

        # ── SEKME 5: Güvenlik & Ekstra Ayarlar ─────────────────────────────
        tab_sec = QWidget()
        sl = QVBoxLayout(tab_sec)
        sl.setSpacing(4); sl.setContentsMargins(16, 16, 16, 16)

        def _section(title):
            lbl = QLabel(f"<b>{title}</b>")
            lbl.setStyleSheet("color: #ddd; font-size: 13px; margin-top: 6px;")
            sl.addWidget(lbl)

        def _check(text, tip, key, default=False):
            cb = QCheckBox(text)
            cb.setChecked(self.settings.get(key, default))
            cb.setToolTip(tip)
            sl.addWidget(cb)
            return cb

        _section(tr("Yanıtlar"))
        self.popup_check = _check(
            tr("Yanıtları sormadan otomatik göster"),
            tr("Kapalıysa 'Cevabı görmek istiyor musun?' diye sorulur."),
            "auto_show_popup")
        self.opt_check = _check(
            tr("Akıllı Dikte Düzeltici"),
            "Diktedeki duraksama/hataları (ııı, eee, şey) LLM ile temizler.\n"
            "UYARI: Yanıt süresini uzatır, token kullanımını ~2 katına çıkarır."
            if _APP_LANG == "tr" else
            "Cleans dictation pauses/errors (uhh, umm) with the LLM.\n"
            "WARNING: slower responses, ~2x token usage.",
            "optimize_dictation")

        _section(tr("Okuma İzinleri"))
        self.clip_check = _check(
            tr("Ekran ve pano okumaya her zaman izin ver"),
            "Kapalıysa yapay zeka okumadan önce her seferinde sorar.\n"
            "Terminal modunda 'bunu/şunu/ekran' denince, Araç Çağırma aktifken\n"
            "okuma yapılmadan hemen önce sorulur."
            if _APP_LANG == "tr" else
            "If off, the AI asks before every read.\n"
            "In terminal mode ('this/that/screen') or with Tool Calling,\n"
            "asks right before reading.",
            "auto_allow_clipboard")

        _section(tr("Araç Çağırma (Uzak/Yerel API)"))
        self.tool_enable_check = _check(
            tr("Araç Çağırma aktif"),
            "Yapay zeka dosya/komut/ekran/pano/tarayıcı araçlarını kullanabilir.\n"
            "Aktifken eski keyword tabanlı pano/ekran enjeksiyonu devre dışı kalır.\n"
            "Terminal (CLI) modunda etkisizdir."
            if _APP_LANG == "tr" else
            "The AI can use file/command/screen/clipboard/browser tools.\n"
            "When on, legacy keyword-based clipboard/screen injection is disabled.\n"
            "No effect in terminal (CLI) mode.",
            "enable_tool_calling", True)
        self.tool_confirm_check = _check(
            tr("Dosya yazma ve komut çalıştırmadan önce sor"),
            tr("Kapalı olsa bile tehlikeli komutlar (rm, sudo vb.) her zaman sorulur."),
            "require_confirm_on_tool")

        iter_row = QHBoxLayout()
        iter_row.addWidget(QLabel(tr("Maks. araç turu:")))
        self.tool_iter_spin = QSpinBox()
        self.tool_iter_spin.setRange(1, 10)
        self.tool_iter_spin.setValue(self.settings.get("tool_max_iterations", 5))
        self.tool_iter_spin.setToolTip(tr("Üst üste kaç tur araç çağrılabilir (sonsuz döngü koruması)."))
        iter_row.addWidget(self.tool_iter_spin)
        iter_row.addStretch()
        sl.addLayout(iter_row)

        _section(tr("Hafıza"))
        hist_row = QHBoxLayout()
        hist_row.addWidget(QLabel(tr("Hatırlanacak konuşma turu:")))
        self.hist_spin = QSpinBox()
        self.hist_spin.setRange(0, 20)
        self.hist_spin.setValue(self.settings.get("history_max_turns", 6))
        self.hist_spin.setToolTip(tr("Son kaç soru-cevap turu modele gönderilir.\n0 = hafıza kapalı."))
        hist_row.addWidget(self.hist_spin)
        hist_row.addStretch()
        sl.addLayout(hist_row)

        _section(tr("Günlük"))
        self.req_log_check = _check(
            tr("Her istek için günlük dosyası yaz"),
            tr("Her istek Loglar/ klasörüne günlüklenir (PC tarafında, ek ücret yok)."),
            "auto_request_log", True)
        sl.addStretch()

        tabs.addTab(tab_sec, tr("Güvenlik"))

        # ── Sekme: Tarayıcı Eklentisi ──
        tab_ext = QWidget()
        ext_layout = QVBoxLayout(tab_ext)
        _ext_tr = (
            "<b>Linux AI Asistan - Tarayıcı Eklentisi Kurulumu</b><br><br>"
            "Eklenti sayesinde Gmail, YouTube, PDF'ler ve tüm web sayfalarını asistanınızla entegre edebilirsiniz.<br><br>"
            "<b>Nasıl Kurulur?</b><br>"
            "1. Chrome, Brave veya Edge tarayıcınızda <code>chrome://extensions/</code> (veya edge://extensions/) sayfasına gidin.<br>"
            "2. Sağ üst köşeden <b>'Geliştirici Modu'</b> (Developer mode) seçeneğini aktifleştirin.<br>"
            "3. Sol üstteki <b>'Paketlenmemiş öge yükle'</b> (Load unpacked) butonuna tıklayın.<br>"
            "4. Açılan pencerede uygulamanın bulunduğu dosya konumundaki <code>extensions/chrome</code> klasörünü seçin.<br><br>"
            "İşte bu kadar! Eklentiyi uzantılar menüsünden sabitleyip hemen kullanmaya başlayabilirsiniz."
        )
        _ext_en = (
            "<b>Linux AI Assistant - Browser Extension Setup</b><br><br>"
            "With the extension you can integrate Gmail, YouTube, PDFs and all web pages with your assistant.<br><br>"
            "<b>How to Install?</b><br>"
            "1. In Chrome, Brave or Edge go to <code>chrome://extensions/</code> (or edge://extensions/).<br>"
            "2. Enable <b>'Developer mode'</b> in the top right.<br>"
            "3. Click <b>'Load unpacked'</b> in the top left.<br>"
            "4. Select the <code>extensions/chrome</code> folder inside the app directory.<br><br>"
            "That's it! Pin the extension and start using it right away."
        )
        ext_info = QLabel(_ext_tr if _APP_LANG == "tr" else _ext_en)
        ext_info.setTextFormat(Qt.TextFormat.RichText)
        ext_info.setStyleSheet("font-size: 13px; line-height: 1.5;")
        ext_info.setWordWrap(True)
        ext_info.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        ext_layout.addWidget(ext_info)
        
        tabs.addTab(tab_ext, tr("Tarayıcı"))

        # ── Sekme: Güncelleme ──
        tab_upd = QWidget()
        ul = QVBoxLayout(tab_upd)
        ul.setContentsMargins(16, 16, 16, 16); ul.setSpacing(10)
        self.upd_ver_lbl = QLabel(f"Linux AI Assistant v{__version__}")
        self.upd_ver_lbl.setStyleSheet("font-size: 14px; font-weight: bold;")
        ul.addWidget(self.upd_ver_lbl)
        self.upd_auto_check = QCheckBox(tr("Açılışta güncellemeleri otomatik denetle"))
        self.upd_auto_check.setChecked(self.settings.get("auto_check_updates", True))
        ul.addWidget(self.upd_auto_check)
        upd_btn_row = QHBoxLayout()
        self.upd_now_btn = QPushButton(tr("Şimdi denetle"))
        self.upd_now_btn.clicked.connect(self._manual_update_check)
        upd_btn_row.addWidget(self.upd_now_btn)
        upd_btn_row.addStretch()
        ul.addLayout(upd_btn_row)
        self.upd_status_lbl = QLabel("")
        self.upd_status_lbl.setStyleSheet("color: #888; font-size: 12px;")
        self.upd_status_lbl.setWordWrap(True)
        ul.addWidget(self.upd_status_lbl)
        ul.addStretch()
        tabs.addTab(tab_upd, "Güncelleme" if _APP_LANG == "tr" else "Updates")

        # ── Alt butonlar ──────────────────────────────────
        btn_row = QHBoxLayout()
        btn_cancel = QPushButton(tr("İptal"))
        btn_cancel.clicked.connect(self.hide)
        btn_save = QPushButton(tr("Kaydet"))
        btn_save.setDefault(True)
        btn_save.clicked.connect(self._save)
        btn_row.addStretch()
        btn_row.addWidget(btn_cancel)
        btn_row.addWidget(btn_save)

        root.addWidget(tabs)
        root.addLayout(btn_row)

    def _update_llm_stack(self, index):
        mode = self.llm_mode_combo.itemData(index)
        idx = {"local": 0, "remote": 1, "cli": 2}.get(mode, 0)
        self.llm_stack.setCurrentIndex(idx)

    def _update_cli_models(self, tool_key):
        """Seçilen araca göre model dropdown'unu günceller."""
        cfg = KNOWN_CLI_TOOLS.get(tool_key, {})
        models = cfg.get("models", [])
        self.cli_model_combo.clear()
        if models:
            self.cli_model_combo.addItems(models)
            self.cli_model_combo.setEnabled(True)
        else:
            self.cli_model_combo.setPlaceholderText(tr("Bu araç model seçimi desteklemiyor"))
            self.cli_model_combo.setEnabled(False)

    def _browse_workspace(self):
        """Dizin seçici açıp workspace path'i doldurur."""
        current = self.workspace_input.text().strip() or ""
        directory = QFileDialog.getExistingDirectory(
            self, tr("Çalışma Alanı Seç"), current or "/home"
        )
        if directory:
            self.workspace_input.setText(directory)

    def _save(self):
        s = self.settings
        
        current_hk = self.hotkey_input.text().strip()
        if "Tuşlara basın" not in current_hk and "Press keys" not in current_hk:
            s.set("hotkey", current_hk)
            
        s.set("overlay_position",        self.pos_combo.currentData())
        s.set("language",                self.lang_combo.currentData())
        s.set("app_language",            self.app_lang_combo.currentData())
        s.set("workspace_dir",           self.workspace_input.text().strip())
        s.set("sticky_window",           self.sticky_cb.isChecked())
        s.set("overlay_timeout_seconds", self.timeout_spin.value())
        s.set("overlay_display_seconds", self.display_spin.value())
        s.set("mic_sensitivity",         self.sensitivity_spin.value())
        s.set("pause_threshold",         self.pause_spin.value() / 10.0)
        s.set("phrase_time_limit",       self.phrase_limit_spin.value())

        mode = self.llm_mode_combo.currentData()
        s.set("llm_mode",            mode)
        s.set("local_api_url",       self.local_url_input.text().strip())
        s.set("local_model",         self.local_model_input.text().strip())
        s.set("remote_api_url",      self.remote_url_input.text().strip())
        s.set("remote_model",        self.remote_model_input.text().strip())
        s.set("remote_api_key",      self.api_key_input.text().strip())
        s.set("llm_cli_tool_key",    self.cli_tool_combo.currentText())
        s.set("llm_cli_model",       self.cli_model_combo.currentText().strip())
        s.set("llm_cli_extra_args",  self.cli_extra_input.text().strip())

        s.set("system_prompt",            self.sys_prompt_input.toPlainText().strip())
        
        s.set("auto_show_popup",          self.popup_check.isChecked())
        s.set("optimize_dictation",       self.opt_check.isChecked())
        s.set("auto_allow_clipboard",     self.clip_check.isChecked())
        s.set("enable_tool_calling",      self.tool_enable_check.isChecked())
        s.set("require_confirm_on_tool",  self.tool_confirm_check.isChecked())
        s.set("tool_max_iterations",      self.tool_iter_spin.value())
        s.set("history_max_turns",        self.hist_spin.value())
        s.set("auto_request_log",         self.req_log_check.isChecked())
        s.set("auto_check_updates",       self.upd_auto_check.isChecked())

        if self.hotkey_manager:
            self.hotkey_manager.update_hotkey(s.get("hotkey"))

        # Eğer uygulama dili değiştiyse uyar
        global _APP_LANG
        new_app_lang = self.app_lang_combo.currentData()
        if new_app_lang != _APP_LANG:
            _APP_LANG = new_app_lang
            QMessageBox.information(self, tr("Kaydedildi"), tr("Ayarlar başarıyla kaydedildi.") + "\n\n" + tr("Dil değişikliklerinin tamamen uygulanması için lütfen uygulamayı yeniden başlatın."))
        else:
            QMessageBox.information(self, tr("Kaydedildi"), tr("Ayarlar başarıyla kaydedildi."))
        self.hide()

# ──────────────────────────────────────────────────────────
#  Ana Uygulama Yöneticisi
# ──────────────────────────────────────────────────────────
class AppManager:
    def __init__(self):
        self.app = QApplication(sys.argv)
        self.app.setQuitOnLastWindowClosed(False)
        self.app.setApplicationName("Linux-AI-Assistant")
        
        self._check_single_instance()

        self.settings = SettingsManager()
        global _APP_LANG
        _APP_LANG = self.settings.get("app_language", "tr")
        try:
            set_core_lang(_APP_LANG)
        except Exception:
            pass

        self._is_listening = False
        self._last_hotkey_time = 0

        self.router = Router(settings=self.settings, confirm_callback=self._confirm_callback)
        self.audio    = AudioListener(settings=self.settings)

        self.comm = Communicate()
        Q = Qt.ConnectionType.QueuedConnection
        self.comm.show_overlay.connect(self._show_overlay, Q)
        self.comm.hide_overlay.connect(self._hide_overlay, Q)
        self.comm.update_text.connect(self._update_text, Q)
        self.comm.start_waveform.connect(self._start_waveform, Q)
        self.comm.stop_waveform.connect(self._stop_waveform, Q)
        self.comm.ask_confirm.connect(self._ask_confirm_gui, Q)
        self.comm.ask_show_response.connect(self._ask_show_response_gui, Q)
        self.comm.ask_clipboard.connect(self._ask_clipboard_gui, Q)
        self.comm.show_response.connect(self._show_response_gui, Q)
        self.comm.update_checked.connect(self._on_update_checked, Q)
        self.waveform     = WaveformWidget(self.settings)
        self.overlay      = OverlayWindow(self.settings, self.waveform)
        
        self.comm.show_context_btns.connect(lambda contexts: self.overlay.show_context_btns(contexts), Q)
        self.comm.hide_context_btns.connect(self.overlay.hide_context_btns, Q)
        
        self.overlay.stop_clicked.connect(self.on_hotkey_triggered)
        self.overlay.context_added.connect(self._on_context_added)

        self.hotkey = HotkeyManager(self.settings.get("hotkey"), self.on_hotkey_triggered)
        self.settings_win = SettingsWindow(self.settings, self.hotkey)
        self.hotkey.start()
        
        # Extension Server Başlat (Tarayıcı eklentisinden veri almak için)
        try:
            from src.context.server import start_server, signals as ext_signals
            self.ext_server = start_server()
            ext_signals.data_received.connect(self._on_extension_data, Q)
            # browser_action tool'u için göndericiyi Router → LLMClient zincirine enjekte et
            if hasattr(self.ext_server, 'send_browser_command'):
                self.router.set_browser_sender(self.ext_server.send_browser_command)
        except Exception as e:
            logger.error(f"Extension server başlatılamadı: {e}")
        
        self._setup_tray()
        self._is_listening = False
        # Açılışta sessiz güncelleme denetimi (arka planda, ayara bağlı)
        if self.settings.get("auto_check_updates", True):
            threading.Thread(target=self._check_updates_bg, args=(True,), daemon=True).start()

    def _check_updates_bg(self, silent):
        try:
            info = updater.check_for_updates()
        except Exception as e:
            info = {"ok": False, "error": str(e), "update_available": False}
        self.comm.update_checked.emit(info, silent)

    def _on_update_checked(self, info, silent):
        """Güncelleme denetimi sonucu (ana thread). Sessiz modda sadece
        yeni + atlanmamış sürümde diyalog açar; manuel modda her sonucu bildirir."""
        if not isinstance(info, dict):
            return
        if info.get("update_available"):
            tag = info.get("tag", "") or f"v{info.get('latest_version', '')}"
            if silent and self.settings.get("skipped_update_version", "") == tag:
                return  # Kullanıcı bu sürümü atlamış
            self._show_update_dialog(info)
        elif not silent:
            if info.get("ok"):
                self.tray.showMessage("Linux-AI-Assistant",
                                      f"{tr('Kullanılabilir güncelleme yok.')} (v{__version__})",
                                      QSystemTrayIcon.MessageIcon.Information, 4000)
            else:
                self.tray.showMessage("Linux-AI-Assistant", tr("Güncelleme denetimi başarısız."),
                                      QSystemTrayIcon.MessageIcon.Warning, 4000)

    def _show_update_dialog(self, info):
        from PyQt6.QtWidgets import QTextBrowser
        latest = info.get("latest_version", "?")
        current = info.get("current_version", __version__)
        notes = (info.get("release_notes", "") or "").strip()
        if len(notes) > 4000:
            notes = notes[:4000] + "\n…"
        dlg = QDialog()
        dlg.setWindowTitle(f"{tr('Yeni sürüm mevcut')}: v{latest}")
        dlg.setWindowFlags(dlg.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
        dlg.resize(520, 420)
        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)
        title = QLabel(f"<b>v{current} → v{latest}</b>")
        title.setStyleSheet("font-size: 14px;")
        layout.addWidget(title)
        browser = QTextBrowser()
        browser.setMarkdown(notes if notes else "(release notes yok)")
        browser.setOpenExternalLinks(True)
        layout.addWidget(browser)
        self._upd_status_lbl = QLabel("")
        self._upd_status_lbl.setWordWrap(True)
        self._upd_status_lbl.setStyleSheet("color: #888; font-size: 12px;")
        layout.addWidget(self._upd_status_lbl)
        btn_row = QHBoxLayout()
        btn_page = QPushButton(tr("Release Sayfasını Aç"))
        btn_page.clicked.connect(lambda: __import__("webbrowser").open(info.get("html_url", updater.HTML_RELEASES)))
        btn_skip = QPushButton(tr("Bu Sürümü Atla"))
        btn_later = QPushButton(tr("Daha Sonra"))
        btn_now = QPushButton(tr("Şimdi Güncelle"))
        btn_now.setDefault(True)
        btn_row.addWidget(btn_page)
        btn_row.addStretch()
        btn_row.addWidget(btn_skip)
        btn_row.addWidget(btn_later)
        btn_row.addWidget(btn_now)
        layout.addLayout(btn_row)

        def _do_skip():
            self.settings.set("skipped_update_version", info.get("tag", "") or f"v{latest}")
            dlg.reject()

        def _do_update():
            for b in (btn_page, btn_skip, btn_later, btn_now):
                b.setEnabled(False)
            self._upd_status_lbl.setText(tr("Güncelleniyor, lütfen bekleyin..."))

            def _bg():
                try:
                    res = updater.perform_update(PROJECT_ROOT)
                except Exception as e:
                    res = {"ok": False, "restart_needed": False, "message": str(e)}
                QTimer.singleShot(0, lambda: _after_update(res, dlg))

            threading.Thread(target=_bg, daemon=True).start()

        btn_skip.clicked.connect(_do_skip)
        btn_later.clicked.connect(dlg.reject)
        btn_now.clicked.connect(_do_update)
        dlg.exec()

    def _after_update(self, res, dlg):
        if res.get("ok") and res.get("restart_needed"):
            self.settings.set("skipped_update_version", "")
            ans = QMessageBox.question(
                dlg, "Linux-AI-Assistant",
                f"{res.get('message', '')}\n\n{tr('Güncelleme tamamlandı. Yeniden başlatılsın mı?')}",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes)
            dlg.accept()
            if ans == QMessageBox.StandardButton.Yes:
                self._restart_app()
        elif res.get("ok"):
            QMessageBox.information(dlg, "Linux-AI-Assistant", res.get("message", ""))
            dlg.accept()
        else:
            if hasattr(self, "_upd_status_lbl"):
                self._upd_status_lbl.setText(res.get("message", ""))
            # Diyalog açık kalır; kullanıcı Release sayfasından manuel indirebilir.

    def _restart_app(self):
        """Aynı Python yorumlayıcısıyla app.py'yi yeniden başlatır."""
        import subprocess
        import sys as _sys
        app_entry = os.path.join(PROJECT_ROOT, "app.py")
        try:
            os.execv(_sys.executable, [_sys.executable, app_entry] + _sys.argv[1:])
        except Exception:
            self._quit()
            subprocess.Popen([_sys.executable, app_entry])

    def _on_extension_data(self, data):
        """Tarayıcı eklentisinden veri geldiğinde tetiklenir."""
        logger.info(f"Extension'dan veri geldi: {data.get('url')}")
        
        ctx = {
            "title": data.get("title", ""),
            "url": data.get("url", ""),
            "type": "extension",
            "content": data.get("content", ""),
            "selection": data.get("selection", ""),
            "contentType": data.get("contentType", "")
        }
        
        # Eğer henüz dinlenmiyorsa, ÖNCE uyandır (çünkü uyanma anında liste sıfırlanıyor)
        if not getattr(self, '_is_listening', False):
            self.on_hotkey_triggered()
            
        if not hasattr(self, '_context_queue'):
            self._context_queue = []
            
        self._context_queue.append(ctx)
            
        # Zaten dinleniyorsa veya yeni uyandıysa bilgilendirme geç
        if getattr(self, '_is_listening', False):
            if data.get('selection'):
                self.comm.update_text.emit(tr("Seçili metin eklendi"))
            elif "youtube.com/watch" in data.get('url', '') or "youtu.be/" in data.get('url', ''):
                self.comm.update_text.emit(tr("Video eklendi"))
            elif data.get('url', '').lower().endswith('.pdf') or data.get('contentType') == 'application/pdf':
                self.comm.update_text.emit(tr("PDF belgesi eklendi"))
            elif "mail.google.com" in data.get('url', ''):
                self.comm.update_text.emit(tr("E-posta eklendi"))
            else:
                self.comm.update_text.emit(tr("Tarayıcı sekmesi eklendi"))
            
            def _reset_text():
                if getattr(self, '_is_listening', False):
                    self.comm.update_text.emit(tr("Seni dinliyorum..."))
                    
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(5000, _reset_text)

    def _setup_tray(self):
        pix = QPixmap(64, 64)
        pix.fill(Qt.GlobalColor.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        # Sade Mikrofon Çizimi
        p.setBrush(QColor(80, 150, 255)) # Mavi mikrofon başlığı
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(24, 12, 16, 26, 8, 8)
        
        pen = QPen(QColor(180, 180, 180), 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawArc(18, 28, 28, 20, 0, -180 * 16) # U-Şeklinde Tutacak
        p.drawLine(32, 48, 32, 54)              # Dikey Stand
        p.drawLine(24, 54, 40, 54)              # Yatay Taban
        p.end()

        self.tray = QSystemTrayIcon(QIcon(pix), self.app)
        self.tray.setToolTip("Linux-AI-Assistant\nSağ tıkla → Menü\nSol tıkla → Ayarlar" if _APP_LANG == "tr" else "Linux-AI-Assistant\nRight-click → Menu\nLeft-click → Settings")
        
        def tray_clicked(reason):
            if reason == QSystemTrayIcon.ActivationReason.Trigger:
                self.settings_win.show()
                self.settings_win.raise_()
        self.tray.activated.connect(tray_clicked)

        menu = QMenu()
        act_s = QAction(tr("Ayarlar"), self.app)
        act_s.triggered.connect(lambda: (self.settings_win.show(), self.settings_win.raise_()))
        menu.addAction(act_s)
        menu.addSeparator()
        act_u = QAction(tr("Güncellemeleri Denetle"), self.app)
        act_u.triggered.connect(lambda: threading.Thread(
            target=self._check_updates_bg, args=(False,), daemon=True).start())
        menu.addAction(act_u)
        menu.addSeparator()
        act_h = QAction(tr("Hafızayı Temizle"), self.app)
        act_h.setToolTip(tr("Yapay zekanın hatırladığı konuşma geçmişini siler."))
        act_h.triggered.connect(self._clear_history)
        menu.addAction(act_h)
        menu.addSeparator()
        act_q = QAction(tr("Çıkış"), self.app)
        act_q.triggered.connect(self._quit)
        menu.addAction(act_q)
        self.tray.setContextMenu(menu)
        self.tray.show()
        # Açılış geri bildirimi: uygulama tray-only olduğundan kullanıcı
        # tıkladıktan sonra "hiçbir şey olmadı" sanmasın diye tek seferlik balon.
        try:
            self.tray.showMessage(
                "Linux-AI-Assistant",
                tr("Arka planda çalışıyor") + " — " + tr("Sistem çekmecesindeki ikona sağ tıklayarak menüyü açabilirsiniz."),
                QSystemTrayIcon.MessageIcon.Information, 4000)
        except Exception:
            pass

    def _clear_history(self):
        self.router.llm.clear_history()
        self.tray.showMessage("Linux-AI-Assistant", tr("Konuşma geçmişi temizlendi."),
                              QSystemTrayIcon.MessageIcon.Information, 3000)

    def _check_single_instance(self):
        import signal
        is_trigger = "--trigger" in sys.argv
        self._lock_file = "/tmp/ai_dikte.lock"
        self._lock_fd = os.open(self._lock_file, os.O_RDWR | os.O_CREAT, 0o666)
        try:
            fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            os.ftruncate(self._lock_fd, 0)
            os.write(self._lock_fd, str(os.getpid()).encode())
        except (BlockingIOError, OSError):
            if is_trigger:
                try:
                    with open(self._lock_file, 'r') as f:
                        pid = int(f.read().strip())
                    os.kill(pid, signal.SIGUSR1)
                except Exception:
                    pass
                sys.exit(0)
            else:
                msg = QMessageBox()
                msg.setIcon(QMessageBox.Icon.Warning)
                msg.setWindowTitle(tr("Uygulama Zaten Çalışıyor"))
                msg.setText(tr("Linux-AI-Assistant şu anda arka planda zaten açık!"))
                msg.setInformativeText("Lütfen sağ alt köşedeki (Sistem Çekmecesi) ikona sağ tıklayıp işlem yapın. Kapatmak için Çıkış'a basabilirsiniz.\n\nEğer asistanı manuel tetiklemek istiyorsanız '--trigger' argümanı ile çalıştırın." if _APP_LANG == "tr" else "Right-click the system tray icon to use it, or Exit to close it.\n\nTo trigger the assistant manually, run with the '--trigger' argument.")
                msg.exec()
                sys.exit(0)

        # Sinyal dinleyiciyi kur (Wayland ve X11 uyumlu evrensel tetikleyici)
        self._sigusr1_received = False
        signal.signal(signal.SIGUSR1, self._sig_handler)
        self._sig_timer = QTimer(self.app)
        self._sig_timer.timeout.connect(self._check_sigusr1)
        self._sig_timer.start(100)

    def _sig_handler(self, signum, frame):
        self._sigusr1_received = True

    def _check_sigusr1(self):
        if self._sigusr1_received:
            self._sigusr1_received = False
            self.on_hotkey_triggered()

    def on_hotkey_triggered(self):
        current_time = time.time()
        # Çok hızlı arka arkaya basılmasını (klavye auto-repeat) engelle (500ms)
        if current_time - self._last_hotkey_time < 0.5:
            return
        self._last_hotkey_time = current_time

        if self._is_listening:
            self.comm.hide_overlay.emit()
            self._is_listening = False
            return
        
        self._is_listening = True
        self._context_queue = [] # reset context queue
        
        # Sadece overlayi göster, KDE tepsisinden bildirim atıp kalp atışını tetikleme
        self.comm.show_overlay.emit()
        self.comm.hide_context_btns.emit()
        self.comm.start_waveform.emit()
        # Panoyu ana thread üzerinde okuyoruz (QClipboard thread-safe değildir, çökme yapabilir)
        import PyQt6.QtGui as QtGui
        current_clip = QtGui.QGuiApplication.clipboard().text().strip()

        threading.Thread(target=self._check_context_bg, daemon=True).start()
        threading.Thread(target=self._process, args=(current_clip,), daemon=True).start()

    def _check_context_bg(self):
        contexts = get_active_contexts()
        if contexts:
            self.comm.show_context_btns.emit(contexts)

    def _on_context_added(self, title, url):
        self._context_queue.append({"title": title, "url": url})

    def _process(self, current_clipboard_text):
        try:
            self.comm.update_text.emit(tr("Dinleniyor..."))
            text = self.audio.listen_and_transcribe()
            self.comm.stop_waveform.emit()

            if not self._is_listening:
                return

            if not text:
                self.comm.update_text.emit(tr("Ses algılanamadı."))
                time.sleep(1.8)
                self.comm.hide_overlay.emit()
                self._is_listening = False
                return

            # Sesi aldık, önce bunu göster
            self.comm.update_text.emit(tr("Ses Kaydı Alındı."))
            time.sleep(0.6) # Yarım saniye kadar göster
            
            if not self._is_listening:
                return
                
            if self.settings.get("optimize_dictation", False):
                self.comm.update_text.emit(tr("Akıllı Dikte Düzeltici Çalışıyor..."))
                opt_system = (
                    "Sen sadece metin düzelten ve iyileştiren bir araçsın. Asla yoruma, kendi fikirlerine, 'Hemen yapıyorum', 'Harika' gibi laflara veya açıklamalara yer vermezsin. "
                    "Kullanıcının verdiği metni, komut veya sohbet formatına uygun en temiz ve pürüzsüz hale getir. YALNIZCA düzeltilmiş metni yaz."
                )
                opt_prompt = "Düzeltilecek Kullanıcı Metni:\n\n" + text
                try:
                    # Router'ın içindeki LLM Client üzerinden doğrudan soralım
                    text = self.router.llm.generate_response(
                        system_prompt=opt_system,
                        user_prompt=opt_prompt,
                        skip_injection=True
                    ).strip()
                    print(f"\n[İyileştirilmiş Dikte]: {text}\n", flush=True)
                except Exception as e:
                    logger.error(f"Dikte iyileştirme hatası: {e}")

            # --- BAĞLAM FARKINDALIĞI (PANO VE EKRAN) ---
            # Hibrit geçiş: Tool calling aktifken (remote/local) eski keyword
            # tabanlı pano/ekran enjeksiyonu atlanır; LLM ihtiyacı oldukça
            # get_clipboard_text / read_screen_text araçlarını kendisi çağırır.
            context = None
            lower_text = text.lower()
            has_attachments = bool(getattr(self, '_context_queue', None))
            llm_mode = self.settings.get("llm_mode", "local")
            tool_path_active = (
                llm_mode in ("remote", "local")
                and self.settings.get("enable_tool_calling", True)
            )

            # Eğer halihazırda ek (browser extension verisi) gönderilmişse, sadece "ekran" ve "pano" gibi kelimeleri dikkate al.
            # Yoksa "bu maile", "şunu oku" gibi şeyler sürekli ekran okuma pop-up'ı çıkarır.
            if has_attachments:
                context_keywords = ["pano", "kopyala", "ekran"]
            else:
                context_keywords = ["pano", "kopyala", "bunu", "şunu", "bu ", "buradaki", "ekran"]

            if not tool_path_active and any(kw in lower_text for kw in context_keywords):
                wants_screen = "ekran" in lower_text
                has_permission = self.settings.get("auto_allow_clipboard", False)

                if not has_permission:
                    preview = ""
                    if wants_screen:
                        preview += "[DİKKAT: Ekranınızın Görüntüsü Çekilip Analiz Edilecek!]\n\n"
                    if current_clipboard_text:
                        preview += f"[PANO ÖNİZLEME]: {current_clipboard_text[:100]}...\n"

                    result = [False]
                    ev = threading.Event()
                    self.comm.ask_clipboard.emit(result, ev, preview.strip())
                    ev.wait()
                    has_permission = result[0]

                if has_permission:
                    clip_text = current_clipboard_text
                    screen_text = ""

                    if wants_screen:
                        self.comm.update_text.emit(tr("Ekran Okunuyor (OCR)..."))
                        try:
                            from src.tools.executor import read_screen_via_ocr
                            screen_text = read_screen_via_ocr()
                        except Exception as e:
                            logger.error(f"OCR Hatası: {e}")

                    combined_context = ""
                    if clip_text:
                        combined_context += f"[Kullanıcının Panosundaki Metin]:\n{clip_text}\n\n"
                    if screen_text:
                        combined_context += f"[Kullanıcının Ekranındaki Metin (OCR)]:\n{screen_text}\n\n"

                    context = combined_context.strip()

            if getattr(self, '_context_queue', None):
                yt_context = "[Kullanıcının Seçtiği Ek Bağlamlar (Ekler)]:\n"
                for yt in self._context_queue:
                    url_str = yt['url'] if yt['url'] else "(Bağlantı bulunamadı, bu başlığı / içeriği referans alabilirsiniz)"
                    yt_context += f"Başlık/İçerik: {yt['title']}\nDetay/URL: {url_str}\n"
                    
                    if yt['url'] and ("youtube.com/watch" in yt['url'] or "youtu.be/" in yt['url']):
                        try:
                            from youtube_transcript_api import YouTubeTranscriptApi
                            import urllib.parse as urlparse
                            
                            self.comm.update_text.emit(tr("Video Altyazısı Okunuyor..."))
                            
                            video_id = None
                            if "youtu.be/" in yt['url']:
                                video_id = yt['url'].split("youtu.be/")[1].split("?")[0]
                            else:
                                parsed = urlparse.urlparse(yt['url'])
                                video_id = urlparse.parse_qs(parsed.query).get('v', [None])[0]
                                
                            if video_id:
                                ytt_api = YouTubeTranscriptApi()
                                t_list = ytt_api.list(video_id)
                                t_obj = None
                                try:
                                    t_obj = t_list.find_transcript(['tr', 'en'])
                                except:
                                    try:
                                        t_obj = t_list.find_generated_transcript(['tr', 'en'])
                                    except:
                                        t_obj = next(iter(t_list))
                                
                                if t_obj:
                                    transcript = t_obj.fetch()
                                    text_lines = [t.text for t in transcript]
                                    full_text = " ".join(text_lines)
                                    if len(full_text) > 25000:
                                        full_text = full_text[:25000] + "... (Videonun tamamı çok uzun olduğu için kesildi)"
                                    yt_context += f"\n[VİDEONUN TAM İÇERİĞİ / ALTYAZISI]:\n{full_text}\n"
                        except Exception as e:
                            logger.error(f"YouTube altyazı çekilemedi: {e}")
                            
                    elif yt.get('type') == 'extension':
                        # Tarayıcı eklentisinden gelen verileri işle
                        if yt.get('selection'):
                            yt_context += f"\n[TARAYICIDA SEÇİLEN METİN]:\n{yt['selection']}\n"
                        
                        is_yt = yt['url'] and ("youtube.com/watch" in yt['url'] or "youtu.be/" in yt['url'])
                        is_pdf = yt['url'] and (yt['url'].lower().endswith('.pdf') or yt.get('contentType') == 'application/pdf')
                        
                        if is_pdf:
                            self.comm.update_text.emit(tr("PDF İçeriği Okunuyor..."))
                            try:
                                pdf_path = None
                                import urllib.parse
                                if yt['url'].startswith('file://'):
                                    pdf_path = urllib.parse.unquote(yt['url'].replace('file://', ''))
                                else:
                                    import requests, tempfile
                                    r = requests.get(yt['url'], stream=True, timeout=10)
                                    if r.status_code == 200:
                                        with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as f:
                                            for chunk in r.iter_content(chunk_size=8192):
                                                f.write(chunk)
                                            pdf_path = f.name
                                if pdf_path:
                                    import subprocess
                                    out = subprocess.run(['pdftotext', '-layout', pdf_path, '-'], capture_output=True, text=True)
                                    pdf_text = out.stdout.strip()
                                    if len(pdf_text) > 30000:
                                        pdf_text = pdf_text[:30000] + "\n... (PDF çok uzun olduğu için kesildi)"
                                    yt_context += f"\n[PDF BELGESİ İÇERİĞİ]:\n{pdf_text}\n"
                            except Exception as e:
                                logger.error(f"PDF Okuma hatası: {e}")
                                
                        elif yt.get('content') and not is_yt:
                            content = yt['content']
                            if len(content) > 30000:
                                content = content[:30000] + "\n... (Sayfa çok uzun olduğu için kesildi)"
                            yt_context += f"\n[TARAYICIDAKİ SAYFANIN TAM METNİ]:\n{content}\n"
                            
                context = (context + "\n\n" + yt_context) if context else yt_context

            self.comm.update_text.emit(tr("Yapay Zekanın Cevabı Bekleniyor..."))

            # --- LLM / YÖNLENDİRME ---
            response = self.router.parse_and_route(text, context=context)
            
            if not self._is_listening:
                return
            
            wants_popup = False
            import re

            # Not: Eski [BROWSER_ACTION: {...}] etiket yolu kaldırıldı.
            # Tarayıcı kontrolü artık browser_action tool'u üzerinden yapılıyor.

            # Daha esnek bir kontrol (büyük/küçük harf, alt tire veya boşluk, türkçe karakter vs.)
            if re.search(r'\[\s*EKRANDA[_ ]G[OÖ]STER\s*\]', response, re.IGNORECASE):
                wants_popup = True
                response = re.sub(r'\[\s*EKRANDA[_ ]G[OÖ]STER\s*\]', '', response, flags=re.IGNORECASE).strip()
            else:
                # Fallback: model etiketi unuttuysa ama okuma amaçlı araç kullanıp
                # uzun bir yanıt ürettiyse kullanıcı muhtemelen cevabı görmek istiyor.
                # (Yazma/çalıştırma araçlarında "arka planda tamamlandı" davranışı korunur.)
                tools_used = getattr(self.router.llm, 'last_tools_used', []) or []
                read_only_tools = {"read_file", "list_directory", "get_clipboard_text",
                                   "read_screen_text", "get_active_window_context"}
                if (tools_used and set(tools_used) <= read_only_tools
                        and not getattr(self, '_context_queue', None)
                        and len(response) >= 200
                        and '[LLM Hatası' not in response):
                    logger.info(f"[EKRANDA_GOSTER] etiketi yok ama fallback ile popup açılıyor (araçlar: {tools_used})")
                    wants_popup = True

            print(f"\n[Dikte]: {text}\n[Yanıt]: {response}\n", flush=True)

            if wants_popup:
                if self.settings.get("auto_show_popup", False):
                    self.comm.hide_overlay.emit()
                    self._is_listening = False
                    self.comm.show_response.emit(response)
                else:
                    result = [False]
                    ev = threading.Event()
                    self.comm.ask_show_response.emit(result, ev)
                    ev.wait()
                    
                    self.comm.hide_overlay.emit()
                    self._is_listening = False
                    
                    if result[0]:
                        time.sleep(0.1) # Qt'nin önceki popup'ı temizlemesi için ufak bir bekleme
                        self.comm.show_response.emit(response)
            else:
                self.comm.update_text.emit(tr("İşlem arka planda tamamlandı ✓"))
                time.sleep(2.5)
                self.comm.hide_overlay.emit()
                self._is_listening = False

        except Exception as e:
            logger.error(f"İşlem hatası: {e}")
            # Hata durumunda bildirim göstermiyoruz, sadece loglara yazılıyor
        finally:
            self.comm.hide_overlay.emit()
            self._is_listening = False

    def _confirm_callback(self, summary):
        result = [False]
        ev = threading.Event()
        self.comm.ask_confirm.emit(summary, result, ev)
        ev.wait()
        return result[0]

    def _play_notification_sound(self):
        import subprocess
        try:
            subprocess.Popen(["paplay", "/usr/share/sounds/freedesktop/stereo/message.oga"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass

    def _ask_confirm_gui(self, summary, result_list, event):
        """Sade onay penceresi: başlık + tek soru + küçük detay."""
        if isinstance(summary, str):
            summary = {"title": tr("Onay"), "question": summary}
        title = summary.get("title") or tr("Onay")
        question = summary.get("question") or ""
        detail = summary.get("detail")

        self._play_notification_sound()
        dlg = ConfirmDialog(title, question, detail)
        result_list[0] = (dlg.exec() == QDialog.DialogCode.Accepted)
        event.set()

    def _ask_show_response_gui(self, result_list, event):
        msg = QMessageBox()
        msg.setIcon(QMessageBox.Icon.Question)
        msg.setWindowTitle(tr("Yanıtı Göster"))
        msg.setText(tr("Yapay zeka işlemini tamamladı ve size bir yanıt vermek istiyor."))
        msg.setInformativeText(tr("Cevabı ekranda görmek istiyor musunuz?"))
        btn_yes = msg.addButton(tr("Evet"), QMessageBox.ButtonRole.YesRole)
        btn_no = msg.addButton(tr("Hayır"), QMessageBox.ButtonRole.NoRole)
        msg.setDefaultButton(btn_yes)
        msg.setWindowFlags(msg.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)

        self._play_notification_sound()
        msg.exec()
        result_list[0] = (msg.clickedButton() == btn_yes)
        event.set()

    def _ask_clipboard_gui(self, result_list, event, clipboard_text):
        msg = QMessageBox()
        msg.setIcon(QMessageBox.Icon.Question)
        msg.setWindowTitle(tr("Bağlam Farkındalığı (Pano ve Ekran Erişimi)"))
        msg.setText(tr("Cümlenizde bağlam gerektiren kelimeler tespit edildi. Yapay zekaya panonuzdaki/ekranınızdaki metin de gönderilsin mi?"))
        
        btn_yes = msg.addButton(tr("Evet"), QMessageBox.ButtonRole.YesRole)
        btn_no = msg.addButton(tr("Hayır"), QMessageBox.ButtonRole.NoRole)
        msg.setDefaultButton(btn_yes)
        msg.setWindowFlags(msg.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)

        self._play_notification_sound()
        msg.exec()
        result_list[0] = (msg.clickedButton() == btn_yes)
        event.set()

    def _show_response_gui(self, text):
        self._resp_win = ResponseWindow(text)
        self._resp_win.exec()

    def _show_overlay(self):
        flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
        if self.settings.get("sticky_window", True):
            flags |= Qt.WindowType.ToolTip
        else:
            flags |= Qt.WindowType.Tool
        
        # Sadece bayrak değiştiyse güncelle (gereksiz hide/show olmasın)
        if self.overlay.windowFlags() != flags:
            self.overlay.setWindowFlags(flags)
            
        self.overlay.reposition()
        self.overlay.show()
        self.overlay.raise_()
        self.overlay.activateWindow()

    def _hide_overlay(self):
        self.overlay.hide()

    def _update_text(self, text):
        self.overlay.set_text(text)

    def _start_waveform(self):
        self.waveform.start()

    def _stop_waveform(self):
        self.waveform.stop()

    def _quit(self):
        self.hotkey.stop()
        self.waveform.cleanup()
        self.app.quit()

    def run(self):
        sys.exit(self.app.exec())


if __name__ == "__main__":
    # Doğrudan çalıştırma desteklenmez — proje kökünden `python app.py` kullanın.
    raise SystemExit("Bu modül doğrudan çalıştırılamaz. Proje kökünde: python app.py")
