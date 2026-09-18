import os
import shlex
import re
import logging
from src.core.i18n import tr

logger = logging.getLogger(__name__)

# Shell komut satırına gömülü mutlak yolları yakalamak için ikincil tarama.
# Örn: python3 -c "open('/etc/passwd').read()" — token tabanlı çözümleme
# bunu tek bir "-c" argümanı olarak görür, regex ise içindeki yolu bulur.
_EMBEDDED_ABS_PATH_RE = re.compile(
    r"""(?P<q>['"])(?P<path>/[^'"\s;|&<>$`]+)(?P=q)"""
    r"""|(?:^|[\s;|&<>\'"`$=])(?P<syspath>/(?:etc|home|root|var|usr|tmp|opt|sys|proc|dev|boot|mnt|srv|run)(?:/[^\s;|&<>\'"`$]*)*)"""
    r"""|(?P<home>~(?:/[^\s;|&<>\'"`$]*)*)"""
)

_SHELL_OPERATORS = {
    "|", "&", ";", "&&", "||", ">", ">>", "<", "<<",
    "2>", "2>>", "&>", "|&",
}

_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def is_path_inside_workspace(path, workspace):
    """`path` (mutlak ya da workspace'e göre göreli) workspace içinde mi?"""
    if not workspace or not os.path.isdir(workspace):
        return False
    ws_real = os.path.realpath(workspace)
    candidate = path if os.path.isabs(path) else os.path.join(ws_real, path)
    real = os.path.realpath(candidate)
    return real == ws_real or real.startswith(ws_real + os.sep)


def _extract_embedded_paths(command_str):
    """Komut metnine gömülü mutlak yol adaylarını regex ile çıkarır."""
    found = []
    for m in _EMBEDDED_ABS_PATH_RE.finditer(command_str or ""):
        p = m.group("path") or m.group("syspath") or m.group("home")
        if p:
            found.append(p)
    return found


def detect_workspace_escapes(command_str, workspace):
    """Shell komutundaki workspace-dışı yol girişimlerini sezer.

    Döner: workspace dışına taşan yol string'lerinin listesi (sıralı, tekrarsız).
    Boş liste = kaçış belirtisi yok.

    İki katmanlı sezgisel:
      1. Token tabanlı: shlex ile böl, flag/operatör/atama dışındakileri
         workspace'e göre çözümle, dışarı taşanları topla.
      2. Gömülü yol taraması: -c "..." gibi tek argümanların içine
         gizlenmiş mutlak yolları regex ile yakala.
    """
    if not workspace or not os.path.isdir(workspace):
        return []
    ws_real = os.path.realpath(workspace)
    outside = []

    def _is_outside(candidate):
        try:
            expanded = os.path.expandvars(os.path.expanduser(candidate))
        except Exception:
            expanded = candidate
        if not expanded:
            return False
        abs_path = expanded if os.path.isabs(expanded) else os.path.join(ws_real, expanded)
        try:
            real = os.path.realpath(abs_path)
        except Exception:
            return False
        return not (real == ws_real or real.startswith(ws_real + os.sep))

    # 1. Token tabanlı çözümleme
    try:
        tokens = shlex.split(command_str or "", posix=True)
    except ValueError:
        tokens = (command_str or "").split()
    for tok in tokens[1:]:  # tokens[0] = komutun kendisi
        if not tok or tok in _SHELL_OPERATORS:
            continue
        if tok.startswith("-") and "/" not in tok and "=" not in tok:
            continue  # -la, --all gibi flag'ler
        if _ASSIGNMENT_RE.match(tok) and "/" not in tok:
            continue  # VAR=değer atamaları
        # --output=/tmp/x gibi flag=içinde-yol durumları: '=' sonrası yolu dene
        candidates = [tok]
        if "=" in tok and "/" in tok:
            candidates.append(tok.split("=", 1)[1])
        for cand in candidates:
            cand = cand.strip().strip("'\"")
            if not cand:
                continue
            if _is_outside(cand) and cand not in outside:
                outside.append(cand)

    # 2. Gömülü yol taraması (token çözümlemenin kaçırdıkları için)
    for cand in _extract_embedded_paths(command_str):
        if _is_outside(cand) and cand not in outside:
            outside.append(cand)

    return outside


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
            raise ValueError(tr("Geçersiz komut sözdizimi: {e}").format(e=e))

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
                 "detail": str | None, # Komut/dosya yolu gibi detay (küçük puntoyla)
                 "workspace_escape": bool,  # True ise GUI'de WorkspaceEscapeDialog,
                                            # terminalde vurgulu prompt kullanılır
                 ...}  # outside_paths/command/tool: kaçış diyaloğunun ek alanları
          str: eski tarz ham komut metni (otomatik sade özete çevrilir).

        explanation: eski imzayla uyumluluk için tutulur.
        Döner: True (onay) / False (red).
        """
        if isinstance(summary, str):
            summary = {
                "title": tr("Komut çalıştırılsın mı?"),
                "question": tr("Şu komut çalıştırılacak:"),
                "detail": summary if summary else (explanation or None),
            }

        title = summary.get("title") or tr("Onay")
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
        # Workspace kaçış uyarıları terminalde de dikkat çekici gösterilir.
        escape = bool(summary.get("workspace_escape"))
        while True:
            try:
                if escape:
                    prompt = f"\n!!! [{tr('GÜVENLİK')}] {title} !!!\n"
                else:
                    prompt = f"\n[{tr('GÜVENLİK')}] {title}\n"
                if question:
                    prompt += f"{question}\n"
                if detail:
                    prompt += tr("Detay: ") + f"{detail}\n"
                prompt += tr("Onaylıyor musunuz? (y/n): ")
                resp = input(prompt).strip().lower()
                if resp in ('y', 'yes'):
                    return True
                if resp in ('n', 'no'):
                    return False
            except (EOFError, KeyboardInterrupt):
                return False
