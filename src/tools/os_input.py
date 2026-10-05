"""
Linux işletim sistemi seviyesinde giriş ve arayüz otomasyonu (Wayland + X11 uyumlu).

Eklenti olmadan tarayıcı ve masaüstü arayüzleriyle etkileşim kurmak için:
- Tuş vuruşları (Ctrl+W, Ctrl+V, PageDown, Enter vb.)
- Fare tıklaması (koordinata veya ekrandaki metne göre)
- Pano üzerinden metin yazma (Unicode / Türkçe korumalı)
- URL / Yeni sekme açma (xdg-open / webbrowser)
- Birincil seçim (primary selection) okuma
"""

import os
import shutil
import socket
import subprocess
import threading
import time
import logging
import webbrowser

logger = logging.getLogger(__name__)

_ydotoold_lock = threading.Lock()
_ydotoold_proc = None

# Linux giriş olay kodları (KEY_* scancodes from linux/input-event-codes.h)
_KEY_CODES = {
    "esc": 1,
    "escape": 1,
    "1": 2, "2": 3, "3": 4, "4": 5, "5": 6, "6": 7, "7": 8, "8": 9, "9": 10, "0": 11,
    "backspace": 14,
    "tab": 15,
    "q": 16, "w": 17, "e": 18, "r": 19, "t": 20, "y": 21, "u": 22, "i": 23, "o": 24, "p": 25,
    "enter": 28,
    "return": 28,
    "ctrl": 29,
    "leftctrl": 29,
    "a": 30, "s": 31, "d": 32, "f": 33, "g": 34, "h": 35, "j": 36, "k": 37, "l": 38,
    "shift": 42,
    "leftshift": 42,
    "z": 44, "x": 45, "c": 46, "v": 47, "b": 48, "n": 49, "m": 50,
    "alt": 56,
    "leftalt": 56,
    "space": 57,
    "rightctrl": 97,
    "home": 102,
    "up": 103,
    "pageup": 104,
    "left": 105,
    "right": 106,
    "end": 107,
    "down": 108,
    "pagedown": 109,
    "delete": 111,
    "f5": 63,
    "f11": 87,
}


def is_wayland():
    """Mevcut oturumun Wayland olup olmadığını denetler."""
    return (
        os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"
        or bool(os.environ.get("WAYLAND_DISPLAY"))
    )


def get_ydotool_socket_path():
    """ydotool soket dosyasının yolunu döndürür."""
    custom = os.environ.get("YDOTOOL_SOCKET")
    if custom:
        return custom
    uid = os.getuid() if hasattr(os, "getuid") else 1000
    return f"/run/user/{uid}/.ydotool_socket"


def is_ydotool_live(socket_path=None):
    """ydotoold arka plan servisinin çalışır durumda olup olmadığını denetler."""
    path = socket_path or get_ydotool_socket_path()
    if not os.path.exists(path):
        return False
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as s:
            s.settimeout(0.5)
            s.connect(path)
        return True
    except Exception:
        return False


def ensure_ydotoold():
    """
    ydotoold servisinin çalıştığından emin olur.
    Çalışmıyorsa kullanıcı izinleriyle arka planda başlatır.
    """
    global _ydotoold_proc
    sock = get_ydotool_socket_path()

    if is_ydotool_live(sock):
        return True

    if not shutil.which("ydotoold"):
        return False

    with _ydotoold_lock:
        if is_ydotool_live(sock):
            return True

        # Eski/bozuk soket dosyasını temizle
        if os.path.exists(sock) and not is_ydotool_live(sock):
            try:
                os.unlink(sock)
            except Exception:
                pass

        try:
            cmd = ["ydotoold", f"--socket-path={sock}"]
            _ydotoold_proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            # Soketin oluşup dinlemeye başlamasını bekle (en fazla 1.5 saniye)
            for _ in range(15):
                time.sleep(0.1)
                if is_ydotool_live(sock):
                    logger.info("ydotoold otomatik olarak arka planda başlatıldı.")
                    return True
        except Exception as e:
            logger.debug(f"ydotoold başlatma hatası: {e}")

    return is_ydotool_live(sock)


