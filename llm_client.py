import subprocess
import logging
import os
from cli_tools_registry import KNOWN_CLI_TOOLS, build_cli_command

logger = logging.getLogger(__name__)


class LLMClient:
    def __init__(self, settings=None):
        self.settings = settings

    def generate_response(self, system_prompt, user_prompt, context=None, skip_injection=False):
        mode = "local"
        if self.settings:
            mode = self.settings.get("llm_mode", "local")

        full_prompt = user_prompt
        if context:
            full_prompt = (
                f"KULLANICININ EKRANINDAN VE PANOSUNDAN ALINAN VERİLER:\n{context}\n\n"
                f"KULLANICI SORUSU: {user_prompt}\n\n"
                f"SİSTEM EMRİ: Kullanıcı sana 'ekranda ne görüyorsun', 'bu kod ne' gibi sorular sorarsa, üstte verilen verileri "
                f"sanki kendi gözlerinle ekranda görüyormuşsun gibi değerlendir ve cevapla. ASLA 'göremiyorum' veya 'dosya bulamadım' deme! "
                f"Yukarıdaki metinleri inceleyerek doğrudan kullanıcının sorusuna cevap ver. "
                f"Ayrıca kullanıcının cevabını ekranda görebilmesi için cevabının en sonuna MUTLAKA [EKRANDA_GOSTER] etiketini eklemeyi unutma!"
            )

        if not skip_injection:
            workspace = self.settings.get("workspace_dir", "").strip() if self.settings else ""
            if workspace:
                system_prompt += f"\n\n[ÖNEMLİ BİLGİ]: Kullanıcının şu anki aktif çalışma dizini (workspace) şudur: {workspace}. Dosya oluşturma veya okuma işlemlerini kesinlikle bu dizinde yapmalısın."

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
                resp_text = self._call_remote(system_prompt, full_prompt)
            elif mode == "cli":
                resp_text = self._call_cli(system_prompt, full_prompt)
            else:
                resp_text = self._call_local(system_prompt, full_prompt)
                
            import re
            print(f"[DEBUG] Ham API Çıktısı: {repr(resp_text)}", flush=True)
            # Düşünen modellerin (reasoning) <think> bloklarını gizle (Sadece cevabın EN BAŞINDA ise)
            resp_clean = re.sub(r'^\s*<think>.*?</think>', '', resp_text, flags=re.DOTALL)
            # Eğer token sınırına takılıp </think> ile kapanmamış yarım bir blok varsa onu da sil
            resp_clean = re.sub(r'^\s*<think>.*', '', resp_clean, flags=re.DOTALL).strip()
            
            if not resp_clean:
                return "(Yapay zeka yanıt üretemedi veya sadece düşünce bloğu gönderdi. Terminaldeki [DEBUG] logunu kontrol edin.)"
            
            return resp_clean
        except Exception as e:
            logger.error(f"LLM hatası ({mode}): {e}")
            return f"[LLM Hatası ({mode})]: {str(e)}"

    def _call_local(self, system_prompt, user_prompt):
        import requests
        url   = self.settings.get("local_api_url",  "http://localhost:8080/v1/chat/completions") if self.settings else "http://localhost:8080/v1/chat/completions"
        model = self.settings.get("local_model",    "local-model") if self.settings else "local-model"
        resp = requests.post(url, json={
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_prompt},
            ],
            "max_tokens": 4096
        }, timeout=60)
        
        if not resp.ok:
            raise RuntimeError(f"HTTP {resp.status_code}: {resp.text}")
            
        return resp.json()["choices"][0]["message"]["content"]

    def _call_remote(self, system_prompt, user_prompt):
        import requests
        if not self.settings:
            raise ValueError("Uzak mod için ayarlar yüklenmedi.")
        api_key = self.settings.get("remote_api_key", "")
        url     = self.settings.get("remote_api_url",  "https://api.openai.com/v1/chat/completions")
        model   = self.settings.get("remote_model",    "gpt-4")
        if not api_key:
            raise ValueError("REMOTE_LLM_API_KEY boş. Ayarlardan doldurun.")
        import requests
        resp = requests.post(url,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": user_prompt},
                ],
                "max_tokens": 4096
            },
            timeout=30
        )
        if not resp.ok:
            raise RuntimeError(f"HTTP {resp.status_code}: {resp.text}")
        return resp.json()["choices"][0]["message"]["content"]

    def _call_cli(self, system_prompt, user_prompt):
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

        # Prompt'u hazırla
        full_input = f"{system_prompt}\n\n{user_prompt}"

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
