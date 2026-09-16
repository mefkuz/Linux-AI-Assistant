"""
OpenAI-formatında Tool Calling desteği için araç tanımları ve çalıştırıcı.

Kullanım:
    from ai_tools import get_openai_tools, ToolExecutor

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

from security import SecurityManager

logger = logging.getLogger(__name__)

# Salt-okuma araçları: asla onay gerektirmez (side-effect yok, hassas veri yok)
READ_ONLY_TOOLS = {
    "read_file",
    "list_directory",
    "get_active_window_context",
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
    return ("Komut çalıştırılsın mı?", "Şu komut çalıştırılacak:", f"$ {short}")


def _summarize_write(args):
    path = (args.get("path") or "").strip()
    size = len(args.get("content", ""))
    return ("Dosya yazılsın mı?", f"Şu dosyaya yazılacak ({size} karakter):", path)


def _summarize_append(args):
    path = (args.get("path") or "").strip()
    size = len(args.get("content", ""))
    return ("Dosyaya eklensin mi?", f"Şu dosyanın sonuna eklenecek ({size} karakter):", path)


def _summarize_browser(args):
    action = (args.get("action") or "").strip()
    labels = {
        "close_tab": "açık sekmeyi kapatmak",
        "scroll_down": "sayfayı aşağı kaydırmak",
        "scroll_up": "sayfayı yukarı kaydırmak",
        "fill_form": "forma metin yazmak",
        "new_tab": "yeni sekme açmak",
    }
    what = labels.get(action, f"'{action}' işlemini yapmak")
    detail = None
    if args.get("text"):
        detail = f"Yazılacak metin: {(args['text'][:100] + '…') if len(args['text']) > 100 else args['text']}"
    elif args.get("url"):
        detail = f"Adres: {args['url']}"
    return ("Tarayıcıda işlem yapılsın mı?", f"Yapay zeka {what} istiyor.", detail)


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
    return text[:limit] + f"\n... (hata mesajı çok uzun, {len(text) - limit} karakter kesildi)"


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
                    "Linux terminal komutu çalıştırır: listeleme, arama, git, "
                    "paket bilgisi gibi OKUMA AMAÇLI işler için kullan. "
                    "Dosya OLUŞTURMA/DÜZENLEME için bu aracı KULLANMA "
                    "(echo/cat > heredoc YASAK) — yerine write_file/append_file kullan. "
                    "Çıktı stdout+stderr ve exit code olarak döner."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "description": "Çalıştırılacak shell komutu, örn: 'ls -la'",
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
                "description": "Bir metin dosyasının içeriğini okur. Dizin dışına taşan yollar reddedilir.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Okunacak dosyanın yolu (göreli veya mutlak)."},
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
                    "Bir metin dosyası oluşturur veya üzerine yazar. "
                    "Dosya yazma işlerinin TEK yolu budur (shell'e echo ile yazma). "
                    "Sadece çalışma alanı (workspace) dizini içine yazılabilir."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Yazılacak dosyanın yolu."},
                        "content": {"type": "string", "description": "Dosyaya yazılacak içerik."},
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
                    "Bir metin dosyasının SONUNA içerik ekler. Dosya yoksa oluşturur. "
                    "Mevcut bir dosyaya satır eklemek için write_file yerine bunu kullan "
                    "(dosyanın tamamını yeniden yazmana gerek yok). "
                    "Sadece çalışma alanı (workspace) dizini içine yazılabilir."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Eklenecek dosyanın yolu."},
                        "content": {"type": "string", "description": "Dosyanın sonuna eklenecek içerik."},
                    },
                    "required": ["path", "content"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "list_directory",
                "description": "Bir klasörün içeriğini listeler.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Listelenecek klasör. Boş bırakılırsa çalışma alanı kullanılır.",
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
                "description": "Kullanıcının panosundaki (kopyalanmış) metni okur.",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "read_screen_text",
                "description": (
                    "Kullanıcının ekranının görüntüsünü çekip OCR ile metne çevirir. "
                    "Kullanıcı 'ekranda ne var', 'ekrandaki yazı' gibi şeyler sorduğunda kullan."
                ),
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_active_window_context",
                "description": (
                    "O anda açık olan pencere/medya oynatıcı gibi bağlamları listeler "
                    "(pencere başlığı, çalan şarkı/video bilgisi)."
                ),
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "browser_action",
                "description": (
                    "Kullanıcının tarayıcısını kontrol eder: sekme kapatma, sayfa kaydırma, "
                    "forma metin yazma. Tarayıcı eklentisi kurulu olmalıdır."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "description": "Eylem adı: close_tab, scroll_down, scroll_up, fill_form, new_tab",
                        },
                        "text": {
                            "type": "string",
                            "description": "fill_form eylemi için forma yazılacak metin.",
                        },
                        "url": {
                            "type": "string",
                            "description": "new_tab eylemi için açılacak adres.",
                        },
                    },
                    "required": ["action"],
                },
            },
        },
    ]


# ──────────────────────────────────────────────────────────
#  Yardımcılar (gui_main'den refactor edildi)
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

    def _resolve_inside_workspace(self, path):
        """Yolu workspace içine sabitle; dışarı taşma varsa ValueError."""
        ws = self._workspace()
        abs_path = path if os.path.isabs(path) else os.path.join(ws, path)
        real = os.path.realpath(abs_path)
        if real != ws and not real.startswith(ws + os.sep):
            raise ValueError(
                f"Güvenlik: '{path}' çalışma alanı dışına taşıyor (workspace: {ws})."
            )
        return real

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
            return f"Araç argümanı JSON olarak çözümlenemedi: {e}"

        handler = getattr(self, f"_tool_{name}", None)
        if handler is None:
            self._log_tool_call(name, args, "bilinmeyen-araç")
            return f"Bilinmeyen araç: '{name}'."

        # Onay kontrolü (üç katman):
        # 1. Hassas okuma (ekran/pano): auto_allow_clipboard kapalıyken her seferinde sor.
        if name in SENSITIVE_READ_TOOLS:
            auto_allow = self.settings.get("auto_allow_clipboard", False) if self.settings else False
            if not auto_allow:
                title, question = SENSITIVE_READ_TOOLS[name]
                summary = {"title": title, "question": question}
                if not self.security.ask_confirmation(summary):
                    self._log_tool_call(name, args, "kullanıcı-reddetti")
                    return (
                        "Kullanıcı bu okuma işlemine izin vermedi. "
                        "Ekran/pano içeriğini görmeden, genel bilgiyle cevapla."
                    )
        # 2. Yazma/çalıştırma: require_confirm_on_tool ayara bağlı.
        elif name not in READ_ONLY_TOOLS and self._require_confirm():
            summarizer = TOOL_SUMMARIZERS.get(name)
            if summarizer:
                title, question, detail = summarizer(args)
                summary = {"title": title, "question": question, "detail": detail}
            else:
                summary = f"Araç: {name}\nArgüman: {json.dumps(args, ensure_ascii=False)[:500]}"
            if not self.security.ask_confirmation(summary):
                self._log_tool_call(name, args, "kullanıcı-reddetti")
                return "Kullanıcı bu işlemi reddetti. İşlem yapılmadı."

        try:
            result = handler(args)
            status = "tamam"
        except Exception as e:
            logger.error(f"Araç hatası ({name}): {e}")
            result = f"Araç çalıştırılırken hata ({name}): {e}"
            status = "hata"
        finally:
            self._log_tool_call(name, args, status)

        result = str(result)
        if len(result) > MAX_TOOL_OUTPUT_CHARS:
            result = result[:MAX_TOOL_OUTPUT_CHARS] + "\n... (çıktı çok uzun olduğu için kesildi)"
        return result

    def _log_tool_call(self, name, arguments, status):
        """Her araç çağrısını Loglar/araçlar-YYYY-MM-DD.md dosyasına tek satır yazar."""
        try:
            log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Loglar")
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
            return "Hata: 'command' parametresi boş."
        # CLIExecutor kendi güvenlik katmanını uygular (çifte koruma).
        from cli_executor import CLIExecutor
        executor = CLIExecutor(settings=self.settings,
                               confirm_callback=self.security.confirm_callback)
        res = executor.execute(command)
        out = f"Exit Code: {res['exit_code']}\n"
        if res["stdout"]:
            out += f"STDOUT:\n{res['stdout']}\n"
        if res["stderr"]:
            out += f"STDERR:\n{res['stderr']}"
        if res["status"] == "cancelled":
            out += "\nNot: Komut kullanıcı tarafından iptal edildi."
        return out.strip() or "(çıktı yok)"

    def _tool_read_file(self, args):
        path = (args.get("path") or "").strip()
        if not path:
            return "Hata: 'path' parametresi boş."
        real = self._resolve_inside_workspace(path)
        if not os.path.isfile(real):
            return f"Dosya bulunamadı: '{path}'"
        try:
            with open(real, "r", encoding="utf-8", errors="replace") as f:
                content = f.read(MAX_TOOL_OUTPUT_CHARS + 1)
        except OSError as e:
            return f"Dosya okunamadı: {e}"
        if len(content) > MAX_TOOL_OUTPUT_CHARS:
            content = content[:MAX_TOOL_OUTPUT_CHARS] + "\n... (dosya çok uzun, kesildi)"
        return f"[{real}]:\n{content}"

    def _tool_write_file(self, args):
        path = (args.get("path") or "").strip()
        content = args.get("content", "")
        if not path:
            return "Hata: 'path' parametresi boş."
        real = self._resolve_inside_workspace(path)
        parent = os.path.dirname(real)
        if parent and not os.path.isdir(parent):
            try:
                os.makedirs(parent, exist_ok=True)
            except OSError as e:
                return f"Klasör oluşturulamadı: {e}"
        try:
            with open(real, "w", encoding="utf-8") as f:
                f.write(content)
        except OSError as e:
            return f"Dosya yazılamadı: {e}"
        return f"Dosya yazıldı: {real} ({len(content)} karakter)"

    def _tool_append_file(self, args):
        path = (args.get("path") or "").strip()
        content = args.get("content", "")
        if not path:
            return "Hata: 'path' parametresi boş."
        real = self._resolve_inside_workspace(path)
        existed = os.path.isfile(real)
        parent = os.path.dirname(real)
        if parent and not os.path.isdir(parent):
            try:
                os.makedirs(parent, exist_ok=True)
            except OSError as e:
                return f"Klasör oluşturulamadı: {e}"
        try:
            with open(real, "a", encoding="utf-8") as f:
                f.write(content)
        except OSError as e:
            return f"Dosyaya eklenemedi: {e}"
        what = "eklendi" if existed else "oluşturulup yazıldı"
        return f"Dosyaya {what}: {real} (+{len(content)} karakter)"

    def _tool_list_directory(self, args):
        path = (args.get("path") or "").strip()
        real = self._resolve_inside_workspace(path) if path else self._workspace()
        if not os.path.isdir(real):
            return f"Klasör bulunamadı: '{path or real}'"
        try:
            entries = sorted(os.listdir(real))
        except OSError as e:
            return f"Klasör listelenemedi: {e}"
        lines = []
        for e in entries[:200]:
            full = os.path.join(real, e)
            lines.append(f"[D] {e}" if os.path.isdir(full) else f"[F] {e}")
        if len(entries) > 200:
            lines.append(f"... (+{len(entries) - 200} öğe daha)")
        return f"[{real}] ({len(entries)} öğe):\n" + "\n".join(lines)

    def _tool_get_clipboard_text(self, args):
        # Tool yolunda pano "şimdi" okunur (gui_main'deki önbellek kullanılmaz).
        text = read_clipboard_subprocess()
        if not text.strip():
            return "(Pano boş veya okunamadı)"
        return f"[Panodaki Metin]:\n{text}"

    def _tool_read_screen_text(self, args):
        text = read_screen_via_ocr()
        if not text.strip():
            return "(Ekrandan metin okunamadı)"
        return f"[Ekrandaki Metin (OCR)]:\n{text}"

    def _tool_get_active_window_context(self, args):
        try:
            from context_helper import get_active_contexts
        except ImportError as e:
            return f"Bağlam yardımcısı yüklenemedi: {e}"
        contexts = get_active_contexts()
        if not contexts:
            return "(Aktif bağlam bulunamadı)"
        lines = []
        for c in contexts:
            detail = f" — {c['detail']}" if c.get("detail") else ""
            lines.append(f"{c.get('icon', '')} {c.get('label', c.get('type', ''))}: {c.get('title', '')}{detail}")
        return "\n".join(lines)

    def _tool_browser_action(self, args):
        action = (args.get("action") or "").strip()
        if not action:
            return "Hata: 'action' parametresi boş."
        if self.browser_sender is None:
            return (
                "Tarayıcı bağlantısı yok (eklenti sunucusu çalışmıyor). "
                "Kullanıcıya tarayıcı eklentisini kurmasını hatırlat."
            )
        command = {"action": action}
        if args.get("text"):
            command.setdefault("params", {})["text"] = args["text"]
        if args.get("url"):
            command.setdefault("params", {})["url"] = args["url"]
        try:
            self.browser_sender(command)
        except Exception as e:
            return f"Tarayıcı komutu gönderilemedi: {e}"
        return f"Tarayıcı komutu gönderildi: {action}"
