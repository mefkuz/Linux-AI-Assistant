from pynput import keyboard
import logging

logger = logging.getLogger(__name__)

class HotkeyManager:
    def __init__(self, hotkey_str, trigger_callback):
        self.hotkey_str = hotkey_str
        self.trigger_callback = trigger_callback
        self.listener = None

    def start(self):
        try:
            # pynput için global kısayol ataması
            self.listener = keyboard.GlobalHotKeys({
                self.hotkey_str: self.on_activate
            })
            self.listener.start()
            logger.info(f"Kısayol dinleyicisi başlatıldı: {self.hotkey_str}")
        except Exception as e:
            logger.error(f"Kısayol dinleyici başlatılamadı: {e}")

    def on_activate(self):
        logger.info("Kısayol tetiklendi!")
        if self.trigger_callback:
            self.trigger_callback()

    def stop(self):
        if self.listener:
            self.listener.stop()

    def update_hotkey(self, new_hotkey_str):
        self.stop()
        import time
        time.sleep(0.2)
        self.hotkey_str = new_hotkey_str
        self.start()