def open_url(url):
    """
    Belirtilen URL'yi varsayılan tarayıcıda yeni sekme/pencere olarak açar.
    Tüm Linux dağıtımlarında ve masaüstü ortamlarında bağımsız çalışır.
    """
    url = (url or "").strip()
    if not url:
        return False, "URL boş."
    if not (url.startswith("http://") or url.startswith("https://") or url.startswith("file://")):
        url = "https://" + url

    try:
        # Python yerel webbrowser (xdg-open'ı arka planda otomatik kullanır)
        opened = webbrowser.open(url)
        if opened:
            return True, f"URL tarayıcıda açıldı: {url}"
    except Exception:
        pass

    if shutil.which("xdg-open"):
        try:
            r = subprocess.run(["xdg-open", url], capture_output=True, timeout=5)
            if r.returncode == 0:
                return True, f"URL xdg-open ile açıldı: {url}"
        except Exception as e:
            return False, f"URL açılamadı: {e}"

    return False, "URL açmak için sistemde tarayıcı bulunamadı."


def send_key_combination(combo_name):
    """
    Belirtilen kısayol veya tuş kombinasyonunu işletim sistemi seviyesinde simüle eder.
    Desteklenenler: ctrl+w, ctrl+t, ctrl+v, ctrl+c, pagedown, pageup, enter, tab, esc, space, down, up vb.
    """
    combo_clean = (combo_name or "").strip().lower()
    if not combo_clean:
        return False, "Tuş kombinasyonu boş."

    # 1. Yöntem: ydotool (Wayland + X11 evrensel)
    if ensure_ydotoold() and shutil.which("ydotool"):
        parts = [p.strip() for p in combo_clean.replace("-", "+").split("+")]
        codes = [_KEY_CODES.get(p) for p in parts if p in _KEY_CODES]
        if len(codes) == len(parts):
            # Örn: 29:1 17:1 17:0 29:0
            press = " ".join(f"{c}:1" for c in codes)
            release = " ".join(f"{c}:0" for c in reversed(codes))
            args = (press + " " + release).split()
            try:
                r = subprocess.run(["ydotool", "key"] + args, capture_output=True, text=True, timeout=4)
                if r.returncode == 0:
                    return True, f"ydotool ile tuş gönderildi: {combo_clean}"
            except Exception as e:
                logger.debug(f"ydotool tuş hatası: {e}")

    # 2. Yöntem: xdotool (X11 / XWayland)
    if shutil.which("xdotool"):
        xdo_map = {
            "pagedown": "Page_Down",
            "pageup": "Page_Up",
            "enter": "Return",
            "return": "Return",
            "tab": "Tab",
            "esc": "Escape",
            "escape": "Escape",
            "space": "space",
            "up": "Up",
            "down": "Down",
            "left": "Left",
            "right": "Right",
        }
        xdo_key = xdo_map.get(combo_clean, combo_clean)
        try:
            r = subprocess.run(["xdotool", "key", xdo_key], capture_output=True, text=True, timeout=4)
            if r.returncode == 0:
                return True, f"xdotool ile tuş gönderildi: {combo_clean}"
        except Exception as e:
            logger.debug(f"xdotool tuş hatası: {e}")

    # 3. Yöntem: pynput fallback
    try:
        from pynput.keyboard import Controller, Key
        kb = Controller()
        if combo_clean == "ctrl+w":
            with kb.pressed(Key.ctrl):
                kb.tap("w")
            return True, f"pynput ile tuş gönderildi: {combo_clean}"
        elif combo_clean == "ctrl+v":
            with kb.pressed(Key.ctrl):
                kb.tap("v")
            return True, f"pynput ile tuş gönderildi: {combo_clean}"
        elif combo_clean in ("enter", "return"):
            kb.tap(Key.enter)
            return True, f"pynput ile tuş gönderildi: {combo_clean}"
        elif combo_clean == "tab":
            kb.tap(Key.tab)
            return True, f"pynput ile tuş gönderildi: {combo_clean}"
        elif combo_clean in ("esc", "escape"):
            kb.tap(Key.esc)
            return True, f"pynput ile tuş gönderildi: {combo_clean}"
        elif combo_clean == "pagedown":
            kb.tap(Key.page_down)
            return True, f"pynput ile tuş gönderildi: {combo_clean}"
        elif combo_clean == "pageup":
            kb.tap(Key.page_up)
            return True, f"pynput ile tuş gönderildi: {combo_clean}"
        elif combo_clean == "down":
            kb.tap(Key.down)
            return True, f"pynput ile tuş gönderildi: {combo_clean}"
        elif combo_clean == "up":
            kb.tap(Key.up)
            return True, f"pynput ile tuş gönderildi: {combo_clean}"
        elif combo_clean == "space":
            kb.tap(Key.space)
            return True, f"pynput ile tuş gönderildi: {combo_clean}"
    except Exception as e:
        logger.debug(f"pynput tuş hatası: {e}")

    return False, f"Tuş simülasyonu yapılamadı (ydotool/xdotool/pynput kullanılamadı): {combo_clean}"


