"""
OpenAI-formatında Tool Calling desteği için araç tanımları ve çalıştırıcı.

Kullanım:
    from src.tools.executor import get_openai_tools, ToolExecutor

    executor = ToolExecutor(settings=settings, confirm_callback=cb)
    result_text = executor.execute_tool("read_file", '{"path": "/tmp/x.txt"}')

Güvenlik modeli (üç katman):
  1. Hassas okuma araçları (ekran/pano): auto_allow_clipboard=False ise
     HER SEFERİNDE security.py üzerinden onay sorulur.
  2. Yazma/çalıştırma araçları: require_confirm_on_tool=True ise onay sorulur.
  3. Onay kapalı olsa bile tehlikeli shell komutları, CLIExecutor içindeki
     mevcut require_confirm_on_write kontrolünden geçmeye devam eder.
"""

import json
import logging
import os
import shutil
import subprocess
import time

from src.core.security import SecurityManager, detect_workspace_escapes
from src.core.i18n import tr

logger = logging.getLogger(__name__)

# Workspace kaçışına karşı onay sorulacak dosya araçları.
# Bunlar normalde _resolve_inside_workspace ile sessizce engellenir; dışarı
# taşma tespit edilirse önce kullanıcıya tek-seferlik onay sorulur ve ret
# halinde eski davranış (sessiz engelleme) korunur.
FILE_TOOLS_WITH_WORKSPACE_GUARD = {
    "read_file",
    "write_file",
    "append_file",
    "list_directory",
}

# Salt-okuma araçları: asla onay gerektirmez (side-effect yok, hassas veri yok)
READ_ONLY_TOOLS = {
    "read_file",
    "list_directory",
    "get_active_window_context",
    "web_search",
    "fetch_web_page",
}

# Hassas okuma araçları: side-effect yok ama özel veri okur (ekran görüntüsü,
# pano içeriği). auto_allow_clipboard=False ise her seferinde onay sorulur.
# Değer: (başlık, soru) — detay/hint popup'ta gösterilmez, sade tutulur.
SENSITIVE_READ_TOOLS = {
    "read_screen_text": ("Ekran okunsun mu?", "Yapay zeka ekranının görüntüsünü okumak istiyor."),
    "get_clipboard_text": ("Pano okunsun mu?", "Yapay zeka panondaki metni okumak istiyor."),
}

# Yazma/çalıştırma araçları için sade başlık+soru üreten fonksiyonlar.
# Her biri args dict'i alır, (başlık, soru, detay) döndürür.
def _summarize_shell(args):
    cmd = (args.get("command") or "").strip()
    short = cmd if len(cmd) <= 80 else cmd[:80] + "…"
    return (tr("Komut çalıştırılsın mı?"), tr("Şu komut çalıştırılacak:"), f"$ {short}")


def _summarize_write(args):
    path = (args.get("path") or "").strip()
    size = len(args.get("content", ""))
    return (tr("Dosya yazılsın mı?"), tr("Şu dosyaya yazılacak ({size} karakter):").format(size=size), path)


def _summarize_append(args):
    path = (args.get("path") or "").strip()
    size = len(args.get("content", ""))
    return (tr("Dosyaya eklensin mi?"), tr("Şu dosyanın sonuna eklenecek ({size} karakter):").format(size=size), path)


def _summarize_browser(args):
    action = (args.get("action") or "").strip()
    labels = {
        "close_tab": tr("açık sekmeyi kapatmak"),
        "scroll_down": tr("sayfayı aşağı kaydırmak"),
        "scroll_up": tr("sayfayı yukarı kaydırmak"),
        "fill_form": tr("forma metin yazmak"),
        "new_tab": tr("yeni sekme açmak"),
    }
    what = labels.get(action, tr("'{action}' işlemini yapmak").format(action=action))
    detail = None
    if args.get("text"):
        detail = tr("Yazılacak metin: ") + ((args["text"][:100] + "…") if len(args["text"]) > 100 else args["text"])
    elif args.get("url"):
        detail = tr("Adres: ") + args["url"]
    return (tr("Tarayıcıda işlem yapılsın mı?"), tr("Yapay zeka {what} istiyor.").format(what=what), detail)


TOOL_SUMMARIZERS = {
    "run_shell_command": _summarize_shell,
    "write_file": _summarize_write,
    "append_file": _summarize_append,
    "browser_action": _summarize_browser,
}

