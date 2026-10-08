"""
os_input ve eklentisiz browser_action birim ve entegrasyon testleri.
Çalıştırma: python3 tests/test_os_input.py
"""

import os
import sys
import unittest.mock as mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.tools import os_input
from src.tools.executor import ToolExecutor, TOOL_SUMMARIZERS, get_openai_tools
from src.core.i18n import set_language, tr

PASS = []
FAIL = []

def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"[{'OK' if cond else 'HATA'}] {name}" + (f" — {detail}" if detail and not cond else ""))


class FakeSettings:
    def __init__(self, d):
        self.d = d
    def get(self, key, fallback=None):
        return self.d.get(key, fallback)


print("=== OS INPUT & EKLENTİSİZ TARAYICI TESTLERİ ===")

# 1. Ortam denetimi
check("is_wayland boolean döner", isinstance(os_input.is_wayland(), bool))
check("get_ydotool_socket_path geçerli", ".ydotool_socket" in os_input.get_ydotool_socket_path())

# 2. open_url
with mock.patch("webbrowser.open", return_value=True):
    ok, msg = os_input.open_url("https://ornek.com")
    check("open_url geçerli url başarılı", ok and "ornek.com" in msg, msg)

ok_empty, _ = os_input.open_url("")
check("open_url boş url başarısız", not ok_empty)

# 3. send_key_combination tuş eşleme ve çağrı
with mock.patch("subprocess.run") as mock_run:
    mock_run.return_value = mock.MagicMock(returncode=0)
    with mock.patch.object(os_input, "ensure_ydotoold", return_value=True), \
         mock.patch("shutil.which", return_value="/usr/bin/ydotool"):
        ok, msg = os_input.send_key_combination("ctrl+w")
        check("send_key_combination ctrl+w başarılı", ok, msg)
        # 29:1 17:1 17:0 29:0 çağrılmalı
        args = mock_run.call_args[0][0]
        check("ydotool tuş kodları doğru (ctrl+w)",
              "29:1" in args and "17:1" in args, str(args))

        ok_pg, _ = os_input.send_key_combination("pagedown")
        args_pg = mock_run.call_args[0][0]
        check("pagedown tuş kodu doğru (109)", "109:1" in args_pg, str(args_pg))

        ok_ent, _ = os_input.send_key_combination("enter")
        args_ent = mock_run.call_args[0][0]
        check("enter tuş kodu doğru (28)", "28:1" in args_ent, str(args_ent))

# 4. click çağrısı ve koordinat kontrolü
with mock.patch("subprocess.run") as mock_run:
    mock_run.return_value = mock.MagicMock(returncode=0)
    with mock.patch.object(os_input, "ensure_ydotoold", return_value=True), \
         mock.patch("shutil.which", return_value="/usr/bin/ydotool"):
        ok, msg = os_input.click(450, 200, button="left")
        check("click koordinat ile başarılı", ok and "(450, 200)" in msg, msg)
        # mousemove -a 450 200 çağrılmış olmalı
        calls = [c[0][0] for c in mock_run.call_args_list]
        check("mousemove ve click çağrıldı",
              any("mousemove" in c and "450" in c for c in calls) and
              any("click" in c and "0xC0" in c for c in calls), str(calls))

# 5. type_text panoya alma ve yapıştırma
with mock.patch.object(os_input, "set_clipboard_text", return_value=True), \
     mock.patch.object(os_input, "send_key_combination", return_value=(True, "ok")):
    ok, msg = os_input.type_text("Merhaba Dünya! 123")
    check("type_text panodan yapıştırma başarılı", ok, msg)

# 6. ToolExecutor eklentisiz çalışma (browser_sender=None)
s = FakeSettings({"require_confirm_on_tool": False})
ex = ToolExecutor(settings=s, browser_sender=None)

# 6a. new_tab
with mock.patch("webbrowser.open", return_value=True):
    res = ex.execute_tool("browser_action", {"action": "new_tab", "url": "https://duckduckgo.com"})
    check("eklentisiz new_tab çalışıyor", "açıldı" in res.lower() and "duckduckgo.com" in res, res)