def set_clipboard_text(text):
    """Metni sistem panosuna kopyalar (Wayland wl-copy, X11 xclip)."""
    text_bytes = (text or "").encode("utf-8")

    # Wayland: wl-copy
    if shutil.which("wl-copy"):
        try:
            r = subprocess.run(["wl-copy"], input=text_bytes, capture_output=True, timeout=4)
            if r.returncode == 0:
                return True
        except Exception:
            pass

    # X11: xclip
    if shutil.which("xclip"):
        try:
            r = subprocess.run(["xclip", "-selection", "clipboard"], input=text_bytes, capture_output=True, timeout=4)
            if r.returncode == 0:
                return True
        except Exception:
            pass

    # PyQt6 fallback (eğer Qt uygulaması açıksa)
    try:
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance()
        if app:
            cb = app.clipboard()
            if cb:
                cb.setText(text)
                return True
    except Exception:
        pass

    return False


def type_text(text):
    """
    Aktif alana metin yazar.
    En kararlı yöntem olarak panoya kopyalar ve ardından Ctrl+V (yapıştır) gönderir.
    Bu sayede Türkçe karakterler, emojiler ve çok satırlı metinler bozulmadan anında yazılır.
    """
    if not text:
        return False, "Yazılacak metin boş."

    if set_clipboard_text(text):
        time.sleep(0.05)
        ok, msg = send_key_combination("ctrl+v")
        if ok:
            return True, "Metin panoya alınıp yapıştırıldı."

    # Alternatif: ydotool type
    if ensure_ydotoold() and shutil.which("ydotool"):
        try:
            r = subprocess.run(["ydotool", "type", text], capture_output=True, text=True, timeout=5)
            if r.returncode == 0:
                return True, "Metin ydotool ile doğrudan yazıldı."
        except Exception as e:
            logger.debug(f"ydotool type hatası: {e}")

    return False, "Metin yazılamadı (pano veya tuş simülasyonu başarısız)."


def click(x=None, y=None, button="left", double=False):
    """
    Belirtilen koordinata (x, y) veya mevcut imleç konumuna fare tıklaması yapar.
    button: 'left', 'right', 'middle'
    double: True ise çift tıklar.
    """
    btn_lower = (button or "left").lower()

    # 1. Yöntem: ydotool
    if ensure_ydotoold() and shutil.which("ydotool"):
        btn_hex = "0xC0" if btn_lower == "left" else ("0xC1" if btn_lower == "right" else "0xC2")
        try:
            if x is not None and y is not None:
                subprocess.run(["ydotool", "mousemove", "-a", str(int(x)), str(int(y))],
                               capture_output=True, timeout=3)
                time.sleep(0.05)

            r = subprocess.run(["ydotool", "click", btn_hex], capture_output=True, timeout=3)
            if double:
                time.sleep(0.1)
                r = subprocess.run(["ydotool", "click", btn_hex], capture_output=True, timeout=3)

            if r.returncode == 0:
                target = f"({x}, {y})" if (x is not None and y is not None) else "mevcut konuma"
                return True, f"ydotool ile tıklandı: {target} ({btn_lower})"
        except Exception as e:
            logger.debug(f"ydotool click hatası: {e}")

    # 2. Yöntem: xdotool
    if shutil.which("xdotool"):
        btn_num = "1" if btn_lower == "left" else ("3" if btn_lower == "right" else "2")
        try:
            cmd = ["xdotool"]
            if x is not None and y is not None:
                cmd.extend(["mousemove", str(int(x)), str(int(y))])
            if double:
                cmd.extend(["click", "--repeat", "2", "--delay", "100", btn_num])
            else:
                cmd.extend(["click", btn_num])
            r = subprocess.run(cmd, capture_output=True, timeout=4)
            if r.returncode == 0:
                target = f"({x}, {y})" if (x is not None and y is not None) else "mevcut konuma"
                return True, f"xdotool ile tıklandı: {target} ({btn_lower})"
        except Exception as e:
            logger.debug(f"xdotool click hatası: {e}")

    # 3. Yöntem: pynput
    try:
        from pynput.mouse import Controller, Button
        m = Controller()
        if x is not None and y is not None:
            m.position = (int(x), int(y))
            time.sleep(0.05)
        pynput_btn = Button.left if btn_lower == "left" else (Button.right if btn_lower == "right" else Button.middle)
        m.click(pynput_btn, 2 if double else 1)
        target = f"({x}, {y})" if (x is not None and y is not None) else "mevcut konuma"
        return True, f"pynput ile tıklandı: {target} ({btn_lower})"
    except Exception as e:
        logger.debug(f"pynput click hatası: {e}")

    return False, "Fare tıklaması gerçekleştirilemedi (ydotool/xdotool/pynput çalışmadı)."