MAX_TOOL_OUTPUT_CHARS = 8000


def truncate_error(text, limit=500):
    """
    Hata mesajını kısaltır. HTML/uzun API hatalarını kullanıcıya
    ham göstermek yerine ilk `limit` karakter + bilgi notu döner.
    """
    text = str(text or "")
    if len(text) <= limit:
        return text
    return text[:limit] + tr("\n... (hata mesajı çok uzun, {n} karakter kesildi)").format(n=len(text) - limit)


# ──────────────────────────────────────────────────────────
#  OpenAI tools şeması
# ──────────────────────────────────────────────────────────

def get_openai_tools():
    """OpenAI chat/completions 'tools' parametresi için şema listesi."""
    return [
        {
            "type": "function",
            "function": {
                "name": "run_shell_command",
                "description": (
                    "Terminal komutu çalıştırır (ls, git, paket bilgisi...). "
                    "DOSYA YAZMA YASAK (echo/cat>heredoc) → write_file kullan. "
                    "WEB ARAMA YASAK (curl kazıma, python-requests) → web_search kullan. "
                    "Çıktı stdout+stderr ve exit code olarak döner."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "description": "Shell komutu, örn: 'ls -la'",
                        },
                    },
                    "required": ["command"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Metin dosyası okur (workspace dışına taşan yol reddedilir).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Dosya yolu."},
                    },
                    "required": ["path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "write_file",
                "description": (
                    "Dosya oluşturur/üzerine yazar. Dosya yazmanın TEK yolu "
                    "(shell-echo YASAK). Yalnızca workspace içine."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Dosya yolu."},
                        "content": {"type": "string", "description": "İçerik."},
                    },
                    "required": ["path", "content"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "append_file",
                "description": (
                    "Dosya SONUNA ekler (yoksa oluşturur). Satır eklemek için "
                    "write_file yerine bunu kullan. Yalnızca workspace içine."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Dosya yolu."},
                        "content": {"type": "string", "description": "Eklenecek içerik."},
                    },
                    "required": ["path", "content"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "list_directory",
                "description": "Klasör listeler (boş = workspace).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Klasör (boş = workspace).",
                        },
                    },
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_clipboard_text",
                "description": "Panodaki metni okur.",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "read_screen_text",
                "description": "Ekranı OCR ile okur ('ekranda ne var' sorularında).",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_active_window_context",
                "description": "Çalan medyayı listeler (şarkı/YouTube bilgisi).",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "browser_action",
                "description": (
                    "Tarayıcıyı kontrol eder (eklenti gerekli): sekme kapat, "
                    "kaydır, forma yaz, yeni sekme."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "description": "close_tab, scroll_down, scroll_up, fill_form, new_tab",
                        },
                        "text": {
                            "type": "string",
                            "description": "fill_form metni.",
                        },
                        "url": {
                            "type": "string",
                            "description": "new_tab adresi.",
                        },
                    },
                    "required": ["action"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "web_search",
                "description": (
                    "İnternette ara (DuckDuckGo, anahtarsız). Güncel olay/sürüm/wiki/"
                    "hata çözümü gibi konularda kullan; shell ile kazıma YASAK. "
                    "Başlık+adres+özet döndürür; detay için fetch_web_page."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Sorgu, örn: 'Minecraft Wesper mod nedir'",
                        },
                        "max_results": {
                            "type": "integer",
                            "description": "Sonuç sayısı (1-10, öntanımlı 5).",
                        },
                    },
                    "required": ["query"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "fetch_web_page",
                "description": (
                    "Sayfayı metne çevirip okur (menü/reklam temizlenir). "
                    "web_search adreslerini derinlemesine okumak için kullan."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {
                            "type": "string",
                            "description": "Sayfa adresi (https://...).",
                        },
                        "query": {
                            "type": "string",
                            "description": "Opsiyonel: sayfada aranan konu (çıktıyı kısaltır).",
                        },
                    },
                    "required": ["url"],
                },
            },
        },
    ]


# ──────────────────────────────────────────────────────────
#  Yardımcılar (GUI katmanından refactor edildi)
# ──────────────────────────────────────────────────────────

