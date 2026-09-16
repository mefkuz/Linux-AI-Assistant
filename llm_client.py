import subprocess
import logging
import os
from cli_tools_registry import KNOWN_CLI_TOOLS, build_cli_command

logger = logging.getLogger(__name__)


def _short_err(text, limit=500):
    """HTTP hata gövdesini (HTML/JSON) kullanıcı dostu uzunluğa indirir."""
    text = str(text or "")
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... ({len(text) - limit} karakter kesildi)"


class LLMClient:
    def __init__(self, settings=None, confirm_callback=None, browser_sender=None):
        self.settings = settings
        self.confirm_callback = confirm_callback
        self.browser_sender = browser_sender
        # Son generate_response çağrısında kullanılan araç adları.
        # gui_main'deki [EKRANDA_GOSTER] fallback heuristic'i için.
        self.last_tools_used = []
        # Konuşma geçmişi: [{"role": "user"|"assistant", "content": str}, ...]
        # Sadece ham kullanıcı sorusu + final asistan yanıtı tutulur
        # (tool çağrıları ve enjekte edilen bağlam dahil edilmez).
        self.history = []

    def generate_response(self, system_prompt, user_prompt, context=None, skip_injection=False,
                            use_history=True):
        """
        system_prompt: sistem talimatı
        user_prompt:   ham kullanıcı sorusu
        context:       enjekte edilecek bağlam (ekran/pano/komut çıktısı)
        skip_injection: True ise workspace/güvenlik ekleri atlanır (dikte düzeltici)
        use_history:  False ise geçmiş okunmaz ve yazılmaz (iç analiz/düzeltme çağrıları)
        """
        mode = "local"
        if self.settings:
            mode = self.settings.get("llm_mode", "local")
        use_history = use_history and not skip_injection

        full_prompt = user_prompt
        if context:
            full_prompt = (
                f"KULLANICININ EKRANINDAN VE PANOSUNDAN ALINAN VERİLER:\n{context}\n\n"
                f"KULLANICI SORUSU: {user_prompt}\n\n"
                f"SİSTEM EMRİ: Kullanıcı sana 'ekranda ne görüyorsun', 'bu kod ne' gibi sorular sorarsa, üstte verilen verileri "
                f"sanki kendi gözlerinle ekranda görüyormuşsun gibi değerlendir ve cevapla. ASLA 'göremiyorum' veya 'dosya bulamadım' deme! "
                f"Yukarıdaki metinleri inceleyerek doğrudan kullanıcının sorusuna cevap ver. "
                f"Ayrıca kullanıcının cevabını ekranda görebilmesi için cevabının en sonuna MUTLAKA [EKRANDA_GOSTER] etiketini eklemeyi unutma, eğer bunu yazmazsan kullanıcı senin cevabını asla göremez"
            )

        if not skip_injection:
            workspace = self.settings.get("workspace_dir", "").strip() if self.settings else ""
            if workspace:
                system_prompt += f"\n\n[ÖNEMLİ BİLGİ]: Kullanıcının şu anki aktif çalışma dizini (workspace) şudur: {workspace}. Dosya oluşturma veya okuma işlemlerini kesinlikle bu dizinde yapmalısın."

        # Tool calling aktifken (remote/local): model araçları bilsin ve
        # "ekranı göremiyorum" gibi eski talimatlarla çelişmesin.
        if mode in ("remote", "local") and not skip_injection and self._tool_calling_enabled():
            system_prompt += (
                "\n\n[ARAÇLAR]: Sana fonksiyon araçları (tools) tanımlandı. Kullanıcı ekranındaki, "
                "panosundaki veya dosyalarındaki bir şeyi sorarsa bunu araçlarla OKUYABİLİRSİN: "
                "`read_screen_text` (ekran OCR), `get_clipboard_text` (pano), "
                "`read_file`/`list_directory` (dosyalar), `run_shell_command` (terminal), "
                "`browser_action` (tarayıcı kontrolü). "
                "İhtiyacın olan veriyi önce araçla al, sonra cevapla. "
                "ASLA 'göremiyorum' veya 'erişemiyorum' deme; önce ilgili aracı dene."
            )

        if mode == "cli" and not skip_injection:
            require_confirm = self.settings.get("require_confirm_on_write", True) if self.settings else True
            if require_confirm:
                system_prompt += (
                    "\n\n[GÜVENLİK KURALI]: Sen arka planda (headless) yetkili olarak çalışıyorsun. "
                    "Sistemde dosya SİLME (rm), TAŞIMA (mv), YETKİ DEĞİŞTİRME (chmod/chown) veya "
                    "sistemi etkileyecek TEHLİKELİ HİÇBİR KOMUTU ÇALIŞTIRMA! "
                    "Eğer kullanıcı tehlikeli bir işlem (ör: silme) isterse, İŞLEMİ YAPMA ve sadece kullanıcının hangi komutu çalıştırması gerektiğini söyle."
                )
            else:
                system_prompt += (
                    "\n\n[GÜVENLİK KURALI]: Güvenlik kısıtlamaları DEVRE DIŞI. "
                    "Kullanıcının belirttiği aktif çalışma alanı (workspace) dizininde "
                    "dosya silme (rm), oluşturma ve değiştirme yetkilerine TAMAMEN SAHİPSİN. "
                    "Kullanıcı silme veya değiştirme isterse bunu doğrudan gerçekleştir."
                )

        try:
            if mode == "remote":
                resp_text = self._call_remote(system_prompt, full_prompt,
                                              history=self._recent_history() if use_history else None)
            elif mode == "cli":
                self.last_tools_used = []
                resp_text = self._call_cli(system_prompt, full_prompt,
                                           history=self._recent_history() if use_history else None)
            else:
                resp_text = self._call_local(system_prompt, full_prompt,
                                             history=self._recent_history() if use_history else None)

            import re
            print(f"[DEBUG] Ham API Çıktısı: {repr(resp_text)}", flush=True)
            # Düşünen modellerin (reasoning) <think> bloklarını gizle (Sadece cevabın EN BAŞINDA ise)
            resp_clean = re.sub(r'^\s*<think>.*?</think>', '', resp_text, flags=re.DOTALL)
            # Eğer token sınırına takılıp </think> ile kapanmamış yarım bir blok varsa onu da sil
            resp_clean = re.sub(r'^\s*<think>.*', '', resp_clean, flags=re.DOTALL).strip()

            if not resp_clean:
                return "(Yapay zeka yanıt üretemedi veya sadece düşünce bloğu gönderdi. Terminaldeki [DEBUG] logunu kontrol edin.)"

            # Geçmişe yaz: ham soru + temiz yanıt (bağlam ve tool çağrıları hariç).
            if use_history:
                self._remember(user_prompt, resp_clean)

            return resp_clean
        except Exception as e:
            logger.error(f"LLM hatası ({mode}): {e}")
            return f"[LLM Hatası ({mode})]: {str(e)}"

    # -- konuşma geçmişi (hafıza) ----------------------------

    def _history_limit(self):
        """Geçmişte tutulacak maksimum diyalog turu (user+assistant çifti)."""
        if self.settings:
            try:
                return max(0, min(int(self.settings.get("history_max_turns", 6)), 20))
            except (TypeError, ValueError):
                pass
        return 6

    def _recent_history(self):
        """LLM'e gönderilecek geçmiş dilimi (kopya; son N tur)."""
        limit = self._history_limit()
        if limit <= 0:
            return []
        return list(self.history[-(limit * 2):])

    def _remember(self, user_prompt, assistant_reply):
        """Bir turu geçmişe ekler, limiti aşan eski turları atar."""
        limit = self._history_limit()
        self.history.append({"role": "user", "content": user_prompt})
        self.history.append({"role": "assistant", "content": assistant_reply})
        if limit <= 0:
            self.history.clear()
        elif len(self.history) > limit * 2:
            del self.history[:-(limit * 2)]

    def clear_history(self):
        """Konuşma geçmişini sıfırlar (örn. tray menüsünden)."""
        self.history = []

    def _call_local(self, system_prompt, user_prompt, history=None):
        import requests
        url   = self.settings.get("local_api_url",  "http://localhost:8080/v1/chat/completions") if self.settings else "http://localhost:8080/v1/chat/completions"
        model = self.settings.get("local_model",    "local-model") if self.settings else "local-model"
        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(history or [])
        messages.append({"role": "user", "content": user_prompt})
        if self._tool_calling_enabled():
            return self._chat_with_tools(url, None, model, messages, timeout=60)
        self.last_tools_used = []
        resp = requests.post(url, json={
            "model": model,
            "messages": messages,
            "max_tokens": 4096
        }, timeout=60)

        if not resp.ok:
            raise RuntimeError(f"HTTP {resp.status_code}: {_short_err(resp.text)}")

        return resp.json()["choices"][0]["message"]["content"]

    def _call_remote(self, system_prompt, user_prompt, history=None):
        import requests
        if not self.settings:
            raise ValueError("Uzak mod için ayarlar yüklenmedi.")
        api_key = self.settings.get("remote_api_key", "")
        url     = self.settings.get("remote_api_url",  "https://api.openai.com/v1/chat/completions")
        model   = self.settings.get("remote_model",    "gpt-4")
        if not api_key:
            raise ValueError("REMOTE_LLM_API_KEY boş. Ayarlardan doldurun.")
        import requests
        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(history or [])
        messages.append({"role": "user", "content": user_prompt})
        if self._tool_calling_enabled():
            headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
            return self._chat_with_tools(url, headers, model, messages, timeout=30)
        self.last_tools_used = []
        resp = requests.post(url,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": messages,
                "max_tokens": 4096
            },
            timeout=30
        )
        if not resp.ok:
            raise RuntimeError(f"HTTP {resp.status_code}: {_short_err(resp.text)}")
        return resp.json()["choices"][0]["message"]["content"]

    def _tool_calling_enabled(self):
        if self.settings:
            return bool(self.settings.get("enable_tool_calling", True))
        return False
    def _tool_max_iterations(self):
        if self.settings:
            try:
                return max(1, min(int(self.settings.get("tool_max_iterations", 5)), 10))
            except (TypeError, ValueError):
                pass
        return 5

    def _chat_with_tools(self, url, headers, model, messages, timeout=30):
        """
        OpenAI-formatında agentic döngü: model tool_calls döndürdükçe
        ToolExecutor ile çalıştırır, sonuçları role:"tool" olarak geri verir.
        """
        import requests
        from ai_tools import get_openai_tools, ToolExecutor

        executor = ToolExecutor(
            settings=self.settings,
            confirm_callback=self.confirm_callback,
            browser_sender=self.browser_sender,
        )
        tools = get_openai_tools()
        max_iter = self._tool_max_iterations()
        tools_supported = True
        self.last_tools_used = []

        for _ in range(max_iter):
            payload = {"model": model, "messages": messages, "max_tokens": 4096}
            if tools_supported:
                payload["tools"] = tools
                payload["tool_choice"] = "auto"

            resp = requests.post(url, headers=headers, json=payload, timeout=timeout)

            # tools parametresini desteklemeyen sunucu → tools'suz tek deneme
            if not resp.ok and tools_supported and resp.status_code == 400 and "tool" in resp.text.lower():
                logger.warning("Sunucu 'tools' parametresini reddetti, tools'suz devam ediliyor.")
                tools_supported = False
                continue
            if not resp.ok:
                raise RuntimeError(f"HTTP {resp.status_code}: {_short_err(resp.text)}")

            message = resp.json()["choices"][0]["message"]
            tool_calls = message.get("tool_calls") or []

            if not tool_calls:
                return message.get("content") or ""

            # Asistanın tool çağrı mesajını geçmişe ekle (OpenAI formatı gereği)
            messages.append({
                "role": "assistant",
                "content": message.get("content"),
                "tool_calls": tool_calls,
            })

            for call in tool_calls:
                func = call.get("function", {}) or {}
                name = func.get("name", "")
                arguments = func.get("arguments", "{}") or "{}"
                logger.info(f"Tool çağrısı: {name} {str(arguments)[:200]}")
                print(f"[TOOL] {name} çağrılıyor...", flush=True)
                result = executor.execute_tool(name, arguments)
                print(f"[TOOL] {name} tamamlandı ({len(result)} karakter)", flush=True)
                if name and name not in self.last_tools_used:
                    self.last_tools_used.append(name)
                messages.append({
                    "role": "tool",
                    "tool_call_id": call.get("id", ""),
                    "name": name,
                    "content": result,
                })

        logger.warning(f"Tool döngüsü üst sınıra ulaştı ({max_iter}), son yanıt döndürülüyor.")
        # Üst sınıra ulaşıldıysa son bir tools'suz çağrı ile özet yanıt al
        resp = requests.post(url, headers=headers, json={
            "model": model, "messages": messages, "max_tokens": 4096
        }, timeout=timeout)
        if not resp.ok:
            raise RuntimeError(f"HTTP {resp.status_code}: {_short_err(resp.text)}")
        content = resp.json()["choices"][0]["message"].get("content") or ""
        if not content.strip() and self.last_tools_used:
            # Model özet üretemediyse kullanıcıya boş ekran gösterme:
            # yapılan işleri listele.
            content = (
                f"İşlem tamamlandı (kullanılan araçlar: {', '.join(self.last_tools_used)}). "
                "Detay için Loglar klasörüne bakabilirsiniz."
            )
        return content

    def _call_cli(self, system_prompt, user_prompt, history=None):
        """
        Registry'den seçilen CLI aracını kullanarak prompt'u stdin üzerinden gönderir.
        Araç kendi yöntemini (stdin/arg) ve model formatını registry'den alır.
        """
        if not self.settings:
            raise ValueError("CLI modu için ayarlar yüklenmedi.")

        tool_key   = self.settings.get("llm_cli_tool_key", "agy (Antigravity)")
        model      = self.settings.get("llm_cli_model", "").strip() or None
        extra_args = self.settings.get("llm_cli_extra_args", "").strip()

        # Registry'den komutu oluştur
        cfg = KNOWN_CLI_TOOLS.get(tool_key)
        if not cfg:
            # Bilinmeyen araç — binary olarak doğrudan dene
            binary = tool_key.split()[0]
            cmd = [binary]
            input_method = "stdin"
        else:
            cmd = build_cli_command(tool_key, model)
            input_method = cfg["input_method"]

        # Ek argümanlar varsa ekle
        if extra_args:
            cmd += extra_args.split()

        # Prompt'u hazırla (CLI araçları tek metin alır, geçmiş düz metne çevrilir)
        full_input = system_prompt
        for msg in (history or []):
            role = "Kullanıcı" if msg.get("role") == "user" else "Asistan"
            full_input += f"\n\n[{role}]: {msg.get('content', '')}"
        full_input += f"\n\n[Kullanıcı]: {user_prompt}\n\n[Asistan]:"

        logger.info(f"CLI LLM: {cmd} (input_method={input_method})")

        # Çalışma dizinini belirle
        workspace = self.settings.get("workspace_dir", "").strip() if self.settings else ""
        cwd = workspace if workspace and os.path.isdir(workspace) else os.path.expanduser("~")

        if input_method == "arg":
            # tgpt gibi araçlar prompt'u argüman olarak alır
            cmd.append(full_input)
            result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    text=True, timeout=120, cwd=cwd)
        else:
            # stdin (agy, ollama, llm, aichat vb.)
            result = subprocess.run(cmd, input=full_input,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    text=True, timeout=120, cwd=cwd)

        if result.returncode != 0:
            logger.warning(f"CLI stderr: {result.stderr[:300]}")

        output = result.stdout.strip() or result.stderr.strip()
        if not output:
            raise RuntimeError(
                f"'{cmd[0]}' aracından çıktı alınamadı. "
                "Aracın doğru kurulduğunu ve PATH'te olduğunu kontrol edin."
            )
        return output
