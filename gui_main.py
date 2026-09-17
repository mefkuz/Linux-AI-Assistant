#!/usr/bin/env python3
"""Geriye dönük uyumluluk kısayolu.

v1.1.0'dan önce GUI giriş noktası bu dosyaydı. Eski .desktop kısayolları,
Wayland tetikleyicileri ve belgeler hâlâ bu yolu çağırabilir; bu dosya
trafiği yeni giriş noktası `app.py`'ye yönlendirir.

Deprecated: yeni kurulumlarda doğrudan `app.py` kullanın.
"""
import os
import runpy
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))

if __name__ == "__main__":
    # app.py'nin beklediği gibi proje kökünü yola ekle, sonra ona devret.
    sys.path.insert(0, _HERE)
    sys.argv[0] = os.path.join(_HERE, "app.py")
    runpy.run_path(os.path.join(_HERE, "app.py"), run_name="__main__")