def find_text_on_screen(search_text):
    """
    Ekran görüntüsü alır ve Tesseract OCR ile belirtilen metnin/kelimenin
    ekrandaki merkez koordinatlarını (center_x, center_y) bulur.
    Bulamazsa None döndürür.
    """
    search_clean = (search_text or "").strip().lower()
    if not search_clean or not shutil.which("tesseract"):
        return None

    ss_path = "/tmp/ai_find_screen.png"
    tsv_base = "/tmp/ai_find_screen_tsv"
    tsv_path = tsv_base + ".tsv"

    try:
        # Ekran görüntüsü al
        shot_ok = False
        for shot_cmd in [
            ["grim", ss_path],
            ["spectacle", "-b", "-n", "-o", ss_path],
            ["gnome-screenshot", "-f", ss_path],
        ]:
            if shutil.which(shot_cmd[0]):
                try:
                    r = subprocess.run(shot_cmd, capture_output=True, timeout=5)
                    if r.returncode == 0 and os.path.exists(ss_path):
                        shot_ok = True
                        break
                except Exception:
                    pass

        if not shot_ok or not os.path.exists(ss_path):
            return None

        # Tesseract TSV çalıştır
        r = subprocess.run(["tesseract", ss_path, tsv_base, "-l", "tur+eng", "tsv"],
                           capture_output=True, timeout=20)
        if r.returncode != 0 or not os.path.exists(tsv_path):
            return None

        # TSV ayrıştır: level, page, block, par, line, word, left, top, width, height, conf, text
        with open(tsv_path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()

        if len(lines) <= 1:
            return None

        # Tek kelime veya çok kelimeli arama
        words_data = []
        for line in lines[1:]:
            parts = line.strip().split("\t")
            if len(parts) >= 12:
                try:
                    left, top, w, h = int(parts[6]), int(parts[7]), int(parts[8]), int(parts[9])
                    txt = parts[11].strip().lower()
                    if txt:
                        words_data.append((txt, left, top, w, h))
                except (ValueError, IndexError):
                    continue

        # 1. Birebir kelime eşleşmesi
        for txt, left, top, w, h in words_data:
            if search_clean == txt or (len(search_clean) >= 3 and search_clean in txt):
                return (left + w // 2, top + h // 2)

        # 2. Çok kelimeli öbek eşleşmesi
        search_words = search_clean.split()
        if len(search_words) > 1 and len(words_data) >= len(search_words):
            for i in range(len(words_data) - len(search_words) + 1):
                match = True
                for j, sw in enumerate(search_words):
                    if sw not in words_data[i + j][0]:
                        match = False
                        break
                if match:
                    # İlk ve son kelimenin kapsadığı alanın merkezi
                    first = words_data[i]
                    last = words_data[i + len(search_words) - 1]
                    min_x = first[1]
                    max_x = last[1] + last[3]
                    min_y = min(first[2], last[2])
                    max_y = max(first[2] + first[4], last[2] + last[4])
                    return ((min_x + max_x) // 2, (min_y + max_y) // 2)

    except Exception as e:
        logger.debug(f"find_text_on_screen hatası: {e}")
    finally:
        for p in (ss_path, tsv_path):
            try:
                if os.path.exists(p):
                    os.remove(p)
            except OSError:
                pass

    return None


def read_primary_selection():
    """
    Kullanıcının ekranda fare ile seçtiği metni (Ctrl+C yapmadan) okur.
    Wayland: wl-paste --primary
    X11: xclip -o -selection primary
    """
    # Wayland
    if shutil.which("wl-paste"):
        try:
            r = subprocess.run(["wl-paste", "--primary", "-n"], capture_output=True, text=True, timeout=3)
            if r.returncode == 0 and r.stdout:
                return r.stdout
        except Exception:
            pass

    # X11
    if shutil.which("xclip"):
        try:
            r = subprocess.run(["xclip", "-o", "-selection", "primary"], capture_output=True, text=True, timeout=3)
            if r.returncode == 0 and r.stdout:
                return r.stdout
        except Exception:
            pass

    return ""
