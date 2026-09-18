import logging
from src.llm.cli import CLIExecutor
from src.llm.client import LLMClient
from src.core.i18n import tr
from src.core.request_log import write_request_log

logger = logging.getLogger(__name__)

# Yerleşik bash komutları — her zaman CLI'ye yönlendirilir
BUILTIN_CLI_TOOLS = {
    'ls', 'pwd', 'cat', 'echo', 'grep', 'ps', 'whoami', 'mkdir',
    'rm', 'mv', 'cp', 'find', 'df', 'du', 'top', 'htop', 'uname',
    'lsblk', 'ip', 'ping', 'curl', 'wget', 'systemctl', 'journalctl',
    'git', 'python', 'python3', 'pip', 'pip3',
}

class Router:
    DEFAULT_SYSTEM_PROMPT = (
        "Sen yetenekli, zeki ve profesyonel bir sistem köprüsü yapay zekasısın. Kullanıcının sorularını ve komut çıktılarını kullanıcının sana konuştuğu dilde yanıtla ve net cevaplar ver. "
        "ZORUNLU KURAL: Eğer kullanıcı senden bir bilgi isterse veya sohbet ederse, mutlaka cevabının EN SONUNA aynen şu metni ekle: [EKRANDA_GOSTER] "
        "ANCAK, eğer kullanıcı senden arka planda bir işlem yapmanı (dosya oluştur/sil vs.) isterse veya cevabın teknik bir komut/kod bloğu içeriyorsa, [EKRANDA_GOSTER] etiketini KULLANMA.\n\n"
        "ÖNEMLİ BİLGİ: Kullanıcı sana tarayıcısındaki bir sekme, ekranındaki bir makale, açık olan bir kodu veya bir video hakkında soru soruyorsa; bu içerik sana sistem tarafından [TARAYICIDAKİ SAYFANIN TAM METNİ], [TARAYICIDA SEÇİLEN METİN], [EKRANDAKİ DOSYANIN İÇERİĞİ] veya [VİDEONUN TAM İÇERİĞİ / ALTYAZISI] gibi etiketlerle otomatik olarak iletilmiş OLMALIDIR.\n"
        "EĞER bu etiketler sana iletilmemişse ve kullanıcı ekranındaki/sekmesindeki bir şeyi soruyorsa, ekranı doğrudan göremeyeceğini, ancak tarayıcı eklentisindeki (yapboz ikonu) 'Bu Sekmeyi Gönder' butonuna tıklayarak veya ekrandaki kısayol butonlarını kullanarak veriyi sana gönderebileceğini kibarca hatırlat.\n\n"
        "TARAYICI YÖNETİMİ: Kullanıcı tarayıcısını kontrol etmeni isterse (sekme kapat, sayfayı kaydır, yeni sekme aç vb.) veya bir mail/form cevabı yazdırıyorsa, `browser_action` aracını kullan (eklenti kurulu olmalıdır).\n\n"
        "NOT: İstek günlükleri sistem tarafından otomatik tutulur; sen Loglar klasörüne dosya yazma, log işleriyle uğraşma.\n\n"
        "Önceki konuşmaları hatırlıyorsun; kullanıcının göndermeleri ('az önce', 'ona ekle' vb.) için geçmişe bak. Net cevaplar ver."
    )

    def __init__(self, settings=None, confirm_callback=None, browser_sender=None):
        self.settings = settings
        self.cli = CLIExecutor(settings=settings, confirm_callback=confirm_callback)
        self.llm = LLMClient(settings=settings, confirm_callback=confirm_callback,
                             browser_sender=browser_sender)

    def set_browser_sender(self, browser_sender):
        """Extension server hazır olduktan sonra tarayıcı bağlantısını enjekte eder."""
        self.llm.browser_sender = browser_sender

    def _cli_keywords(self):
        """Yerleşik araçlar + kullanıcı tanımlı araçları birleştirir."""
        user_tools = set()
        if self.settings:
            user_tools = set(self.settings.get("custom_cli_tools", []))
        return BUILTIN_CLI_TOOLS | user_tools

    def _analyze_with_llm(self, command, cli_result):
        """
        CLI çıktısını LLM ile analiz eder.
        LLM yoksa veya hata alırsa sadece ham çıktıyı döndürür.
        """
        try:
            context = (
                f"Exit Code: {cli_result['exit_code']}\n"
                f"STDOUT:\n{cli_result['stdout']}\n"
                f"STDERR:\n{cli_result['stderr']}"
            )
            sys_prompt = self.settings.get("system_prompt", self.DEFAULT_SYSTEM_PROMPT) if self.settings else self.DEFAULT_SYSTEM_PROMPT
            analysis = self.llm.generate_response(
                system_prompt=sys_prompt,
                user_prompt=f"Terminalde '{command}' komutu çalıştırıldı. Sonuçları değerlendir.",
                context=context,
                use_history=False  # İç analiz: geçmişe yazma, bağlam şişirmesin
            )
            return (
                f"\n--- {tr("KOMUT ÇIKTISI")} ---\n{cli_result['stdout']}{cli_result['stderr']}"
                f"\n--- {tr("YAPAY ZEKA")} ---\n{analysis}"
            )
        except Exception as e:
            # LLM yoksa sadece ham çıktıyı döndür, hata verme
            logger.warning(f"LLM analizi atlandı: {e}")
            raw = cli_result['stdout'] or cli_result['stderr'] or tr("(çıktı yok)")
            return f"\n--- {tr("KOMUT ÇIKTISI")} ---\n{raw}"

    def _auto_log(self, user_input, response, route):
        """İstek günlüğünü PC tarafında yazar (sıfır token).

        route: "cli" veya "llm". Kullanıcı "log oluşturma/log tutma" derse
        bu istek için atlanır; ayar kapalıysa tamamen devre dışıdır.
        """
        try:
            if self.settings and not self.settings.get("auto_request_log", True):
                return
            lowered = user_input.lower()
            if any(kw in lowered for kw in ("log oluşturma", "log olusturma", "log tutma", "günlük tutma", "gunluk tutma")):
                logger.info("Kullanıcı log istemedi, istek günlüğü atlandı.")
                return
            llm_mode = self.settings.get("llm_mode", "") if self.settings else ""
            tools_used = list(getattr(self.llm, "last_tools_used", []) or [])
            write_request_log(user_input, response, tools_used=tools_used,
                              mode=route, llm_mode=llm_mode)
        except Exception as e:
            logger.warning(f"Otomatik istek günlüğü atlandı: {e}")

    def parse_and_route(self, user_input, context=None):
        user_input = user_input.strip()
        if not user_input:
            return tr("Boş girdi alındı.")

        tokens   = user_input.split()
        base_cmd = tokens[0]

        # CLI araç mı?
        if base_cmd in self._cli_keywords() or base_cmd.startswith('./'):
            logger.info(f"CLI yönlendirmesi: {user_input}")
            cli_result = self.cli.execute(user_input)

            if cli_result['status'] == 'cancelled':
                return cli_result['stderr']

            response = self._analyze_with_llm(user_input, cli_result)
            self._auto_log(user_input, response, route="cli")
            return response

        else:
            # Doğal dil → LLM
            logger.info("LLM yönlendirmesi.")
            sys_prompt = self.settings.get("system_prompt", self.DEFAULT_SYSTEM_PROMPT) if self.settings else self.DEFAULT_SYSTEM_PROMPT
            try:
                response = self.llm.generate_response(
                    system_prompt=sys_prompt,
                    user_prompt=user_input,
                    context=context
                )
            except Exception as e:
                logger.warning(f"LLM yanıt hatası: {e}")
                fail = tr("Yapay zeka yanıt veremedi ({e}).").format(e=e)
                hint = tr("İpucu: Ayarlar → Yapay Zeka sekmesinden LLM modunu yapılandırın.")
                return fail + "\n" + hint
            self._auto_log(user_input, response, route="llm")
            return response
