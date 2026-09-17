"""Updater + i18n birim testleri. Çalıştırma: python3 tests/test_updater.py"""
import io
import json
import os
import sys
import tempfile
import unittest.mock as mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core import updater
from src.core.i18n import tr, set_language, get_language

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"[{'OK' if cond else 'HATA'}] {name}" + (f" — {detail}" if detail and not cond else ""))


# 1. Sürüm karşılaştırma ----------------------------------------------
check("v öneki yoksayılıyor", updater.parse_version("v1.2.3") == (1, 2, 3))
check("bozuk sürüm güvenli", updater.parse_version("xyz") == (0, 0, 0) or updater.parse_version("xyz")[0] == 0)
check("yeni sürüm algılanıyor", updater.is_newer("1.1.0", "1.0.3"))
check("aynı sürüm yeni değil", not updater.is_newer("1.0.3", "1.0.3"))
check("eski sürüm yeni değil", not updater.is_newer("1.0.1", "1.0.3"))
check("uzunluk farkı dengeleniyor", updater.is_newer("1.0.3.1", "1.0.3") and not updater.is_newer("1.0", "1.0.0"))

# 2. check_for_updates (mock ağ) ----------------------------------------
payload = {"tag_name": "v9.9.9", "body": "## Yenilikler\n- x",
           "html_url": "https://github.com/x/y/releases/tag/v9.9.9"}


class FakeResp:
    def __init__(self, data):
        self._buf = io.BytesIO(json.dumps(data).encode())

    def read(self):
        return self._buf.read()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


with mock.patch.object(updater.urllib.request, "urlopen", return_value=FakeResp(payload)):
    info = updater.check_for_updates(current="1.0.0")
check("yeni release bulunuyor",
      info["ok"] and info["update_available"] and info["latest_version"] == "9.9.9"
      and "Yenilikler" in info["release_notes"] and info["html_url"].endswith("v9.9.9"), str(info))
with mock.patch.object(updater.urllib.request, "urlopen", return_value=FakeResp(payload)):
    info2 = updater.check_for_updates(current="9.9.9")
check("güncelse bayrak inmiyor", info2["ok"] and not info2["update_available"])
with mock.patch.object(updater.urllib.request, "urlopen", side_effect=TimeoutError("yok")):
    info3 = updater.check_for_updates(current="1.0.0")
check("ağ hatası ok=False", not info3["ok"] and "error" in info3, str(info3))

# 3. perform_update: git olmayan klasör ---------------------------------
with tempfile.TemporaryDirectory() as d:
    r = updater.perform_update(d)
check("git'siz klasörde güncelleme reddediliyor",
      not r["ok"] and not r["restart_needed"] and "Git" in r["message"], str(r))
check("is_git_checkout kökte True", updater.is_git_checkout(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

# 4. i18n: TR varsayılan, EN çeviriyor -----------------------------------
set_language("tr")
check("varsayılan dil tr", get_language() == "tr" and tr("Onay") == "Onay")
set_language("en")
check("EN çeviri çalışıyor", tr("Onay") == "Confirm" and tr("Ekran okunsun mu?") == "Allow screen reading?")
check("bilinmeyen metin aynen geçiyor", tr("çevrilmemiş-xyz") == "çevrilmemiş-xyz")
check("format şablonu EN", tr("Komut bulunamadı: '{cmd}'").format(cmd="foo") == "Command not found: 'foo'")
set_language("xx")
check("geçersiz dil TR'ye düşüyor", get_language() == "tr")
set_language("tr")

# 5. Özetleyiciler dile uyuyor -------------------------------------------
from src.tools.executor import TOOL_SUMMARIZERS
set_language("en")
t, q, d = TOOL_SUMMARIZERS["run_shell_command"]({"command": "ls"})
check("shell özeti EN", t == "Run this command?" and q == "The following command will run:", f"{t}|{q}")
t, q, d = TOOL_SUMMARIZERS["browser_action"]({"action": "scroll_down"})
check("browser özeti EN", t == "Allow browser action?" and "scroll the page down" in q, f"{t}|{q}")
t, q, d = TOOL_SUMMARIZERS["write_file"]({"path": "a", "content": "xx"})
check("write şablonu EN", "2 characters" in q, q)
set_language("tr")
t, q, d = TOOL_SUMMARIZERS["run_shell_command"]({"command": "ls"})
check("TR'ye dönüş sorunsuz", t == "Komut çalıştırılsın mı?", t)

print(f"\n{len(PASS)} geçti, {len(FAIL)} kaldı.")
if FAIL:
    print("Kalanlar:", FAIL)
    sys.exit(1)
