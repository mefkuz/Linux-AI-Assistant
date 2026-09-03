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

    def ask_confirmation(self, command_str):
        """
        GUI callback varsa onu kullanır (popup), yoksa terminal prompt.
        """
        if self.confirm_callback:
            return self.confirm_callback(command_str)
        # Fallback: terminal
        while True:
            try:
                resp = input(
                    f"\n[GÜVENLİK] Kritik komut: '{command_str}'\n"
                    "Onaylıyor musunuz? (y/n): "
                ).strip().lower()
                if resp in ('y', 'yes'):
                    return True
                if resp in ('n', 'no'):
                    return False
            except (EOFError, KeyboardInterrupt):
                return False
