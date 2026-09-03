import subprocess
import logging
import os
from security import SecurityManager

logger = logging.getLogger(__name__)

class CLIExecutor:
    def __init__(self, settings=None, confirm_callback=None):
        self.settings = settings
        self.security = SecurityManager(confirm_callback=confirm_callback)

    def execute(self, command_str):
        try:
            tokens = self.security.sanitize_command(command_str)
            if not tokens:
                raise ValueError("Boş komut algılandı.")

            allow_sudo = self.settings.get("allow_sudo", False) if self.settings else False
            require_confirm = self.settings.get("require_confirm_on_write", True) if self.settings else True

            if require_confirm and self.security.requires_confirmation(tokens, allow_sudo=allow_sudo):
                if not self.security.ask_confirmation(command_str):
                    return {
                        "status": "cancelled",
                        "stdout": "",
                        "stderr": "İşlem kullanıcı tarafından iptal edildi.",
                        "exit_code": -1
                    }

            workspace = ""
            if self.settings:
                workspace = self.settings.get("workspace_dir", "").strip()
            cwd = workspace if workspace and os.path.isdir(workspace) else os.path.expanduser("~")

            logger.info(f"Çalıştırılıyor: {tokens} (cwd={cwd})")

            result = subprocess.run(
                tokens,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                shell=False,
                cwd=cwd
            )

            return {
                "status": "success" if result.returncode == 0 else "error",
                "stdout": result.stdout,
                "stderr": result.stderr,
                "exit_code": result.returncode
            }

        except ValueError as ve:
            logger.error(f"Ayrıştırma/Güvenlik hatası: {ve}")
            return {"status": "error", "stdout": "", "stderr": str(ve), "exit_code": 1}
        except FileNotFoundError:
            cmd_name = command_str.split()[0] if command_str else "?"
            return {"status": "error", "stdout": "",
                    "stderr": f"Komut bulunamadı: '{cmd_name}'", "exit_code": 127}
        except Exception as e:
            logger.exception("Komut çalıştırılırken hata.")
            return {"status": "error", "stdout": "", "stderr": str(e), "exit_code": 1}
