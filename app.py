#!/usr/bin/env python3
"""Linux AI Assistant — grafik arayüz giriş noktası.

Kullanım:
    ./venv/bin/python app.py            # normal başlatma (sistem çekmecesi)
    ./venv/bin/python app.py --trigger  # Wayland kısayol tetikleyicisi
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.gui.app import AppManager

if __name__ == "__main__":
    manager = AppManager()
    manager.run()
