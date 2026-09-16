import shlex
import re
import logging

logger = logging.getLogger(__name__)

class SecurityManager:
    DANGEROUS_COMMANDS = {
        'rm', 'mv', 'chmod', 'chown', 'dd', 'mkfs', 'fdisk',
        'systemctl', 'kill', 'pkill', 'sudo', 'su',
    }

    def __init__(self, confirm_callback=None):
        """
        confirm_callback: (command_str) -> bool
          Çağrıldığında kullanıcıya onay sorar ve True/False döndürür.
          None ise terminal prompt kullanılır.
        """
        self.confirm_callback = confirm_callback

    def sanitize_command(self, command_str):
        try:
            return shlex.split(command_str)
        except ValueError as e:
            raise ValueError(f"Geçersiz komut sözdizimi: {e}")

    def requires_confirmation(self, tokens, allow_sudo=False):
        if not tokens:
            return False
        base_cmd = tokens[0]
        if base_cmd in self.DANGEROUS_COMMANDS:
            if base_cmd == 'sudo' and allow_sudo:
                return False   # İzin verilmiş sudo → onay gerekmez
            return True
        for token in tokens:
            if re.search(r'[&|;<>$`]', token):
                return True
        return False

    def ask_confirmation(self, summary, explanation=""):
        """
        Kullanıcıdan onay ister.

        summary: dict veya str.
          dict: {"title": str,         # Pencere başlığı ("Ekran okunsun mu?")
                 "question": str,      # Tek cümlelik soru
                 "detail": str | None} # Komut/dosya yolu gibi detay (küçük puntoyla)
          str: eski tarz ham komut metni (otomatik sade özete çevrilir).

        explanation: eski imzayla uyumluluk için tutulur.
        Döner: True (onay) / False (red).
        """
        if isinstance(summary, str):
            summary = {
                "title": "Komut çalıştırılsın mı?",
                "question": "Şu komut çalıştırılacak:",
                "detail": summary if summary else (explanation or None),
            }

        title = summary.get("title") or "Onay"
        question = summary.get("question") or ""
        detail = summary.get("detail")

        if self.confirm_callback:
            import inspect
            try:
                params = inspect.signature(self.confirm_callback).parameters
                takes_two = len(params) >= 2
            except (TypeError, ValueError):
                takes_two = False
            if takes_two:
                # Eski tarz iki-argümanlı callback: (question, detail)
                return self.confirm_callback(question, detail or "")
            return self.confirm_callback(summary)

        # Fallback: terminal (remember burada desteklenmez, her seferinde sorulur)
        while True:
            try:
                prompt = f"\n[GÜVENLİK] {title}\n"
                if question:
                    prompt += f"{question}\n"
                if detail:
                    prompt += f"Detay: {detail}\n"
                prompt += "Onaylıyor musunuz? (y/n): "
                resp = input(prompt).strip().lower()
                if resp in ('y', 'yes'):
                    return True
                if resp in ('n', 'no'):
                    return False
            except (EOFError, KeyboardInterrupt):
                return False