def _ocr_image(png_path, txt_base):
    """Tek PNG'yi tesseract ile okur, metni döndürür (boş olabilir)."""
    import os as _os
    import subprocess as _sp
    try:
        r = _sp.run(["tesseract", png_path, txt_base, "-l", "tur+eng"],
                    capture_output=True, timeout=30)
        if r.returncode == 0 and _os.path.exists(txt_base + ".txt"):
            with open(txt_base + ".txt", "r", encoding="utf-8") as f:
                return f.read().strip()
    except Exception as e:
        logger.error(f"OCR Hatası: {e}")
    return ""


def read_screen_via_ocr():
    """
    Ekran görüntüsü alıp tesseract OCR ile metne çevirir.
    Hassas geçici dosyaları kullandıktan sonra siler.

    Not: KDE Wayland'da spectacle bazen boş/kilitli kare yakalar.
    Bu yüzden en fazla 3 kare denenir, ilk anlamlı sonuç (>20 karakter) alınır.
    """
    import os as _os
    import subprocess as _sp
    import time as _time
    ss_path = "/tmp/ai_dikte_screen.png"
    txt_path = "/tmp/ai_dikte_screen"
    best = ""
    try:
        for attempt in range(3):
            if attempt:
                _time.sleep(0.5)
            if _sp.run(["grim", ss_path], capture_output=True).returncode != 0:
                # grim yoksa/çalışmazsa spectacle (KDE), sonra gnome-screenshot
                if _sp.run(["spectacle", "-b", "-n", "-o", ss_path],
                           capture_output=True).returncode != 0:
                    _sp.run(["gnome-screenshot", "-f", ss_path], capture_output=True)
            if not _os.path.exists(ss_path):
                continue
            text = _ocr_image(ss_path, txt_path)
            if len(text) > len(best):
                best = text
            if len(best) > 20:
                break
    except Exception as e:
        logger.error(f"OCR Hatası: {e}")
    finally:
        try:
            if _os.path.exists(ss_path):
                _os.remove(ss_path)
            if _os.path.exists(txt_path + ".txt"):
                _os.remove(txt_path + ".txt")
        except OSError:
            pass
    return best


def read_clipboard_subprocess():
    """Qt'ye dokunmadan (thread-safe) panoyu okur. Wayland + X11 destekli."""
    for cmd in (["wl-paste", "-n"], ["xclip", "-o", "-selection", "clipboard"]):
        if shutil.which(cmd[0]) is None:
            continue
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                return result.stdout
        except Exception:
            continue
    return ""


# ──────────────────────────────────────────────────────────
#  Web arama + sayfa okuma (DuckDuckGo / requests, API anahtarsız)
# ──────────────────────────────────────────────────────────

import html as _html
import re as _re
import urllib.parse as _urlparse

_WEB_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120 Safari/537.36"
    )
}
_WEB_TIMEOUT = 20
_WEB_MAX_DOWNLOAD_BYTES = 1_500_000  # 1.5 MB: devasa sayfalara karşı üst sınır
# Token cimriliği sabitleri: snippet/sayfa kırpma bu limitlerle yapılır
# (MAX_TOOL_OUTPUT_CHARS=8000 genel tavan olarak kalır).
_WEB_SNIPPET_CHARS = 200   # arama sonucu başına özet uzunluğu
_WEB_FETCH_CHARS = 4000    # fetch_web_page çıktısı üst sınırı (~1000 token)


def _unwrap_ddg_href(href):
    """DuckDuckGo //duckduckgo.com/l/?uddg=<urlencoded> bağlantısını çözer."""
    href = _html.unescape(href or "")
    if "duckduckgo.com/l/" in href and "uddg=" in href:
        try:
            qs = _urlparse.parse_qs(_urlparse.urlsplit(href).query)
            if qs.get("uddg"):
                return qs["uddg"][0]
        except Exception:
            pass
    return href


