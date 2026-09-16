"""Tool Calling birim testleri. Çalıştırma: python3 test_tools.py"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ai_tools import get_openai_tools, ToolExecutor, READ_ONLY_TOOLS, SENSITIVE_READ_TOOLS
from security import SecurityManager


class FakeSettings:
    def __init__(self, d):
        self.d = d

    def get(self, key, fallback=None):
        return self.d.get(key, fallback)


PASS = []
FAIL = []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"[{'OK' if cond else 'HATA'}] {name}" + (f" — {detail}" if detail and not cond else ""))


# 1. Şema geçerliliği ------------------------------------------------
tools = get_openai_tools()
check("9 araç tanımlı", len(tools) == 9, f"bulunan: {len(tools)}")
names = {t["function"]["name"] for t in tools}
check("beklenen araç adları",
      names == {"run_shell_command", "read_file", "write_file", "append_file", "list_directory",
                "get_clipboard_text", "read_screen_text",
                "get_active_window_context", "browser_action"},
      str(names))
check("tüm şemalar function tipinde",
      all(t.get("type") == "function" and "parameters" in t["function"] for t in tools))

# 2. Salt-okunurluk ve hassas-okuma kümeleri -------------------------------
check("read_only seti doğru",
      READ_ONLY_TOOLS == {"read_file", "list_directory", "get_active_window_context"})
check("sensitive seti doğru",
      set(SENSITIVE_READ_TOOLS) == {"read_screen_text", "get_clipboard_text"})

# 3. write/read round-trip (workspace içinde) --------------------------
with tempfile.TemporaryDirectory() as ws:
    s = FakeSettings({"workspace_dir": ws, "require_confirm_on_tool": False,
                      "require_confirm_on_write": False})
    ex = ToolExecutor(settings=s)
    r = ex.execute_tool("write_file", {"path": "alt/klasor/not.txt", "content": "merhaba"})
    check("write_file başarılı", "yazıldı" in r.lower(), r)
    r = ex.execute_tool("read_file", {"path": "alt/klasor/not.txt"})
    check("read_file içeriği okuyor", "merhaba" in r, r)
    r = ex.execute_tool("list_directory", {"path": "alt/klasor"})
    check("list_directory listeliyor", "not.txt" in r, r)

# 4. Workspace dışına taşma engeli --------------------------------------
with tempfile.TemporaryDirectory() as ws:
    s = FakeSettings({"workspace_dir": ws, "require_confirm_on_tool": False})
    ex = ToolExecutor(settings=s)
    # SettingsManager yerine FakeSettings kullandığımız için os.path.realpath kontrolü:
    r = ex.execute_tool("read_file", {"path": "../disari.txt"})
    outside = os.path.realpath(os.path.join(ws, "../disari.txt"))
    inside_ws = outside == os.path.realpath(ws) or outside.startswith(os.path.realpath(ws) + os.sep)
    if inside_ws:
        check("workspace dışına taşma (n/a: tmp yapısı)", True)
    else:
        check("workspace dışına taşma engelleniyor", "çalışma alanı dışı" in r.lower() or "güvenlik" in r.lower(), r)
    r = ex.execute_tool("write_file", {"path": "/etc/tool-test-yazma-denemesi.txt", "content": "x"})
    check("mutlak yolla dışarı yazma engelleniyor", "çalışma alanı dışı" in r.lower() or "güvenlik" in r.lower(), r)

# 5. Onay akışı: red ----------------------------------------------------
with tempfile.TemporaryDirectory() as ws:
    calls = []
    s = FakeSettings({"workspace_dir": ws, "require_confirm_on_tool": True,
                      "require_confirm_on_write": False})
    ex = ToolExecutor(settings=s, confirm_callback=lambda summary: calls.append(summary) or False)
    r = ex.execute_tool("write_file", {"path": "red.txt", "content": "x"})
    check("onay reddedilince işlem yapılmıyor",
          "reddet" in r.lower() and not os.path.exists(os.path.join(ws, "red.txt")), r)
    check("sade özet üretiliyor (başlık+soru+detay)",
          len(calls) == 1 and calls[0].get("title") == "Dosya yazılsın mı?"
          and "red.txt" in (calls[0].get("detail") or ""), str(calls))

# 6. Onay akışı: kabul --------------------------------------------------
with tempfile.TemporaryDirectory() as ws:
    s = FakeSettings({"workspace_dir": ws, "require_confirm_on_tool": True,
                      "require_confirm_on_write": False})
    ex = ToolExecutor(settings=s, confirm_callback=lambda summary: True)
    r = ex.execute_tool("write_file", {"path": "kabul.txt", "content": "ok"})
    check("onay verilince işlem yapılıyor",
          os.path.exists(os.path.join(ws, "kabul.txt")), r)

# 7. Salt-okuma araçları onay istemez -----------------------------------
with tempfile.TemporaryDirectory() as ws:
    open(os.path.join(ws, "a.txt"), "w").write("veri")
    s = FakeSettings({"workspace_dir": ws, "require_confirm_on_tool": True})
    asked = []
    ex = ToolExecutor(settings=s, confirm_callback=lambda summary: asked.append(summary) or False)
    r = ex.execute_tool("read_file", {"path": "a.txt"})
    check("read_file onay sormuyor", not asked and "veri" in r, r)

# 8. Bilinmeyen araç + bozuk JSON ---------------------------------------
ex = ToolExecutor(settings=FakeSettings({}))
check("bilinmeyen araç hata metni",
      "bilinmeyen araç" in ex.execute_tool("yok_boyle", {}).lower())
check("bozuk JSON hata metni",
      "json" in ex.execute_tool("read_file", "{bozuk").lower())

# 9. Tehlikeli komut çifte koruma (onay kapalı olsa bile CLI sorar) ------
with tempfile.TemporaryDirectory() as ws:
    s = FakeSettings({"workspace_dir": ws, "require_confirm_on_tool": False,
                      "require_confirm_on_write": True})
    cli_asked = []
    ex = ToolExecutor(settings=s, confirm_callback=lambda summary: cli_asked.append(summary) or False)
    r = ex.execute_tool("run_shell_command", {"command": "rm -rf /tmp/asla-olmamali"})
    check("tehlikeli komut CLI katmanında onaya takılıyor",
          len(cli_asked) == 1 and ("iptal" in r.lower() or "reddet" in r.lower()), r)
    check("CLI onayı da sade özet formatında",
          isinstance(cli_asked[0], dict) and "rm -rf" in (cli_asked[0].get("detail") or ""),
          str(cli_asked))

# 10. Güvenli komut onaysız çalışır --------------------------------------
with tempfile.TemporaryDirectory() as ws:
    s = FakeSettings({"workspace_dir": ws, "require_confirm_on_tool": False,
                      "require_confirm_on_write": True})
    ex = ToolExecutor(settings=s, confirm_callback=lambda summary: (_ for _ in ()).throw(
        AssertionError("onay sorulmamalıydı")))
    r = ex.execute_tool("run_shell_command", {"command": "echo selam"})
    check("güvenli komut onaysız çalışıyor", "selam" in r, r)

# 11. SecurityManager özet formatı + eski uyumluluk ------------------------
sm = SecurityManager(confirm_callback=lambda summary: summary)
got = sm.ask_confirmation({"title": "T?", "question": "S?", "detail": "D"})
check("security dict özeti iletiyor",
      got == {"title": "T?", "question": "S?", "detail": "D"}, str(got))
got = sm.ask_confirmation("rm -rf /tmp/x")
check("security str girdiyi sade özete çeviriyor",
      isinstance(got, dict) and got.get("title") == "Komut çalıştırılsın mı?"
      and "rm -rf" in (got.get("detail") or ""), str(got))
sm_old = SecurityManager(confirm_callback=lambda q, d="": f"eski:{q}|{d}")
check("security eski iki-argüman callback ile çalışıyor",
      sm_old.ask_confirmation({"title": "T", "question": "S?", "detail": "D"}) == "eski:S?|D")

# 12. LLMClient tool döngüsü (mock HTTP) ----------------------------------
from llm_client import LLMClient


class FakeResp:
    def __init__(self, payload, ok=True, status=200, text=""):
        self._payload = payload
        self.ok = ok
        self.status_code = status
        self.text = text or json.dumps(payload)

    def json(self):
        return self._payload


import unittest.mock as mock

with tempfile.TemporaryDirectory() as ws:
    s = FakeSettings({"workspace_dir": ws, "enable_tool_calling": True,
                      "require_confirm_on_tool": False, "tool_max_iterations": 5,
                      "require_confirm_on_write": False})
    client = LLMClient(settings=s)

    tool_call_msg = {"role": "assistant", "content": None, "tool_calls": [
        {"id": "call_1", "type": "function",
         "function": {"name": "write_file",
                      "arguments": json.dumps({"path": "llm.txt", "content": "via-tool"})}}
    ]}
    final_msg = {"role": "assistant", "content": "Dosya oluşturuldu. [EKRANDA_GOSTER]"}

    posts = []

    def fake_post(url, headers=None, json=None, timeout=None):
        posts.append(json)
        if len(posts) == 1:
            return FakeResp({"choices": [{"message": tool_call_msg}]})
        return FakeResp({"choices": [{"message": final_msg}]})

    with mock.patch("requests.post", side_effect=fake_post):
        out = client._chat_with_tools("http://x", None, "m", [{"role": "user", "content": "yaz"}])

    check("ilk istekte tools gönderiliyor",
          "tools" in posts[0] and posts[0].get("tool_choice") == "auto", str(posts[0].keys()))
    check("tool sonucu role=tool olarak geri veriliyor",
          any(m.get("role") == "tool" and m.get("tool_call_id") == "call_1" for m in posts[1]["messages"]))
    check("dosya gerçekten yazılıyor", os.path.exists(os.path.join(ws, "llm.txt")))
    check("final içerik dönüyor", "oluşturuldu" in out, out)

# 13. tools desteklemeyen sunucu fallback ---------------------------------
s = FakeSettings({"enable_tool_calling": True})
client = LLMClient(settings=s)
calls = []

def fake_post_400(url, headers=None, json=None, timeout=None):
    calls.append(json)
    if "tools" in json:
        return FakeResp({}, ok=False, status=400, text='{"error": "unsupported parameter tools"}')
    return FakeResp({"choices": [{"message": {"role": "assistant", "content": "düz yanıt"}}]})

with mock.patch("requests.post", side_effect=fake_post_400):
    out = client._chat_with_tools("http://x", None, "m", [{"role": "user", "content": "selam"}])
check("400+tools hatasında tools'suz retry", len(calls) == 2 and "tools" not in calls[1] and out == "düz yanıt",
      f"calls={len(calls)} out={out!r}")

# 14. Router bağlantıları --------------------------------------------------
from router import Router
r = Router(settings=FakeSettings({}), confirm_callback=lambda summary: True)
check("Router LLMClient'a callback iletiyor", r.llm.confirm_callback is not None)
r.set_browser_sender(lambda c: None)
check("set_browser_sender çalışıyor", r.llm.browser_sender is not None)

# 15. Hassas okuma: auto_allow kapalıysa ekran/pano onayı sorulur -----------
import unittest.mock as _mock  # noqa: E402 (mock zaten import edildi, tekrar güvenli)

asked = []
s = FakeSettings({"auto_allow_clipboard": False})
ex = ToolExecutor(settings=s, confirm_callback=lambda summary: asked.append(summary) or False)
with _mock.patch("ai_tools.read_screen_via_ocr", return_value="gizli ekran"):
    out = ex.execute_tool("read_screen_text", {})
check("ekran onayı soruluyor ve red okumayı engelliyor",
      len(asked) == 1 and "izin vermedi" in out.lower() and "gizli" not in out, out)
check("ekran onayı sade formatta",
      asked[0].get("title") == "Ekran okunsun mu?"
      and "ekran" in asked[0].get("question", "") and "remember_key" not in asked[0],
      str(asked))

asked_pano = []
s = FakeSettings({"auto_allow_clipboard": False})
ex = ToolExecutor(settings=s, confirm_callback=lambda summary: asked_pano.append(summary) or False)
with _mock.patch("ai_tools.read_clipboard_subprocess", return_value="gizli pano"):
    out = ex.execute_tool("get_clipboard_text", {})
check("pano onayı soruluyor ve red okumayı engelliyor",
      len(asked_pano) == 1 and "izin vermedi" in out.lower() and "gizli" not in out, out)

# 16. Hassas okuma: auto_allow açıksa sorulmadan okunur ----------------------
s = FakeSettings({"auto_allow_clipboard": True})
ex = ToolExecutor(settings=s, confirm_callback=lambda summary: (_ for _ in ()).throw(
    AssertionError("onay sorulmamalıydı")))
with _mock.patch("ai_tools.read_screen_via_ocr", return_value="ekran içeriği"):
    out = ex.execute_tool("read_screen_text", {})
check("auto_allow açıkken ekran sorulmadan okunuyor", "ekran içeriği" in out, out)

# 17. Hassas okuma: onay verilince içerik LLM'e gidiyor ----------------------
s = FakeSettings({"auto_allow_clipboard": False})
ex = ToolExecutor(settings=s, confirm_callback=lambda summary: True)
with _mock.patch("ai_tools.read_clipboard_subprocess", return_value="panodaki sır"):
    out = ex.execute_tool("get_clipboard_text", {})
check("onay verilince pano içeriği dönüyor", "panodaki sır" in out, out)

# 18. Özetleyiciler: shell / browser -----------------------------------------
from ai_tools import TOOL_SUMMARIZERS
t, q, d = TOOL_SUMMARIZERS["run_shell_command"]({"command": "ls -la /tmp"})
check("shell özeti sade", t == "Komut çalıştırılsın mı?" and d == "$ ls -la /tmp", f"{t}|{q}|{d}")
t, q, d = TOOL_SUMMARIZERS["browser_action"]({"action": "scroll_down"})
check("browser özeti türkçe", t == "Tarayıcıda işlem yapılsın mı?"
      and "aşağı kaydır" in q, f"{t}|{q}|{d}")

# 19. last_tools_used takibi --------------------------------------------------
s = FakeSettings({"enable_tool_calling": True, "require_confirm_on_tool": False,
                  "require_confirm_on_write": False})
client = LLMClient(settings=s)
check("last_tools_used başlangıçta boş", client.last_tools_used == [])

tool_call_msg = {"role": "assistant", "content": None, "tool_calls": [
    {"id": "c1", "type": "function",
     "function": {"name": "get_active_window_context", "arguments": "{}"}},
    {"id": "c2", "type": "function",
     "function": {"name": "read_file", "arguments": '{"path": "yok.txt"}'}},
]}
final_msg = {"role": "assistant", "content": "özet"}

def fake_post_tools(url, headers=None, json=None, timeout=None):
    if len([1 for m in json["messages"] if m.get("role") == "tool"]) == 0 and "tools" in json:
        return FakeResp({"choices": [{"message": tool_call_msg}]})
    return FakeResp({"choices": [{"message": final_msg}]})

with mock.patch("requests.post", side_effect=fake_post_tools):
    client._chat_with_tools("http://x", None, "m", [{"role": "user", "content": "s"}])
check("last_tools_used çağrılan araçları içeriyor",
      set(client.last_tools_used) == {"get_active_window_context", "read_file"},
      str(client.last_tools_used))

with mock.patch("requests.post", side_effect=lambda *a, **k: FakeResp(
        {"choices": [{"message": {"role": "assistant", "content": "düz"}}]})):
    client._chat_with_tools("http://x", None, "m", [{"role": "user", "content": "s"}])
check("last_tools_used her çağrıda sıfırlanıyor", client.last_tools_used == [],
      str(client.last_tools_used))

# 20. append_file: sona ekleme + yeni dosya -------------------------------
with tempfile.TemporaryDirectory() as ws:
    s = FakeSettings({"workspace_dir": ws, "require_confirm_on_tool": False,
                      "require_confirm_on_write": False})
    ex = ToolExecutor(settings=s)
    ex.execute_tool("write_file", {"path": "log.txt", "content": "satır1\n"})
    r = ex.execute_tool("append_file", {"path": "log.txt", "content": "satır2\n"})
    with open(os.path.join(ws, "log.txt"), encoding="utf-8") as f:
        content = f.read()
    check("append mevcut dosyaya ekliyor",
          content == "satır1\nsatır2\n" and "eklendi" in r, f"{content!r} / {r}")
    r = ex.execute_tool("append_file", {"path": "yeni/olusan.txt", "content": "ilk"})
    check("append yoksa oluşturuyor",
          os.path.exists(os.path.join(ws, "yeni/olusan.txt")) and "oluşturulup" in r, r)
    r = ex.execute_tool("append_file", {"path": "/etc/disari-append.txt", "content": "x"})
    check("append workspace dışına taşamıyor",
          "çalışma alanı dışı" in r.lower() or "güvenlik" in r.lower(), r)
    t, q, d = TOOL_SUMMARIZERS["append_file"]({"path": "a.txt", "content": "xx"})
    check("append özeti sade",
          t == "Dosyaya eklensin mi?" and "a.txt" in (d or ""), f"{t}|{q}|{d}")
    # Onay katmanı append için de çalışıyor mu?
    s2 = FakeSettings({"workspace_dir": ws, "require_confirm_on_tool": True})
    asked_app = []
    ex2 = ToolExecutor(settings=s2, confirm_callback=lambda summary: asked_app.append(summary) or False)
    r = ex2.execute_tool("append_file", {"path": "log.txt", "content": "hayır"})
    check("append onayı soruluyor",
          len(asked_app) == 1 and "reddet" in r.lower(), r)

# 21. Hata mesajı kısaltma --------------------------------------------------
from llm_client import _short_err
long_html = "<html>" + "x" * 2000 + "</html>"
short = _short_err(f"HTTP 404: {long_html}")
check("uzun hata 500 civarında kesiliyor",
      len(short) < 700 and "kesildi" in short and short.startswith("HTTP 404: <html>"))
check("kısa hata aynen geçiyor", _short_err("HTTP 500: boom") == "HTTP 500: boom")

# 404 + tools içermeyen hata da kısaltılmış RuntimeError üretiyor mu?
s = FakeSettings({"enable_tool_calling": False})
client = LLMClient(settings=s)
big_err = FakeResp({}, ok=False, status=404, text="<html>" + "y" * 3000 + "</html>")
with mock.patch("requests.post", return_value=big_err):
    out = client.generate_response("sys", "soru")
check("404 HTML hatası kısaltılarak dönüyor",
      "404" in out and len(out) < 900 and "kesildi" in out, f"len={len(out)}")

# 22. Konuşma geçmişi (hafıza) ----------------------------------------------
s = FakeSettings({"llm_mode": "local", "enable_tool_calling": False,
                  "history_max_turns": 2, "local_api_url": "http://x",
                  "local_model": "m"})
client = LLMClient(settings=s)
sent = []

def fake_post_hist(url, headers=None, json=None, timeout=None):
    sent.append(json)
    n = len(sent)
    return FakeResp({"choices": [{"message": {"role": "assistant", "content": f"yanıt{n}"}}]})

with mock.patch("requests.post", side_effect=fake_post_hist):
    client.generate_response("sys", "soru1")
    client.generate_response("sys", "soru2")
    client.generate_response("sys", "soru3")

check("hafıza: ilk istekte geçmiş yok",
      [m["role"] for m in sent[0]["messages"]] == ["system", "user"])
check("hafıza: ikinci istekte 1 tur geçmiş var",
      [m["role"] for m in sent[1]["messages"]] == ["system", "user", "assistant", "user"]
      and sent[1]["messages"][1]["content"] == "soru1"
      and sent[1]["messages"][2]["content"] == "yanıt1")
check("hafıza: limit=2 tur aşılınca eski tur atılıyor",
      len([m for m in sent[2]["messages"] if m["role"] != "system"]) == 5
      and sent[2]["messages"][1]["content"] == "soru1"
      and all(m["content"] != "soru1" or i == 1
              for i, m in enumerate(sent[2]["messages"])),
      str([m.get("content", "")[:10] for m in sent[2]["messages"]]))
check("hafıza: ham soru saklanıyor (context enjekte edilmemiş)",
      all("KULLANICININ EKRANINDAN" not in m.get("content", "")
          for m in client.history))

# use_history=False geçmişi okumaz ve yazmaz
with mock.patch("requests.post", side_effect=fake_post_hist):
    before = len(client.history)
    client.generate_response("sys", "gizli", use_history=False)
check("use_history=False geçmişi değiştirmiyor",
      len(client.history) == before and sent[-1]["messages"][-1]["content"] == "gizli"
      and len(sent[-1]["messages"]) == 2, str(len(client.history)))

# skip_injection (dikte düzeltici) geçmişe dokunmaz
with mock.patch("requests.post", side_effect=fake_post_hist):
    client.generate_response("sys", "dikte", skip_injection=True)
check("skip_injection geçmişe yazmıyor", len(client.history) == before)

# history_max_turns=0 hafızayı kapatır
s0 = FakeSettings({"llm_mode": "local", "enable_tool_calling": False,
                   "history_max_turns": 0, "local_api_url": "http://x", "local_model": "m"})
c0 = LLMClient(settings=s0)
with mock.patch("requests.post", side_effect=lambda *a, **k: FakeResp(
        {"choices": [{"message": {"role": "assistant", "content": "y"}}]})):
    c0.generate_response("sys", "s1")
check("limit=0 iken hafıza kapalı", c0.history == [])

# CLI modu geçmişi düz metne çevirir
s_cli = FakeSettings({"llm_mode": "cli", "history_max_turns": 3})
c_cli = LLMClient(settings=s_cli)
c_cli.history = [{"role": "user", "content": "önceki soru"},
                 {"role": "assistant", "content": "önceki yanıt"}]

class FakeProc:
    stdout = "cli yanıt"; stderr = ""; returncode = 0

with mock.patch("subprocess.run", return_value=FakeProc()) as mrun:
    c_cli.generate_response("SYS", "yeni soru")
sent_input = mrun.call_args[1].get("input", "")
check("CLI geçmişi düz metin olarak iletiyor",
      "[Kullanıcı]: önceki soru" in sent_input
      and "[Asistan]: önceki yanıt" in sent_input
      and "[Kullanıcı]: yeni soru" in sent_input, sent_input[:200])

# clear_history
client.clear_history()
check("clear_history sıfırlıyor", client.history == [])

# 23. Tool loglama (Loglar/) -------------------------------------------------
import glob as _glob
log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Loglar")
before_logs = set(_glob.glob(os.path.join(log_dir, "araclar-*.md"))) if os.path.isdir(log_dir) else set()
ex = ToolExecutor(settings=FakeSettings({"workspace_dir": tempfile.gettempdir(),
                                         "require_confirm_on_tool": False,
                                         "require_confirm_on_write": False}))
ex.execute_tool("run_shell_command", {"command": "echo logtest"})
ex.execute_tool("yok_boyle_arac", {})
after_logs = set(_glob.glob(os.path.join(log_dir, "araclar-*.md")))
new_logs = after_logs - before_logs
check("Loglar/ klasörü ve günlük dosya oluşuyor", bool(after_logs),
      f"log_dir={log_dir}")
if after_logs:
    newest = max(after_logs, key=os.path.getmtime)
    with open(newest, encoding="utf-8") as f:
        log_text = f.read()
    check("araç çağrısı loga düşüyor",
          "`run_shell_command` [tamam]" in log_text and "logtest" in log_text,
          log_text[-300:])
    check("bilinmeyen araç da loglanıyor",
          "`yok_boyle_arac` [bilinmeyen-araç]" in log_text, log_text[-300:])

print(f"\n{len(PASS)} geçti, {len(FAIL)} kaldı.")
if FAIL:
    print("Kalanlar:", FAIL)
    sys.exit(1)
