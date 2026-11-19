import subprocess
import logging
import os
from src.llm.cli_registry import KNOWN_CLI_TOOLS, build_cli_command
from src.core.i18n import tr
from src.tools.questions import (UserQuestionCancelled, question_was_cancelled, clarification_question,
                                 visual_question_request, VisualAnswer, drain_answer_images, api_answer_content)

logger = logging.getLogger(__name__)


def _short_err(text, limit=500):
    """HTTP hata gövdesini (HTML/JSON) kullanıcı dostu uzunluğa indirir."""
    text = str(text or "")
    import re
    text = re.sub(r'data:image/[^;,\s]+;base64,[A-Za-z0-9+/=]+', '[image omitted]', text)
    if len(text) <= limit:
        return text
    return text[:limit] + tr("\n... ({n} karakter kesildi)").format(n=len(text) - limit)


class LLMClient:
    def __init__(self, settings=None, confirm_callback=None, browser_sender=None, question_callback=None):
        self.settings = settings
        self.confirm_callback = confirm_callback
        self.browser_sender = browser_sender
        self.question_callback = question_callback
        # Son generate_response çağrısında kullanılan araç adları.
        # src/gui/app.py'deki [EKRANDA_GOSTER] fallback heuristic'i için.
        self.last_tools_used = []
        # Konuşma geçmişi: [{"role": "user"|"assistant", "content": str}, ...]
        # Kullanıcı sorusu, varsa açıklama yanıtları ve final asistan yanıtı
        # tutulur; diğer tool çağrıları ve enjekte edilen bağlam tutulmaz.
        self.history = []
        self.last_error = None
        self._clarifications = []
        self.last_cancelled = False

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
        self.last_error = None
        self._clarifications = []
        self.last_cancelled = False
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
            system_prompt += (
                "\n\n[İSTEK SINIRI]: Yalnızca kullanıcının en son isteğini yerine getir. "
                "Önceki konuşmalar bağlamdır; tamamlanmış işlemleri yeni bir talimat gibi "
                "tekrar çalıştırma. Kullanıcı önceki bir tercihi/sonucu hatırlamanı istiyorsa "
                "geçmişten yanıtla; yeni bir işlem istemedikçe dosya yazma veya komut çalıştırma."
            )
            workspace = self.settings.get("workspace_dir", "").strip() if self.settings else ""
            if workspace:
                system_prompt += f"\n\n[ÖNEMLİ BİLGİ]: Kullanıcının şu anki aktif çalışma dizini (workspace) şudur: {workspace}. Dosya oluşturma veya okuma işlemlerini kesinlikle bu dizinde yapmalısın."

        # Tool calling aktifken (remote/local): model araçları bilsin ve
        # "ekranı göremiyorum" gibi eski talimatlarla çelişmesin.
        if self.supports_app_tools() and not skip_injection and self._tool_calling_enabled():
            system_prompt += (
                "\n\n[ARAÇLAR]: Fonksiyon araçların var. Gereken veriyi önce araçla al, "
                "sonra cevapla — ASLA 'göremiyorum/erişemiyorum' deme, önce ilgili aracı dene: "
                "`read_screen_text` (ekran OCR), `get_clipboard_text` (pano), "
                "`read_file`/`list_directory` (dosya), `run_shell_command` (terminal), "
                "`browser_action` (tarayıcı ve arayüz kontrolü: sekme, form, tıklama, tuş), "
                "`web_search`+`fetch_web_page` (İNTERNET: "
                "güncel bilgi gerektiğinde MUTLAKA bunları kullan; shell ile web kazıma YASAK)."
                " Kullanıcının isteğini gerçekleştirmek için gerekli bilgi eksik veya belirsizse "
                "`ask_user` ile sor ve yanıtı kullanarak devam et. Bilgi yeterliyse doğrudan ilerle; "
                "rutin onay, durum bildirimi veya isteğe bağlı sohbet için soru penceresi açma. "
                "Önce mevcut bağlamı ve önceki yanıtları kullan; yalnızca gerekli eksik bilgiyi sor. "
                "Seçenekleri ancak seçimi kolaylaştırıyorsa, görseli ancak görsel belirsizliği "
                "çözmek gerekiyorsa ekle. Kullanıcıdan gereksiz çizim veya görsel isteme. "
                "Geçmişte zaten verilmiş bilgiyi "
                "tekrar sorma. Kullanıcı iptal ederse bağımlı işlemleri yapma; tahmin etme."
                " AÇIKLAMA SORUSU normal yanıt değildir: 'Neyi kastettiniz?', 'Hangisini "
                "istiyorsunuz?' gibi soruları son yanıt olarak yazma; MUTLAKA ask_user çağır."
                " Soruna image_paths ile yerel görsel veya screenshot=true ile ekran görüntüsü ekleyebilirsin. "
                "Görsele işaret ekleme; kullanıcı yalnızca senin eklediğin görseli işaretleyebilir. "
                "Yalnızca işaretleme sorusu soracaksan önce read_screen_text çağırma; "
                "doğrudan ask_user screenshot=true kullan. OCR ve soru eki aynı ekran karesini kullanır. "
                "Kullanıcının mavi işaretleri referanstır, otomatik tıklama yetkisi değildir."
            )

        if mode == "cli" and not self._is_codex() and not skip_injection:
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
                                              history=self._recent_history() if use_history else None,
                                              allow_tools=not skip_injection)
            elif mode == "cli":
                self.last_tools_used = []
                resp_text = self._call_cli(system_prompt, full_prompt,
                                           history=self._recent_history() if use_history else None,
                                           allow_tools=not skip_injection)
            else:
                resp_text = self._call_local(system_prompt, full_prompt,
                                             history=self._recent_history() if use_history else None,
                                             allow_tools=not skip_injection)

            import re
            print(f"[DEBUG] Ham API Çıktısı: {repr(resp_text)}", flush=True)
            # Düşünen modellerin (reasoning) <think> bloklarını gizle (Sadece cevabın EN BAŞINDA ise)
            resp_clean = re.sub(r'^\s*<think>.*?</think>', '', resp_text, flags=re.DOTALL)
            # Eğer token sınırına takılıp </think> ile kapanmamış yarım bir blok varsa onu da sil
            resp_clean = re.sub(r'^\s*<think>.*', '', resp_clean, flags=re.DOTALL).strip()

            if not resp_clean:
                return tr("(Yapay zeka yanıt üretemedi veya sadece düşünce bloğu gönderdi. Terminaldeki [DEBUG] logunu kontrol edin.)")

            # Geçmişe yaz: ham soru + temiz yanıt (bağlam ve tool çağrıları hariç).
            if use_history:
                self._remember(user_prompt, resp_clean)

            return resp_clean
        except UserQuestionCancelled:
            self.last_cancelled = True
            return tr("Yanıt verilmedi; işlem durduruldu.") + " [EKRANDA_GOSTER]"
        except Exception as e:
            self.last_error = str(e)
            logger.error(f"LLM hatası ({mode}): {e}")
            return tr("[LLM Hatası ({mode})]: {e}").format(mode=mode, e=str(e))

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
        if self._clarifications:
            import json
            user_prompt += "\n\n[Kullanıcının açıklama yanıtları]:\n" + json.dumps(
                self._clarifications, ensure_ascii=False)
        self.history.append({"role": "user", "content": user_prompt})
        self.history.append({"role": "assistant", "content": assistant_reply})
        if limit <= 0:
            self.history.clear()
        elif len(self.history) > limit * 2:
            del self.history[:-(limit * 2)]

    def clear_history(self):
        """Konuşma geçmişini sıfırlar (örn. tray menüsünden)."""
        self.history = []
        self._clarifications = []

    def _ask_user(self, request):
        from src.tools.questions import ask_in_terminal
        answer = (self.question_callback or ask_in_terminal)(request)
        text = answer
        if isinstance(answer, VisualAnswer):
            from src.tools.image_annotations import prepare_visual_answer
            text, _ = prepare_visual_answer(answer)
        if isinstance(text, str) and text.strip():
            self._clarifications.append({"question": request["question"], "answer": text.strip()})
        return answer

    def _call_local(self, system_prompt, user_prompt, history=None, allow_tools=True):
        import requests
        url   = self.settings.get("local_api_url",  "http://localhost:8080/v1/chat/completions") if self.settings else "http://localhost:8080/v1/chat/completions"
        model = self.settings.get("local_model",    "local-model") if self.settings else "local-model"
        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(history or [])
        messages.append({"role": "user", "content": user_prompt})
        if allow_tools and self._tool_calling_enabled():
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

    def _call_remote(self, system_prompt, user_prompt, history=None, allow_tools=True):
        import requests
        if not self.settings:
            raise ValueError(tr("Uzak mod için ayarlar yüklenmedi."))
        api_key = self.settings.get("remote_api_key", "")
        url     = self.settings.get("remote_api_url",  "https://api.openai.com/v1/chat/completions")
        model   = self.settings.get("remote_model",    "gpt-4")
        if not api_key:
            raise ValueError(tr("REMOTE_LLM_API_KEY boş. Ayarlardan doldurun."))
        import requests
        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(history or [])
        messages.append({"role": "user", "content": user_prompt})
        if allow_tools and self._tool_calling_enabled():
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

    def _is_codex(self):
        return bool(self.settings and self.settings.get("llm_cli_tool_key", "") == "codex (OpenAI)")

    def supports_app_tools(self):
        mode = self.settings.get("llm_mode", "local") if self.settings else "local"
        return mode in ("remote", "local") or (mode == "cli" and self._is_codex())
    def _tool_max_iterations(self):
        if self.settings:
            try:
                return max(1, min(int(self.settings.get("tool_max_iterations", 8)), 10))
            except (TypeError, ValueError):
                pass
        return 5

    def _chat_with_tools(self, url, headers, model, messages, timeout=30):
        """
        OpenAI-formatında agentic döngü: model tool_calls döndürdükçe
        ToolExecutor ile çalıştırır, sonuçları role:"tool" olarak geri verir.
        """
        import requests
        from src.tools.executor import get_openai_tools, ToolExecutor

        executor = ToolExecutor(
            settings=self.settings,
            confirm_callback=self.confirm_callback,
            browser_sender=self.browser_sender,
            question_callback=self._ask_user,
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

            if not resp.ok and resp.status_code in (400, 422) and any(
                    isinstance(m.get('content'), list) and any(p.get('type') == 'image_url' for p in m['content'])
                    for m in messages) and any(word in resp.text.lower() for word in ('image', 'vision', 'multimodal')):
                for m in messages:
                    if isinstance(m.get('content'), list):
                        m['content'] = '\n'.join(p.get('text', '') for p in m['content'] if p.get('type') == 'text')
                messages.append({'role': 'user', 'content': 'Bu model görseli kabul etmedi. İşaretli bölgelerin OCR metni ve konumlarıyla devam et; görseli gördüğünü varsayma.'})
                continue
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
                content = message.get("content") or ""
                question = clarification_question(content)
                if question:
                    result = executor.execute_tool("ask_user", visual_question_request(question))
                    if "ask_user" not in self.last_tools_used:
                        self.last_tools_used.append("ask_user")
                    if question_was_cancelled("ask_user", result):
                        raise UserQuestionCancelled()
                    import json
                    answer = json.loads(result)["answer"]
                    messages.extend([
                        {"role": "assistant", "content": question},
                        {"role": "user", "content": api_answer_content("Açıklama sorusuna yanıtım: " + answer, drain_answer_images(executor))},
                    ])
                    continue
                return content

            # Asistanın tool çağrı mesajını geçmişe ekle (OpenAI formatı gereği)
            messages.append({
                "role": "assistant",
                "content": message.get("content"),
                "tool_calls": tool_calls,
            })

            has_question = any(c.get("function", {}).get("name") == "ask_user" for c in tool_calls)
            answer_images = []
            # Model-prepared actions in a question's batch cannot yet reflect
            # the user's answer. Ask first and have the model regenerate those
            # actions with the new information before anything is executed.
            for call in sorted(tool_calls, key=lambda c: c.get("function", {}).get("name") != "ask_user"):
                func = call.get("function", {}) or {}
                name = func.get("name", "")
                arguments = func.get("arguments", "{}") or "{}"
                if has_question and name != "ask_user":
                    messages.append({"role": "tool", "tool_call_id": call.get("id", ""),
                                     "name": name, "content": (
                                         "Action deferred. Read the user's clarification answer and "
                                         "call this tool again with arguments based on that answer.")})
                    continue
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
                if question_was_cancelled(name, result):
                    raise UserQuestionCancelled()
                answer_images.extend(drain_answer_images(executor))
            if answer_images:
                messages.append({'role': 'user', 'content': api_answer_content(
                    'Gönder ile paylaştığım işaretli görseller. OCR/bölge açıklamaları ilgili araç yanıtındadır.', answer_images)})

        logger.warning(f"Tool döngüsü üst sınıra ulaştı ({max_iter}), son yanıt döndürülüyor.")
        # Üst sınıra ulaşıldıysa son bir tools'suz çağrı ile özet yanıt al.
        # Modele açık talimat enjekte edilir: eldeki tool sonuçlarıyla SORUYU
        # yanıtlasın, boş dönmesin ("...'ı araştır' sorularında kullanıcının
        # ekranı boş kalmasın" garantisi).
        messages.append({
            "role": "user",
            "content": (
                "ARAÇ LİMİTİNE ULAŞTIN — artık araç çağıramazsın. "
                "Elindeki araç sonuçlarını kullanarak KULLANICININ ORİJİNAL SORUSUNU "
                "şimdi yanıtla. Kısa özetle yetinme: bulguları derleyip net bir cevap ver. "
                "Cevabın en sonuna [EKRANDA_GOSTER] etiketini ekle."
            ),
        })
        try:
            resp = requests.post(url, headers=headers, json={
                "model": model, "messages": messages, "max_tokens": 4096
            }, timeout=timeout)
            if resp.ok:
                content = resp.json()["choices"][0]["message"].get("content") or ""
            else:
                content = ""
        except Exception as e:
            logger.warning(f"Özet çağrısı başarısız: {e}")
            content = ""
        if not content.strip():
            # Model yine boş döndüyse (veya özet çağrısı patladıysa) son çare:
            # tool sonuçlarını PC tarafında derleyip kullanıcıya göster.
            # Böylece cevap ASLA "İşlem tamamlandı, Loglar'a bak" gibi
            # içi boş bir mesaja düşmez.
            content = self._compile_tool_results(messages)
        if clarification_question(content):
            raise RuntimeError("Açıklama sorusu limiti doldu; istek tamamlanamadı.")
        return content

    def _compile_tool_results(self, messages):
        """Tool çıktılarını kullanıcıya gösterilebilir bir derlemeye çevirir.

        Sadece _chat_with_tools'un üst-sınır yolunda, model özet üretemezse
        çağrılır. Tool sonuçlarının ilk ~2500 karakterini madde madde dizer;
        sonuç yoksa eski "işlem tamamlandı" mesajına düşer.
        """
        tool_msgs = [m for m in messages if m.get("role") == "tool"]
        parts = []
        for m in tool_msgs:
            body = (m.get("content") or "").strip()
            if not body:
                continue
            # Red/iptal/hata mesajları "bilgi" değildir, ama tamamen boş
            # kalmaktansa ne olduğunu söylemek daha dürüsttür.
            parts.append(f"• **{m.get('name', '?')}**: {body[:1200]}")
        if not parts:
            done = tr("İşlem tamamlandı (kullanılan araçlar: {tools}).").format(
                tools=", ".join(self.last_tools_used) or "?")
            return done + " " + tr("Detay için Loglar klasörüne bakabilirsiniz.")
        joined = "\n\n".join(parts)
        if len(joined) > 2500:
            joined = joined[:2500] + tr("\n... ({n} karakter kesildi)").format(
                n=len(joined) - 2500)
        header = tr(
            "(Araç limiti dolduğu için ham sonuçlar derlendi — özetlenemedi.)")
        return f"{header}\n\n{joined} [EKRANDA_GOSTER]"

    def _call_cli(self, system_prompt, user_prompt, history=None, allow_tools=True):
        """
        Registry'den seçilen CLI aracını kullanarak prompt'u stdin üzerinden gönderir.
        Araç kendi yöntemini (stdin/arg) ve model formatını registry'den alır.
        """
        if not self.settings:
            raise ValueError(tr("CLI modu için ayarlar yüklenmedi."))

        tool_key   = self.settings.get("llm_cli_tool_key", "agy (Antigravity)")
        model      = self.settings.get("llm_cli_model", "").strip() or None
        extra_args = self.settings.get("llm_cli_extra_args", "").strip()

        if self._is_codex():
            from src.llm.codex import CodexSession
            from src.tools.executor import ToolExecutor, get_openai_tools
            executor = ToolExecutor(settings=self.settings,
                                    confirm_callback=self.confirm_callback,
                                    browser_sender=self.browser_sender,
                                    question_callback=self._ask_user)
            tools = get_openai_tools() if allow_tools and self._tool_calling_enabled() else []
            workspace = self.settings.get("workspace_dir", "").strip() or os.path.expanduser("~")
            with CodexSession(workspace, extra_args=extra_args,
                              timeout=self.settings.get("codex_timeout", 180)) as session:
                return session.generate(system_prompt, user_prompt, history or [], model,
                                        tools, executor, self.last_tools_used,
                                        self._tool_max_iterations())

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
            import shlex
            cmd += shlex.split(extra_args)

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
                tr("'{cmd}' aracından çıktı alınamadı. Aracın doğru kurulduğunu ve PATH'te olduğunu kontrol edin.").format(cmd=cmd[0])
            )
        return output