# 6b. close_tab
with mock.patch.object(os_input, "send_key_combination", return_value=(True, "ok")):
    res = ex.execute_tool("browser_action", {"action": "close_tab"})
    check("eklentisiz close_tab Ctrl+W tetikliyor", "kapatıldı" in res.lower(), res)

# 6c. scroll_down & scroll_up
with mock.patch.object(os_input, "send_key_combination", return_value=(True, "ok")):
    res_down = ex.execute_tool("browser_action", {"action": "scroll_down"})
    check("eklentisiz scroll_down çalışıyor", "aşağı kaydırıldı" in res_down.lower(), res_down)

    res_up = ex.execute_tool("browser_action", {"action": "scroll_up"})
    check("eklentisiz scroll_up çalışıyor", "yukarı kaydırıldı" in res_up.lower(), res_up)

# 6d. fill_form
with mock.patch.object(os_input, "type_text", return_value=(True, "ok")):
    res = ex.execute_tool("browser_action", {"action": "fill_form", "text": "test metni"})
    check("eklentisiz fill_form metin yazıyor", "yazıldı" in res.lower(), res)

# 6e. click
with mock.patch.object(os_input, "click", return_value=(True, "ok")):
    res = ex.execute_tool("browser_action", {"action": "click", "coordinate": "320, 150"})
    check("eklentisiz click koordinata tıklıyor", "tıklama yapıldı" in res.lower() and "(320, 150)" in res, res)

# 6f. press_key
with mock.patch.object(os_input, "send_key_combination", return_value=(True, "ok")):
    res = ex.execute_tool("browser_action", {"action": "press_key", "text": "enter"})
    check("eklentisiz press_key çalışıyor", "tuşa basıldı" in res.lower() and "enter" in res, res)

# 7. Eklenti bağlı olduğunda hibrit yönlendirme
dispatched = []
def mock_extension_sender(cmd):
    dispatched.append(cmd)
    return True

ex_hybrid = ToolExecutor(settings=s, browser_sender=mock_extension_sender)
res = ex_hybrid.execute_tool("browser_action", {"action": "close_tab"})
check("eklenti bağlıyken komut eklentiye iletiliyor", "gönderildi" in res.lower() and len(dispatched) == 1, res)
check("eklentiye giden komut doğru", dispatched[0] == {"action": "close_tab"}, str(dispatched))

# 8. Eklenti bağlı değilken (sender False dönerse) otomatik OS fallback
def mock_unconnected_sender(cmd):
    return False

ex_fallback = ToolExecutor(settings=s, browser_sender=mock_unconnected_sender)
with mock.patch.object(os_input, "send_key_combination", return_value=(True, "ok")) as mock_combo:
    res = ex_fallback.execute_tool("browser_action", {"action": "close_tab"})
    check("istemci yokken otomatik OS fallback çalışıyor", "kapatıldı" in res.lower(), res)
    check("fallback send_key_combination çağırdı", mock_combo.called)

# 9. Özetleyiciler (Türkçe ve İngilizce)
set_language("tr")
t, q, d = TOOL_SUMMARIZERS["browser_action"]({"action": "click", "coordinate": "100,200"})
check("TR click özeti", "tıklamak" in q and "100,200" in d, f"{q}|{d}")

t, q, d = TOOL_SUMMARIZERS["browser_action"]({"action": "press_key", "text": "enter"})
check("TR press_key özeti", "tuşuna basmak" in q and "enter" in d, f"{q}|{d}")

set_language("en")
t, q, d = TOOL_SUMMARIZERS["browser_action"]({"action": "click", "coordinate": "100,200"})
check("EN click özeti", "click a button" in q and "100,200" in d, f"{q}|{d}")

t, q, d = TOOL_SUMMARIZERS["browser_action"]({"action": "press_key", "text": "enter"})
check("EN press_key özeti", "press the 'enter' key" in q, f"{q}|{d}")

set_language("tr")

# 10. Şema boyutu testi
tools = get_openai_tools()
schema_str = str(tools)
check("12 araç şeması 6500 karakter altında", len(schema_str) < 6500, f"boyut: {len(schema_str)}")

print(f"\n{len(PASS)} geçti, {len(FAIL)} kaldı.")
if FAIL:
    sys.exit(1)
