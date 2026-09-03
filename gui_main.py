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
    QFileDialog, QTextEdit
)
from PyQt6.QtCore import Qt, pyqtSignal, QObject, QTimer, QEvent
from PyQt6.QtGui import QIcon, QAction, QFont, QPainter, QColor, QPixmap, QPen, QKeyEvent, QKeySequence

from settings_manager import SettingsManager
from hotkey_manager import HotkeyManager
from audio_listener import AudioListener
from router import Router
from llm_client import LLMClient
from cli_tools_registry import KNOWN_CLI_TOOLS

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
    "Kapat": "Close"
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
    ask_confirm       = pyqtSignal(str, str, object, object) # cmd_str, explanation, result_list, threading.Event
    ask_show_response = pyqtSignal(object, object) # result_list, threading.Event
    ask_clipboard     = pyqtSignal(object, object, str) # result_list, threading.Event, text
    show_response     = pyqtSignal(str) # text

# ──────────────────────────────────────────────────────────
#  Kısayol Yakalayıcı (KDE-style)
# ──────────────────────────────────────────────────────────
class HotkeyCaptureWidget(QLineEdit):
    def __init__(self, current_hotkey=""):
        super().__init__(current_hotkey)
        self.setReadOnly(True)
        self.setPlaceholderText("Kısayol atamak için tıklayın...")
        self.capturing = False

    def mousePressEvent(self, event):
        self.capturing = True
        self.setText("Tuşlara basın... (İptal için ESC)")
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

    def __init__(self, settings, waveform):
        super().__init__()
        self.settings = settings
        self.waveform = waveform
        self.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, False)
        self._init_ui()

    def _init_ui(self):
        global _APP_LANG
        _APP_LANG = self.settings.get('app_language', 'tr')
        # BypassWindowManagerHint bazı WM'lerde pencereyi görünmez yapıyor, kaldırıldı
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        container = QWidget(self)
        container.setObjectName("oc")
        container.setStyleSheet("""
            #oc {
                background: rgba(16, 16, 20, 235);
                border-radius: 14px;
                border: 1px solid rgba(255,255,255,25);
            }
        """)
        container.setFixedWidth(250)
        container.setMinimumHeight(60)
        
        inner = QVBoxLayout(container)
        inner.setContentsMargins(18, 12, 18, 12)
        inner.setSpacing(6)

        self.label = QLabel(tr("Dinleniyor..."))
        self.label.setStyleSheet("color: rgba(255,255,255,190); font-size: 13px; background: transparent;")
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label.setFont(QFont("Sans Serif", 11))
        self.label.setWordWrap(True) # Uzun metinlerde alt satıra geçsin
        self.label.setFixedSize(214, 32) # Metin değişince kutu büyümesin diye sabit boyut

        self.waveform.setStyleSheet("background: transparent;")
        inner.addWidget(self.label)
        inner.addWidget(self.waveform)

        self.stop_btn = QPushButton("Sustur")
        self.stop_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(220, 50, 50, 200);
                color: white;
                border-radius: 4px;
                padding: 4px;
            }
            QPushButton:hover { background-color: rgba(255, 70, 70, 255); }
        """)
        self.stop_btn.clicked.connect(self.stop_clicked.emit)
        self.stop_btn.hide()
        inner.addWidget(self.stop_btn)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(container)
        
        self.setMinimumWidth(250)
        self.setMaximumWidth(450) # Barın aşırı uzamasını engelle
        self.resize(260, 100)

    def reposition(self):
        screen = QApplication.primaryScreen().availableGeometry()
        pos    = self.settings.get("overlay_position", "Bottom-Right")
        m      = 28
        w, h   = self.width(), self.height()
        coords = {
            "Bottom-Right": (screen.width()  - w - m, screen.height() - h - m),
            "Bottom-Left":  (m,                        screen.height() - h - m),
            "Top-Right":    (screen.width()  - w - m, m),
            "Top-Left":     (m,                        m),
            "Center":       ((screen.width()  - w) // 2, (screen.height() - h) // 2),
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

# ──────────────────────────────────────────────────────────
#  Ayarlar penceresi  (Dialog YOK — sıradan QWidget)
#  Dialog bayrağı X11'de global keyboard grab yapıyor;
#  bu yüzden tamamen kaldırıldı.
# ──────────────────────────────────────────────────────────
# ──────────────────────────────────────────────────────────
#  Yapay Zeka Yanıt Penceresi (Özel QDialog)
# ──────────────────────────────────────────────────────────
from PyQt6.QtWidgets import QDialog, QTextBrowser

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
        lbl = QLabel("<b>İşlem Tamamlandı.</b>")
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

        tabs.addTab(tab_general, tr("Genel"))

        # ── SEKME 2: Dinleme ──────────────────────────────
        tab_listen = QWidget()
        ll = QFormLayout(tab_listen)
        ll.setSpacing(12); ll.setContentsMargins(16, 16, 16, 16)

        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(2, 30); self.timeout_spin.setSuffix(" sn")
        self.timeout_spin.setValue(self.settings.get("overlay_timeout_seconds", 5))
        ll.addRow("Dinleme zaman aşımı:", self.timeout_spin)

        self.display_spin = QSpinBox()
        self.display_spin.setRange(1, 10); self.display_spin.setSuffix(" sn")
        self.display_spin.setValue(self.settings.get("overlay_display_seconds", 3))
        ll.addRow("Sonuç gösterim süresi:", self.display_spin)

        self.sensitivity_spin = QSpinBox()
        self.sensitivity_spin.setRange(200, 10000)
        self.sensitivity_spin.setSingleStep(100)
        self.sensitivity_spin.setValue(self.settings.get("mic_sensitivity", 3000))
        self.sensitivity_spin.setToolTip("Düşük = daha hassas. Önerilen: 1000–4000")
        ll.addRow("Mikrofon hassasiyeti:", self.sensitivity_spin)

        self.pause_spin = QSpinBox()
        self.pause_spin.setRange(5, 50); self.pause_spin.setSuffix(" × 0.1 sn")
        self.pause_spin.setValue(int(self.settings.get("pause_threshold", 1.5) * 10))
        self.pause_spin.setToolTip(
            "Kaç saniyelik sessizlik 'konuşma bitti' sayılsın?\n"
            "Artırın → cümle ortasında kesilmez\n"
            "Azaltın → hızlı tepki verir (Varsayılan: 1.5 sn = 15)"
        )
        ll.addRow("Sessizlik eşiği (cümle sonu):", self.pause_spin)

        self.phrase_limit_spin = QSpinBox()
        self.phrase_limit_spin.setRange(5, 120); self.phrase_limit_spin.setSuffix(" sn")
        self.phrase_limit_spin.setValue(self.settings.get("phrase_time_limit", 30))
        self.phrase_limit_spin.setToolTip("Tek bir konuşmada maksimum süre.")
        ll.addRow("Maks. konuşma süresi:", self.phrase_limit_spin)

        tabs.addTab(tab_listen, tr("Dinleme"))

        # ── SEKME 3: Yapay Zeka ───────────────────────────
        tab_llm = QWidget()
        al = QVBoxLayout(tab_llm)
        al.setContentsMargins(16, 16, 16, 16); al.setSpacing(10)

        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("Mod:"))
        self.llm_mode_combo = QComboBox()
        self.llm_mode_combo.addItem("Yerel Sunucu (Local)", "local")
        self.llm_mode_combo.addItem("Uzak Sunucu (Remote)", "remote")
        self.llm_mode_combo.addItem("Terminal (CLI)", "cli")
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
        self.cli_model_combo.setPlaceholderText("Model seçin veya yazın")
        cp.addRow(tr("Model:"), self.cli_model_combo)

        self.cli_extra_input = QLineEdit(self.settings.get("llm_cli_extra_args", ""))
        self.cli_extra_input.setPlaceholderText("Opsiyonel ek argümanlar (örn: --temperature 0.7)")
        cp.addRow(tr("Ek argümanlar:"), self.cli_extra_input)

        note = QLabel("Sesiniz metne çevrildikten sonra stdin üzerinden\n"
                       "seçilen araca gönderilir. Araç yanıtını stdout'a yazdırmalıdır.")
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
        self.sys_prompt_input.setPlaceholderText("Sen yetenekli bir asistan...")
        pl.addWidget(self.sys_prompt_input)

        tabs.addTab(tab_prompt, tr("Sistem Promptu"))

        # ── SEKME 5: Güvenlik & Ekstra Ayarlar ─────────────────────────────
        tab_sec = QWidget()
        sl = QFormLayout(tab_sec)
        sl.setSpacing(12); sl.setContentsMargins(16, 16, 16, 16)

        self.popup_check = QCheckBox(tr("Yapay Zeka yanıtlarını bana sormadan (otomatik) ekranda göstersin"))
        self.popup_check.setChecked(self.settings.get("auto_show_popup", False))
        sl.addRow(self.popup_check)
        
        info = QLabel("Eğer işaretlenirse, ekrana 'Cevabı görmek istiyor musun?' sorusu çıkmaz, direkt Pop-up açılır.")
        info.setStyleSheet("color: #aaa; font-size: 11px;")
        sl.addRow(info)
        
        self.opt_check = QCheckBox(tr("Akıllı Dikte Düzeltici (Yapay Zeka ile İyileştirme)"))
        self.opt_check.setChecked(self.settings.get("optimize_dictation", False))
        sl.addRow(self.opt_check)
        
        opt_info = QLabel("Diktedeki duraksama veya hataları (ııı, eee, şey vs.) algılayarak asıl yapay zekaya en temiz komutu/soruyu iletmek üzere arka planda ön-işlemden geçirir.\nUYARI: Bu işlem yanıt süresini uzatır ve API kotanızı (token kullanımını) 2 katına çıkarır.")
        opt_info.setStyleSheet("color: #eb7a34; font-size: 11px;")
        opt_info.setWordWrap(True)
        sl.addRow(opt_info)

        self.clip_check = QCheckBox(tr("Bağlam Farkındalığı (Pano ve Ekran Okumaya Her Zaman İzin Ver)"))
        self.clip_check.setChecked(self.settings.get("auto_allow_clipboard", False))
        sl.addRow(self.clip_check)

        clip_info = QLabel("İşaretlendiğinde; cümlenizde 'bunu', 'şunu', 'pano' veya 'ekran' kelimeleri geçtiğinde panonuzdaki metin veya ekran görüntünüz (OCR) doğrudan yapay zekaya iletilir, onay sormaz.")
        clip_info.setStyleSheet("color: #aaa; font-size: 11px;")
        clip_info.setWordWrap(True)
        sl.addRow(clip_info)

        tabs.addTab(tab_sec, tr("Güvenlik"))

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
            self.cli_model_combo.setPlaceholderText("Bu araç model seçimi desteklemiyor")
            self.cli_model_combo.setEnabled(False)

    def _browse_workspace(self):
        """Dizin seçici açıp workspace path'i doldurur."""
        current = self.workspace_input.text().strip() or ""
        directory = QFileDialog.getExistingDirectory(
            self, "Çalışma Alanı Seç", current or "/home"
        )
        if directory:
            self.workspace_input.setText(directory)

    def _save(self):
        s = self.settings
        
        current_hk = self.hotkey_input.text().strip()
        if "Tuşlara basın" not in current_hk:
            s.set("hotkey", current_hk)
            
        s.set("overlay_position",        self.pos_combo.currentData())
        s.set("language",                self.lang_combo.currentData())
        s.set("app_language",            self.app_lang_combo.currentData())
        s.set("workspace_dir",           self.workspace_input.text().strip())
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

        self.waveform     = WaveformWidget(self.settings)
        self.overlay      = OverlayWindow(self.settings, self.waveform)
        self.overlay.stop_clicked.connect(self.on_hotkey_triggered)

        self.hotkey = HotkeyManager(self.settings.get("hotkey"), self.on_hotkey_triggered)
        self.settings_win = SettingsWindow(self.settings, self.hotkey)
        self.hotkey.start()

        self._setup_tray()
        self._is_listening = False

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
        self.tray.setToolTip("Linux-AI-Assistant\nSağ tıkla → Menü\nSol tıkla → Ayarlar")
        
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
        act_q = QAction(tr("Çıkış"), self.app)
        act_q.triggered.connect(self._quit)
        menu.addAction(act_q)
        self.tray.setContextMenu(menu)
        self.tray.show()

    def _check_single_instance(self):
        self._lock_file = "/tmp/ai_dikte.lock"
        self._lock_fd = os.open(self._lock_file, os.O_RDWR | os.O_CREAT, 0o666)
        try:
            fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError):
            msg = QMessageBox()
            msg.setIcon(QMessageBox.Icon.Warning)
            msg.setWindowTitle("Uygulama Zaten Çalışıyor")
            msg.setText("Linux-AI-Assistant şu anda arka planda zaten açık!")
            msg.setInformativeText("Lütfen sağ alt köşedeki (Sistem Çekmecesi) ikona sağ tıklayıp işlem yapın. Kapatmak için Çıkış'a basabilirsiniz.")
            msg.exec()
            sys.exit(0)

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
        
        # Sadece overlayi göster, KDE tepsisinden bildirim atıp kalp atışını tetikleme
        self.comm.show_overlay.emit()
        self.comm.start_waveform.emit()
        # Panoyu ana thread üzerinde okuyoruz (QClipboard thread-safe değildir, çökme yapabilir)
        import PyQt6.QtGui as QtGui
        current_clip = QtGui.QGuiApplication.clipboard().text().strip()

        threading.Thread(target=self._process, args=(current_clip,), daemon=True).start()

    def _process(self, current_clipboard_text):
        try:
            self.comm.update_text.emit(tr("Dinleniyor..."))
            text = self.audio.listen_and_transcribe()
            self.comm.stop_waveform.emit()

            if not self._is_listening:
                return

            if not text:
                self.comm.update_text.emit("Ses algılanamadı.")
                time.sleep(1.8)
                self.comm.hide_overlay.emit()
                self._is_listening = False
                return

            # Sesi aldık, önce bunu göster
            self.comm.update_text.emit("Ses Kaydı Alındı.")
            time.sleep(0.6) # Yarım saniye kadar göster
            
            if not self._is_listening:
                return
                
            if self.settings.get("optimize_dictation", False):
                self.comm.update_text.emit("Akıllı Dikte Düzeltici Çalışıyor...")
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
            context = None
            lower_text = text.lower()
            context_keywords = ["pano", "kopyala", "bunu", "şunu", "bu ", "buradaki", "ekran"]
            
            if any(kw in lower_text for kw in context_keywords):
                import os
                import subprocess
                
                clip_text = current_clipboard_text
                
                # Ekran görüntüsü ve OCR (eğer cümlede 'ekran' kelimesi geçiyorsa)
                screen_text = ""
                if "ekran" in lower_text:
                    self.comm.update_text.emit("Ekran Okunuyor (OCR)...")
                    try:
                        ss_path = "/tmp/ai_dikte_screen.png"
                        txt_path = "/tmp/ai_dikte_screen"
                        
                        # Ekran resmi al (Wayland/X11 desteği için sırayla dene)
                        if os.system(f"grim {ss_path} >/dev/null 2>&1") != 0:
                            if os.system(f"spectacle -b -n -o {ss_path} >/dev/null 2>&1") != 0:
                                os.system(f"gnome-screenshot -f {ss_path} >/dev/null 2>&1")
                        
                        if os.path.exists(ss_path):
                            # Tesseract OCR
                            if os.system(f"tesseract {ss_path} {txt_path} -l tur+eng >/dev/null 2>&1") == 0:
                                if os.path.exists(txt_path + ".txt"):
                                    with open(txt_path + ".txt", "r", encoding="utf-8") as f:
                                        screen_text = f.read().strip()
                    except Exception as e:
                        logger.error(f"OCR Hatası: {e}")

                combined_context = ""
                if clip_text:
                    combined_context += f"[Kullanıcının Panosundaki Metin]:\n{clip_text}\n\n"
                if screen_text:
                    combined_context += f"[Kullanıcının Ekranındaki Metin (OCR)]:\n{screen_text}\n\n"
                
                if combined_context.strip():
                    if self.settings.get("auto_allow_clipboard", False):
                        context = combined_context.strip()
                    else:
                        result = [False]
                        ev = threading.Event()
                        self.comm.ask_clipboard.emit(result, ev, combined_context.strip())
                        ev.wait()
                        if result[0]:
                            context = combined_context.strip()

            self.comm.update_text.emit(tr("Yapay Zekanın Cevabı Bekleniyor..."))

            # --- LLM / YÖNLENDİRME ---
            response = self.router.parse_and_route(text, context=context)
            
            if not self._is_listening:
                return
            
            wants_popup = False
            if "[EKRANDA_GOSTER]" in response or "[EKRANDA_GÖSTER]" in response:
                wants_popup = True
                response = response.replace("[EKRANDA_GOSTER]", "").replace("[EKRANDA_GÖSTER]", "").strip()

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

    def _confirm_callback(self, cmd_str, explanation):
        result = [False]
        ev = threading.Event()
        self.comm.ask_confirm.emit(cmd_str, explanation, result, ev)
        ev.wait()
        return result[0]

    def _play_notification_sound(self):
        import subprocess
        try:
            subprocess.Popen(["paplay", "/usr/share/sounds/freedesktop/stereo/message.oga"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass

    def _ask_confirm_gui(self, cmd_str, explanation, result_list, event):
        msg = QMessageBox()
        msg.setIcon(QMessageBox.Icon.Warning)
        msg.setWindowTitle("Güvenlik Onayı")
        msg.setText(f"Şu komut çalıştırılmak isteniyor:\n\n<b>{cmd_str}</b>")
        msg.setInformativeText(f"<b>Yapay Zeka Açıklaması:</b>\n{explanation}\n\nBu komutun çalıştırılmasına izin veriyor musunuz?")
        btn_yes = msg.addButton("Evet", QMessageBox.ButtonRole.YesRole)
        btn_no = msg.addButton("Hayır", QMessageBox.ButtonRole.NoRole)
        msg.setDefaultButton(btn_no)
        
        # Wayland için Tool veya Frameless flag ekleyebiliriz
        msg.setWindowFlags(msg.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)

        self._play_notification_sound()
        msg.exec()
        result_list[0] = (msg.clickedButton() == btn_yes)
        event.set()

    def _ask_show_response_gui(self, result_list, event):
        msg = QMessageBox()
        msg.setIcon(QMessageBox.Icon.Question)
        msg.setWindowTitle("Yanıtı Göster")
        msg.setText("Yapay zeka işlemini tamamladı ve size bir yanıt vermek istiyor.")
        msg.setInformativeText("Cevabı ekranda görmek istiyor musunuz?")
        btn_yes = msg.addButton("Evet", QMessageBox.ButtonRole.YesRole)
        btn_no = msg.addButton("Hayır", QMessageBox.ButtonRole.NoRole)
        msg.setDefaultButton(btn_yes)
        msg.setWindowFlags(msg.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)

        self._play_notification_sound()
        msg.exec()
        result_list[0] = (msg.clickedButton() == btn_yes)
        event.set()

    def _ask_clipboard_gui(self, result_list, event, clipboard_text):
        msg = QMessageBox()
        msg.setIcon(QMessageBox.Icon.Question)
        msg.setWindowTitle("Bağlam Farkındalığı (Pano ve Ekran Erişimi)")
        msg.setText("Cümlenizde bağlam gerektiren kelimeler tespit edildi. Yapay zekaya panonuzdaki/ekranınızdaki metin de gönderilsin mi?")
        
        btn_yes = msg.addButton("Evet", QMessageBox.ButtonRole.YesRole)
        btn_no = msg.addButton("Hayır", QMessageBox.ButtonRole.NoRole)
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
    manager = AppManager()
    manager.run()