def duckduckgo_search(query, max_results=5):
    """DuckDuckGo HTML ucundan arama yapar, API anahtarı gerekmez.

    Döner: [{"title": str, "url": str, "snippet": str}, ...]
    Ağ/format hatalarında exception fırlatır (çağıran yakalar).
    """
    import requests

    query = (query or "").strip()
    if not query:
        raise ValueError("query boş")
    max_results = max(1, min(int(max_results or 5), 10))

    resp = requests.get(
        "https://html.duckduckgo.com/html/",
        params={"q": query},
        headers=_WEB_HEADERS,
        timeout=_WEB_TIMEOUT,
    )
    resp.raise_for_status()

    links = _re.findall(
        r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
        resp.text, _re.S,
    )
    snippets = _re.findall(
        r'class="result__snippet"[^>]*>(.*?)</a>', resp.text, _re.S,
    )
    if not snippets:
        # Yedek desen: snippet etiketi farklı kapanıyorsa
        snippets = _re.findall(
            r'class="result__snippet"[^>]*>(.*?)</', resp.text, _re.S,
        )

    results = []
    for i, (href, raw_title) in enumerate(links[:max_results]):
        title = _html.unescape(_re.sub(r"<[^>]+>", "", raw_title)).strip()
        snip = ""
        if i < len(snippets):
            snip = _html.unescape(_re.sub(r"<[^>]+>", "", snippets[i])).strip()
        results.append({
            "title": title or "(başlıksız)",
            "url": _unwrap_ddg_href(href),
            "snippet": snip,
        })
    return results


def html_to_text(page_html):
    """Ham HTML'yi LLM'in okuyabileceği düz metne çevirir (token cimrisi).

    script/style/noscript + header/footer/nav/aside blokları atılır;
    kalan metin sıkıştırılır. Menü artıkları için fetch tarafı ayrıca
    pick_relevant_sections ile soru-odaklı kırpma yapar.
    """
    text = page_html or ""
    # Gürültü blokları: sayfa iskeleti token şişirir, bilgi taşımaz
    text = _re.sub(r"<(script|style|noscript|header|footer|nav|aside)[^>]*>.*?</\1>",
                   " ", text, flags=_re.S | _re.I)
    # Blok etiketleri önce satır sonuna çevir (paragraf sınırları korunsun)
    text = _re.sub(r"</?(?:p|br|li|h[1-6]|tr|div|article|section)[^>]*>",
                   "\n\n", text, flags=_re.I)
    text = _re.sub(r"<[^>]+>", " ", text)   # kalan (satır-içi) etiketler → boşluk
    text = _html.unescape(text)
    text = _re.sub(r"[ \t\xa0]+", " ", text)          # yatay boşlukları sıkıştır
    text = _re.sub(r"\n\s*\n+", "\n\n", text)         # çoklu satır boşlukları
    return text.strip()


