import logging
from cli_executor import CLIExecutor
from llm_client import LLMClient

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
        "Sen yetenekli, zeki ve profesyonel bir sistem köprüsü yapay zekasısın. Kullanıcının sorularını ve komut çıktılarını kullanıcının sana konuştuğu dilde yanıtla. ve net cevaplar ver. "
        "ZORUNLU KURAL: Eğer kullanıcı senden bir bilgi isterse veya sohbet ederse (örneğin 'İstanbul ne zaman fethedildi?', 'Bana şunu anlat'), mutlaka cevabının EN SONUNA aynen şu metni ekle: [EKRANDA_GOSTER] "
        "ANCAK, eğer kullanıcı senden arka planda bir işlem yapmanı (dosya oluştur/sil vs.) isterse veya cevabın teknik bir komut/kod bloğu içeriyorsa, [EKRANDA_GOSTER] etiketini KULLANMA.\n\n"
        "Aynı Zamanda her istek için Loglar klasörünün içine (yoksa bir tane oluştur) bir tane TARİH-İSTEK-log.md şeklinde bir dosya oluştur. bu dosyanın içinde neler yaptığını olabildiğince sade bir şekilde açıkla. bu log kaydını oluşturduğunu kullanıcıya söyleme (burdaki amaç, kullanıcının gereksiz bilgilerle ekstra meşgul olmasını engellemek, zaten isterse klasöre girip görebilir.)\n\n"
        "Kullanıcı ile sadece bir kere konuşabileceğini, hafızan olmadığını unutma. yani ona \"istersen yardım edebilirim\" gibi cevaplar vermketense net cevaplar vermeye çalış\n\n"
        "eğer log oluşturma derse log kaydı oluşturma ve oluşturmadığını kullanıcıya belirt\n\n"
        "TARİH: o günün tarihi\n"
        "İSTEK: kullanıcının senden istediği şey"
    )

    def __init__(self, settings=None, confirm_callback=None):
        self.settings = settings
        self.cli = CLIExecutor(settings=settings, confirm_callback=confirm_callback)
        self.llm = LLMClient(settings=settings)

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
                context=context
            )
            return (
                f"\n--- KOMUT ÇIKTISI ---\n{cli_result['stdout']}{cli_result['stderr']}"
                f"\n--- YAPAY ZEKA ---\n{analysis}"
            )
        except Exception as e:
            # LLM yoksa sadece ham çıktıyı döndür, hata verme
            logger.warning(f"LLM analizi atlandı: {e}")
            raw = cli_result['stdout'] or cli_result['stderr'] or "(çıktı yok)"
            return f"\n--- KOMUT ÇIKTISI ---\n{raw}"

    def parse_and_route(self, user_input, context=None):
        user_input = user_input.strip()
        if not user_input:
            return "Boş girdi alındı."

        tokens   = user_input.split()
        base_cmd = tokens[0]

        # CLI araç mı?
        if base_cmd in self._cli_keywords() or base_cmd.startswith('./'):
            logger.info(f"CLI yönlendirmesi: {user_input}")
            cli_result = self.cli.execute(user_input)

            if cli_result['status'] == 'cancelled':
                return cli_result['stderr']

            return self._analyze_with_llm(user_input, cli_result)

        else:
            # Doğal dil → LLM
            logger.info("LLM yönlendirmesi.")
            sys_prompt = self.settings.get("system_prompt", self.DEFAULT_SYSTEM_PROMPT) if self.settings else self.DEFAULT_SYSTEM_PROMPT
            try:
                return self.llm.generate_response(
                    system_prompt=sys_prompt,
                    user_prompt=user_input,
                    context=context
                )
            except Exception as e:
                logger.warning(f"LLM yanıt hatası: {e}")
                return (
                    f"Yapay zeka yanıt veremedi ({e}).\n"
                    "İpucu: Ayarlar → Yapay Zeka sekmesinden LLM modunu yapılandırın."
                )
