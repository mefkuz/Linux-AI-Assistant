#!/usr/bin/env python3
"""Linux AI Assistant — grafik arayüz giriş noktası.

Kullanım:
    ./venv/bin/python app.py            # normal başlatma (sistem çekmecesi)
    ./venv/bin/python app.py --trigger  # Wayland kısayol tetikleyicisi
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

_HERE = os.path.dirname(os.path.abspath(__file__))
_VENV_PY = os.path.join(_HERE, "venv", "bin", "python")

# venv dururken sistem python'uyla çalıştırıldıysa sessizce venv'e geç.
# (Kullanıcı `python3 app.py` dese bile doğru yorumlayıcı kullanılır.)
if os.path.isfile(_VENV_PY) and os.path.realpath(sys.executable) != os.path.realpath(_VENV_PY):
    os.execv(_VENV_PY, [_VENV_PY, os.path.abspath(__file__)] + sys.argv[1:])

try:
    from src.gui.app import AppManager
except ModuleNotFoundError as e:
    missing = getattr(e, "name", "") or str(e)
    sys.stderr.write(
        f"\nHATA / ERROR: Gerekli Python paketi eksik / missing package: '{missing}'\n"
        "Çözüm / Fix: ./scripts/install.sh  (veya: ./venv/bin/pip install -r requirements.txt)\n\n"
    )
    sys.exit(2)

if __name__ == "__main__":
    manager = AppManager()
    manager.run()