def fetch_url_as_text(url):
    """URL'deki sayfayı indirip düz metne çevirir.

    Döner: (metin, içerik_türü). HTML olmayan içerik (PDF vb.) veya
    hata durumlarında exception fırlatır (çağıran yakalar).
    """
    import requests

    url = (url or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        raise ValueError("URL http:// veya https:// ile başlamalı")

    resp = requests.get(url, headers=_WEB_HEADERS,
                        timeout=_WEB_TIMEOUT, stream=True)
    resp.raise_for_status()

    content_type = (resp.headers.get("Content-Type", "") or "").lower()
    if "html" not in content_type and "text" not in content_type:
        raise ValueError(f"desteklenmeyen içerik türü: {content_type or 'bilinmiyor'}")

    chunks, total = [], 0
    for chunk in resp.iter_content(chunk_size=65536, decode_unicode=True):
        if not chunk:
            continue
        if isinstance(chunk, bytes):
            chunk = chunk.decode(resp.encoding or "utf-8", errors="replace")
        total += len(chunk)
        if total > _WEB_MAX_DOWNLOAD_BYTES:
            break
        chunks.append(chunk)
    page_html = "".join(chunks)
    if not page_html.strip():
        raise ValueError("sayfa içeriği boş")
    return html_to_text(page_html), content_type


# ──────────────────────────────────────────────────────────
#  ToolExecutor
# ──────────────────────────────────────────────────────────

class ToolExecutor:
    def __init__(self, settings=None, confirm_callback=None, browser_sender=None):
        """
        settings:         SettingsManager (workspace_dir, require_confirm_on_tool okunur)
        confirm_callback: (display_text, explanation) -> bool
        browser_sender:   browser komutlarını eklentiye ileten fonksiyon (dict -> None)
        """
        self.settings = settings
        self.security = SecurityManager(confirm_callback=confirm_callback)
        self.browser_sender = browser_sender

    # -- ayar yardımcıları ---------------------------------

    def _workspace(self):
        ws = ""
        if self.settings:
            ws = (self.settings.get("workspace_dir", "") or "").strip()
        if ws and os.path.isdir(ws):
            return os.path.realpath(ws)
        return os.path.expanduser("~")

    def _require_confirm(self):
        if self.settings:
            return bool(self.settings.get("require_confirm_on_tool", False))
        return False

    def _resolve_inside_workspace(self, path, allow_escape=False):
        """Yolu workspace içine sabitle; dışarı taşma varsa ValueError.

        allow_escape=True ise workspace dışındaki yolu aynen çözümleyip
        döndürür (kullanıcı kaçış penceresinde tek seferlik izin vermişse).
        """
        ws = self._workspace()
        abs_path = path if os.path.isabs(path) else os.path.join(ws, path)
        real = os.path.realpath(abs_path)
        if real != ws and not real.startswith(ws + os.sep):
            if allow_escape:
                return real
            raise ValueError(
                tr("Güvenlik: '{path}' çalışma alanı dışına taşıyor (workspace: {ws}).").format(path=path, ws=ws)
            )
        return real

    def _workspace_assigned(self):
        """Kullanıcı settings'te geçerli bir workspace dizini atamış mı?"""
        if not self.settings:
            return False
        ws = (self.settings.get("workspace_dir", "") or "").strip()
        return bool(ws) and os.path.isdir(ws)

    def _ask_workspace_escape(self, name, args, outside_paths, command_text=None):
        """Workspace kaçış girişimini kullanıcıya sorar (özel vurgulu özet).

        outside_paths: workspace dışına taşan yol string'leri.
        command_text:  shell komutuysa ham komut metni (pencerede gösterilir).
        Döner: True (tek seferlik izin) / False (red).
        GUI'de WorkspaceEscapeDialog, terminalde vurgulu prompt kullanılır.
        """
        shown = list(outside_paths[:5])
        if len(outside_paths) > 5:
            shown.append("…")
        lines = [tr("Dışarı taşan yollar:")] + [f"  • {p}" for p in shown]
        if command_text:
            short = command_text if len(command_text) <= 300 else command_text[:300] + "…"
            lines += ["", f"{tr('Komut:')} {short}"]
        lines += ["", f"{tr('Araç:')} {name}", f"{tr('Çalışma alanı:')} {self._workspace()}"]
        summary = {
            "title": tr("Yapay zeka çalışma alanının dışına çıkmak istiyor!"),
            "question": tr("Bu işlem çalışma alanı dışındaki dosyalara erişiyor:") + "\n" + tr("İzin verilsin mi? (sadece bu seferlik)"),
            "detail": "\n".join(lines),
            "workspace_escape": True,
            "outside_paths": list(outside_paths),
            "command": command_text,
            "tool": name,
        }
        return bool(self.security.ask_confirmation(summary))

    def _precheck_workspace_escape(self, name, args):
        """Kaçış denetimi: workspace atandıysa dışarı taşmayı yakala.

        Döner: (allowed, error_message)
          - Kaçış yoksa veya kullanıcı tek seferlik izin verdiyse: (True, None).
            İzin durumunda args içine "_escape_allowed"=True işlenir.
          - Kullanıcı reddettiyse: (False, LLM'e dönülecek red metni).
        """
        if not self._workspace_assigned():
            return True, None
        if name in FILE_TOOLS_WITH_WORKSPACE_GUARD:
            path = (args.get("path") or "").strip()
            if not path:
                return True, None  # Boş path: handler kendi hatasını üretir
            try:
                self._resolve_inside_workspace(path)
                return True, None
            except ValueError:
                if self._ask_workspace_escape(name, args, [path]):
                    args["_escape_allowed"] = True
                    return True, None
                self._log_tool_call(name, args, "kullanıcı-reddetti")
                return False, tr("Kullanıcı çalışma alanı dışına erişime izin vermedi. Sadece çalışma alanı içindeki dosyalarla devam et.")
        if name == "run_shell_command":
            command = (args.get("command") or "").strip()
            if not command:
                return True, None
            outside = detect_workspace_escapes(command, self._workspace())
            if not outside:
                return True, None
            if self._ask_workspace_escape(name, args, outside, command_text=command):
                args["_escape_allowed"] = True
                return True, None
            self._log_tool_call(name, args, "kullanıcı-reddetti")
            return False, tr("Kullanıcı çalışma alanı dışına erişime izin vermedi. Sadece çalışma alanı içindeki dosyalarla devam et.")
        return True, None

    # -- ana giriş -----------------------------------------

    def execute_tool(self, name, arguments):
        """
        name:      araç adı (örn. "read_file")
        arguments: dict veya JSON string
        Döner:    LLM'e iletilecek sonuç metni (asla exception fırlatmaz).
        """
        try:
            args = json.loads(arguments) if isinstance(arguments, str) else (arguments or {})
        except (json.JSONDecodeError, TypeError) as e:
            self._log_tool_call(name, arguments, "json-hatası")
            return tr("Araç argümanı JSON olarak çözümlenemedi: {e}").format(e=e)

        handler = getattr(self, f"_tool_{name}", None)
        if handler is None:
            self._log_tool_call(name, args, "bilinmeyen-araç")
            return tr("Bilinmeyen araç: '{name}'.").format(name=name)

        # 0. Workspace kaçış denetimi (her şeyden önce):
        #    workspace atandıysa ve araç/comut dışarı taşıyorsa, ayarlardan
        #    bağımsız olarak vurgulu kaçış penceresiyle onay sorulur.
        escape_ok, escape_msg = self._precheck_workspace_escape(name, args)
        if not escape_ok:
            return escape_msg

        # Onay kontrolü (üç katman):
        # 1. Hassas okuma (ekran/pano): auto_allow_clipboard kapalıyken her seferinde sor.
        if name in SENSITIVE_READ_TOOLS:
            auto_allow = self.settings.get("auto_allow_clipboard", False) if self.settings else False
            if not auto_allow:
                _t, _q = SENSITIVE_READ_TOOLS[name]
                title, question = tr(_t), tr(_q)
                summary = {"title": title, "question": question}
                if not self.security.ask_confirmation(summary):
                    self._log_tool_call(name, args, "kullanıcı-reddetti")
                    return tr("Kullanıcı bu okuma işlemine izin vermedi. Ekran/pano içeriğini görmeden, genel bilgiyle cevapla.")
        # 2. Yazma/çalıştırma: require_confirm_on_tool ayara bağlı.
        elif name not in READ_ONLY_TOOLS and self._require_confirm():
            summarizer = TOOL_SUMMARIZERS.get(name)
            if summarizer:
                title, question, detail = summarizer(args)
                summary = {"title": title, "question": question, "detail": detail}
            else:
                summary = tr("Araç: ") + f"{name}\n" + tr("Argüman: ") + json.dumps(args, ensure_ascii=False)[:500]
            if not self.security.ask_confirmation(summary):
                self._log_tool_call(name, args, "kullanıcı-reddetti")
                return tr("Kullanıcı bu işlemi reddetti. İşlem yapılmadı.")

        try:
            result = handler(args)
            status = "tamam"
        except Exception as e:
            logger.error(f"Araç hatası ({name}): {e}")
            result = tr("Araç çalıştırılırken hata ({name}): {e}").format(name=name, e=e)
            status = "hata"
        finally:
            self._log_tool_call(name, args, status)

        result = str(result)
        if len(result) > MAX_TOOL_OUTPUT_CHARS:
            result = result[:MAX_TOOL_OUTPUT_CHARS] + tr("\n... (çıktı çok uzun olduğu için kesildi)")
        return result

    def _log_tool_call(self, name, arguments, status):
        """Her araç çağrısını Loglar/araçlar-YYYY-MM-DD.md dosyasına tek satır yazar."""
        try:
            from src.core.settings import PROJECT_ROOT
            log_dir = os.path.join(PROJECT_ROOT, "Loglar")
            os.makedirs(log_dir, exist_ok=True)
            day = time.strftime("%Y-%m-%d")
            arg_str = json.dumps(arguments, ensure_ascii=False)[:300] if isinstance(arguments, dict) else str(arguments)[:300]
            line = f"- {time.strftime('%H:%M:%S')} `{name}` [{status}] {arg_str}\n"
            with open(os.path.join(log_dir, f"araclar-{day}.md"), "a", encoding="utf-8") as f:
                f.write(line)
        except OSError:
            pass  # Log yazılamazsa araç çalışmaya devam eder

    # -- araç implementasyonları ----------------------------

    def _tool_run_shell_command(self, args):
        command = (args.get("command") or "").strip()
        if not command:
            return tr("Hata: 'command' parametresi boş.")
        # CLIExecutor kendi güvenlik katmanını uygular (çifte koruma).
        from src.llm.cli import CLIExecutor
        executor = CLIExecutor(settings=self.settings,
                               confirm_callback=self.security.confirm_callback)
        res = executor.execute(command)
        out = f"Exit Code: {res['exit_code']}\n"
        if res["stdout"]:
            out += f"STDOUT:\n{res['stdout']}\n"
        if res["stderr"]:
            out += f"STDERR:\n{res['stderr']}"
        if res["status"] == "cancelled":
            out += tr("\nNot: Komut kullanıcı tarafından iptal edildi.")
        return out.strip() or "(çıktı yok)"

    def _tool_read_file(self, args):
        path = (args.get("path") or "").strip()
        if not path:
            return tr("Hata: 'path' parametresi boş.")
        real = self._resolve_inside_workspace(path, allow_escape=bool(args.get("_escape_allowed")))
        if not os.path.isfile(real):
            return tr("Dosya bulunamadı: '{path}'").format(path=path)
        try:
            with open(real, "r", encoding="utf-8", errors="replace") as f:
                content = f.read(MAX_TOOL_OUTPUT_CHARS + 1)
        except OSError as e:
            return tr("Dosya okunamadı: {e}").format(e=e)
        if len(content) > MAX_TOOL_OUTPUT_CHARS:
            content = content[:MAX_TOOL_OUTPUT_CHARS] + tr("\n... (dosya çok uzun, kesildi)")
        return f"[{real}]:\n{content}"

    def _tool_write_file(self, args):
        path = (args.get("path") or "").strip()
        content = args.get("content", "")
        if not path:
            return tr("Hata: 'path' parametresi boş.")
        real = self._resolve_inside_workspace(path, allow_escape=bool(args.get("_escape_allowed")))
        parent = os.path.dirname(real)
        if parent and not os.path.isdir(parent):
            try:
                os.makedirs(parent, exist_ok=True)
            except OSError as e:
                return tr("Klasör oluşturulamadı: {e}").format(e=e)
        try:
            with open(real, "w", encoding="utf-8") as f:
                f.write(content)
        except OSError as e:
            return tr("Dosya yazılamadı: {e}").format(e=e)
        return tr("Dosya yazıldı: {real} ({n} karakter)").format(real=real, n=len(content))

    def _tool_append_file(self, args):
        path = (args.get("path") or "").strip()
        content = args.get("content", "")
        if not path:
            return tr("Hata: 'path' parametresi boş.")
        real = self._resolve_inside_workspace(path, allow_escape=bool(args.get("_escape_allowed")))
        existed = os.path.isfile(real)
        parent = os.path.dirname(real)
        if parent and not os.path.isdir(parent):
            try:
                os.makedirs(parent, exist_ok=True)
            except OSError as e:
                return tr("Klasör oluşturulamadı: {e}").format(e=e)
        try:
            with open(real, "a", encoding="utf-8") as f:
                f.write(content)
        except OSError as e:
            return tr("Dosyaya eklenemedi: {e}").format(e=e)
        return (tr("Dosyaya eklendi: {real} (+{n} karakter)") if existed else tr("Dosya oluşturulup yazıldı: {real} (+{n} karakter)")).format(real=real, n=len(content))

    def _tool_list_directory(self, args):
        path = (args.get("path") or "").strip()
        real = self._resolve_inside_workspace(path, allow_escape=bool(args.get("_escape_allowed"))) if path else self._workspace()
        if not os.path.isdir(real):
            return tr("Klasör bulunamadı: '{path}'").format(path=path or real)
        try:
            entries = sorted(os.listdir(real))
        except OSError as e:
            return tr("Klasör listelenemedi: {e}").format(e=e)
        lines = []
        for e in entries[:200]:
            full = os.path.join(real, e)
            lines.append(f"[D] {e}" if os.path.isdir(full) else f"[F] {e}")
        if len(entries) > 200:
            lines.append(tr("... (+{n} öğe daha)").format(n=len(entries) - 200))
        return tr("[{real}] ({n} öğe):").format(real=real, n=len(entries)) + "\n" + "\n".join(lines)

    def _tool_get_clipboard_text(self, args):
        # Tool yolunda pano "şimdi" okunur (GUI'deki önbellek kullanılmaz).
        text = read_clipboard_subprocess()
        if not text.strip():
            return tr("(Pano boş veya okunamadı)")
        return tr("[Panodaki Metin]:") + f"\n{text}"

    def _tool_read_screen_text(self, args):
        text = read_screen_via_ocr()
        if not text.strip():
            return tr("(Ekrandan metin okunamadı)")
        return tr("[Ekrandaki Metin (OCR)]:") + f"\n{text}"

    def _tool_get_active_window_context(self, args):
        try:
            from src.context.helper import get_active_contexts
        except ImportError as e:
            return tr("Bağlam yardımcısı yüklenemedi: {e}").format(e=e)
        contexts = get_active_contexts()
        if not contexts:
            return tr("(Aktif bağlam bulunamadı)")
        lines = []
        for c in contexts:
            detail = f" — {c['detail']}" if c.get("detail") else ""
            lines.append(f"{c.get('icon', '')} {c.get('label', c.get('type', ''))}: {c.get('title', '')}{detail}")
        return "\n".join(lines)

    def _tool_browser_action(self, args):
        action = (args.get("action") or "").strip()
        if not action:
            return tr("Hata: 'action' parametresi boş.")
        if self.browser_sender is None:
            return (
                tr("Tarayıcı bağlantısı yok (eklenti sunucusu çalışmıyor). Kullanıcıya tarayıcı eklentisini kurmasını hatırlat.")
            )
        command = {"action": action}
        if args.get("text"):
            command.setdefault("params", {})["text"] = args["text"]
        if args.get("url"):
            command.setdefault("params", {})["url"] = args["url"]
        try:
            self.browser_sender(command)
        except Exception as e:
            return tr("Tarayıcı komutu gönderilemedi: {e}").format(e=e)
        return tr("Tarayıcı komutu gönderildi: {action}").format(action=action)

    def _tool_web_search(self, args):
        query = (args.get("query") or "").strip()
        if not query:
            return tr("Hata: 'query' parametresi boş.")
        try:
            max_results = int(args.get("max_results", 5) or 5)
        except (TypeError, ValueError):
            max_results = 5
        try:
            results = duckduckgo_search(query, max_results=max_results)
        except Exception as e:
            logger.warning(f"Web arama hatası ('{query[:60]}'): {e}")
            return tr("Web araması başarısız oldu ({e}). Başka bir sorguyla tekrar dene.").format(
                e=truncate_error(e, 200))
        if not results:
            return tr("Sonuç bulunamadı: '{q}'. Farklı kelimelerle tekrar dene.").format(q=query)
        # Token cimriliği: sonuç başına tek satır (başlık + url),
        # snippet yalnızca ilk SONUÇ değil her sonuç için kısaltılmış verilir.
        lines = [tr("'{q}' için {n} sonuç:").format(q=query, n=len(results))]
        for i, r in enumerate(results, 1):
            lines.append(f"{i}. {r['title']}\n   {r['url']}")
            if r["snippet"]:
                snip = r["snippet"]
                if len(snip) > _WEB_SNIPPET_CHARS:
                    snip = snip[:_WEB_SNIPPET_CHARS] + "…"
                lines.append(f"   {snip}")
        return "\n".join(lines)

    def _tool_fetch_web_page(self, args):
        url = (args.get("url") or "").strip()
        if not url:
            return tr("Hata: 'url' parametresi boş.")
        try:
            # Sorgu bağlamı: sayfa metnini sorulan konuya göre kırp (token tasarrufu).
            # Yardımcı modül yoksa (veya hata verirse) düz kırpma kullanılır.
            try:
                from src.tools.webtext import pick_relevant_sections
            except ImportError:
                pick_relevant_sections = None
            text, _ctype = fetch_url_as_text(url)
            if not text.strip():
                return tr("Sayfadan metin çıkarılamadı: {url}").format(url=url)
            query_hint = (args.get("query") or "").strip()
            if query_hint and pick_relevant_sections:
                try:
                    text = pick_relevant_sections(text, query_hint,
                                                  limit=_WEB_FETCH_CHARS)
                except Exception:
                    pass
            if len(text) > _WEB_FETCH_CHARS:
                text = text[:_WEB_FETCH_CHARS] + tr("\n... (sayfa uzun, kesildi)")
            return f"[{url}]:\n{text}"
        except Exception as e:
            logger.warning(f"Sayfa okuma hatası ({url[:80]}): {e}")
            return tr("Sayfa okunamadı ({e}). web_search ile başka bir adres dene.").format(
                e=truncate_error(e, 200))
