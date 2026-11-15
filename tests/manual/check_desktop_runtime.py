"""Opt-in real KDE/Wayland desktop smoke test; no user text is printed.

Opens a temporary test window, types only when that window is active, performs
OCR, reads a short microphone buffer, and checks the actual app startup. Saves
and restores all clipboard MIME formats. Run with the project's venv Python.
"""
import json
import os
from pathlib import Path
import signal
import sys
import subprocess
import tempfile
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
# Use the compositor's native focus reporting for a guarded input test;
# XWayland's last active X11 window can remain marked active while a native
# Wayland application actually owns keyboard focus.
if os.environ.get("XDG_SESSION_TYPE") == "wayland":
    os.environ.setdefault("QT_QPA_PLATFORM", "wayland")
from PyQt6.QtCore import Qt, QTimer, QMimeData, QObject, pyqtSlot
from PyQt6.QtDBus import QDBusConnection
from PyQt6.QtWidgets import QLineEdit, QPushButton
from src.gui.app import AppManager, ResponseWindow
from src.tools import os_input
from src.tools.executor import read_screen_via_ocr, read_clipboard_subprocess


def main():
    # An unrelated release dialog must not steal focus during input checks.
    # The normal app still performs its configured startup update check.
    manager = AppManager(check_updates=False)
    app = manager.app
    results = {"gui_startup": True, "tray": manager.tray.isVisible(),
               "hotkey_listener": bool(manager.hotkey.listener and manager.hotkey.listener.running),
               "browser_bridge": bool(getattr(manager, "ext_server", None))}
    clipboard = app.clipboard()
    original = clipboard.mimeData()
    saved = {fmt: bytes(original.data(fmt)) for fmt in original.formats()} if original else {}
    window = ResponseWindow("LINUX ASSISTANT TEST\n\nSCREEN VERIFICATION 73921")
    title = "Linux AI Assistant — çalışma testi " + str(os.getpid())
    window.setWindowTitle(title)
    layout = window.layout()
    class TestEdit(QLineEdit):
        def keyPressEvent(self, event):
            before = self.text()
            start = self.selectionStart()
            end = start + len(self.selectedText())
            if start < 0:
                start = end = self.cursorPosition()
            is_paste = (event.key() == Qt.Key.Key_V
                        and bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier))
            super().keyPressEvent(event)
            if is_paste:
                results['paste_inserted_correctly'] = self.text() == before[:start] + marker + before[end:]
    edit = TestEdit()
    edit.setPlaceholderText("Otomatik yazma testi")
    layout.insertWidget(2, edit)
    for button in window.findChildren(QPushButton):
        button.setAutoDefault(False)
        button.setDefault(False)
    window.show()
    window.raise_()
    window.activateWindow()
    edit.setFocus()
    failures = []
    marker = "Codex Türkçe doğrulama 73921"
    completion = threading.Event()
    class FocusProbe(QObject):
        @pyqtSlot(str)
        def report(self, value):
            results['compositor_focus_verified'] = value == 'true'
    probe = FocusProbe()
    bus = QDBusConnection.sessionBus()
    service = 'org.linuxai.DesktopTest.p' + str(os.getpid())
    bus.registerService(service)
    bus.registerObject('/Probe', probe, QDBusConnection.RegisterOption.ExportAllSlots)

    def worker():
        try:
            import speech_recognition as sr
            from src.audio.listener import suppress_c_stderr
            with suppress_c_stderr():
                with sr.Microphone() as mic:
                    sample = mic.stream.read(512)
            results["microphone_capture"] = len(sample) > 0
            results["clipboard_roundtrip"] = read_clipboard_subprocess().strip() == marker
            time.sleep(0.6)
            ocr = read_screen_via_ocr()
            results["screen_ocr"] = "73921" in ocr and "VERIFICATION" in ocr.upper()
        except Exception as exc:
            failures.append(type(exc).__name__ + ": " + str(exc))
        finally:
            completion.set()

    def start():
        if not window.isActiveWindow() or not edit.hasFocus():
            failures.append("Test window is not active; refused to send keyboard input.")
            finish()
            return
        edit.clear()
        # Test the actual signal hook without activating speech capture.
        callback = manager.on_hotkey_triggered
        observed = []
        manager.on_hotkey_triggered = lambda: observed.append(True)
        os.kill(os.getpid(), signal.SIGUSR1)
        manager._check_sigusr1()
        manager.on_hotkey_triggered = callback
        results["signal_trigger"] = bool(observed)
        # Inject immediately after the main-thread focus check. Opening the
        # microphone first introduced an unnecessary focus-change interval.
        ok, backend = os_input.type_text(marker)
        results["input_backend"] = backend
        results["input_command"] = ok
        threading.Thread(target=worker, daemon=True).start()
        poll.start(100)

    def finish():
        poll.stop()
        results["typed_text_matches"] = edit.text() == marker
        results["typed_text_length"] = len(edit.text())
        results["qt_clipboard_matches"] = clipboard.text() == marker
        mime = QMimeData()
        for fmt, data in saved.items():
            mime.setData(fmt, data)
        clipboard.setMimeData(mime)
        window.close()
        from src.context.server import has_active_clients
        results["extension_connected_optional"] = has_active_clients()
        results["errors"] = failures
        print(json.dumps(results, ensure_ascii=False), flush=True)
        manager._quit()

    def check_done():
        if completion.is_set():
            finish()

    def focus_test_window():
        # KWin may refuse requestActivate() for a tool-launched application.
        # A short-lived script targets only our uniquely titled test window;
        # it does not change focus policy or any saved desktop setting.
        name = "linux-ai-smoke-" + str(os.getpid())
        try:
            with tempfile.TemporaryDirectory(prefix="linux-ai-focus-") as temp:
                script = Path(temp, "focus.js")
                script.write_text("let ok = false; for (const w of workspace.windowList()) { if (w.caption.startsWith("
                                  + json.dumps(title) + ")) { workspace.activeWindow = w; ok = workspace.activeWindow === w; } } "
                                  + "callDBus(" + json.dumps(service) + ", '/Probe', '', 'report', String(ok));")
                loaded = subprocess.run(["qdbus6", "org.kde.KWin", "/Scripting",
                                         "org.kde.kwin.Scripting.loadScript", str(script), name],
                                        capture_output=True, text=True, timeout=3)
                script_id = int(loaded.stdout.strip())
                run = subprocess.run(["qdbus6", "org.kde.KWin", f"/Scripting/Script{script_id}", "run"],
                                     capture_output=True, text=True, timeout=3)
                results["compositor_focus_requested"] = run.returncode == 0
        except Exception as exc:
            results["compositor_focus_requested"] = False
        finally:
            subprocess.run(["qdbus6", "org.kde.KWin", "/Scripting",
                            "org.kde.kwin.Scripting.unloadScript", name],
                           capture_output=True, timeout=3)
        edit.setFocus()
        QTimer.singleShot(500, start)

    poll = QTimer()
    poll.timeout.connect(check_done)
    QTimer.singleShot(1600, focus_test_window)
    QTimer.singleShot(90000, lambda: (failures.append("Desktop test timed out"), finish()))
    app.exec()
    required = ["gui_startup", "tray", "hotkey_listener", "browser_bridge", "microphone_capture",
                "input_command", "clipboard_roundtrip", "screen_ocr", "paste_inserted_correctly", "signal_trigger"]
    if failures or not all(results.get(k) for k in required):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
