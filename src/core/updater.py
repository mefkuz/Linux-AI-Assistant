"""GitHub Releases tabanlı güncelleme denetimi ve kurulumu.

Akış: check_for_updates() → yeni sürüm varsa GUI diyalog gösterir →
kullanıcı onaylarsa perform_update() ile `git pull --ff-only` + bağımlılık
tazeleme yapılır. Sadece stdlib kullanır (ek bağımlılık yok).
"""
import json
import logging
import os
import shutil
import subprocess
import sys
import urllib.request

from src.core.i18n import tr
from src.core.version import __version__

logger = logging.getLogger(__name__)

GITHUB_OWNER = "mefkuz"
GITHUB_REPO = "Linux-AI-Assistant"
API_LATEST = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"
HTML_RELEASES = f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}/releases"


def parse_version(s):
    """'v1.2.3' → (1, 2, 3). Bozuk girdide (0,)."""
    try:
        parts = []
        for p in str(s).strip().lstrip("vV").split("."):
            num = "".join(ch for ch in p if ch.isdigit())
            parts.append(int(num) if num else 0)
        return tuple(parts) or (0,)
    except (TypeError, ValueError):
        return (0,)


def _pad(a, b):
    n = max(len(a), len(b))
    return a + (0,) * (n - len(a)), b + (0,) * (n - len(b))


def is_newer(latest, current):
    """latest sürümü current'tan büyük mü?"""
    return _pad(parse_version(latest), parse_version(current))[0] > _pad(
        parse_version(latest), parse_version(current))[1]


def check_for_updates(current=None, timeout=10):
    """GitHub'daki son release'i sorgular. Ağ hatasında ok=False döner."""
    current = current or __version__
    try:
        req = urllib.request.Request(
            API_LATEST,
            headers={"User-Agent": "Linux-AI-Assistant",
                     "Accept": "application/vnd.github+json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        logger.warning(f"Güncelleme denetimi başarısız: {e}")
        return {"ok": False, "error": tr("GitHub'a ulaşılamadı: {e}").format(e=e),
                "current_version": current, "update_available": False}
    tag = str(data.get("tag_name", "") or "")
    latest = tag.lstrip("vV") or "0"
    return {
        "ok": True,
        "update_available": is_newer(latest, current),
        "current_version": current,
        "latest_version": latest,
        "tag": tag,
        "release_notes": data.get("body", "") or "",
        "html_url": data.get("html_url", "") or HTML_RELEASES,
    }


def is_git_checkout(project_root):
    return os.path.isdir(os.path.join(project_root, ".git"))


def perform_update(project_root, timeout=180):
    """`git pull --ff-only` + pip bağımlılık tazeleme. Sözlük döner:
    {"ok", "message", "restart_needed"}."""
    if not is_git_checkout(project_root):
        return {"ok": False, "restart_needed": False,
                "message": tr("Bu klasör bir Git deposu değil. "
                              "Güncellemek için yeni sürümü manuel indirin.")}
    if shutil.which("git") is None:
        return {"ok": False, "restart_needed": False,
                "message": tr("Git bulunamadı (PATH'te yok).")}
    try:
        r = subprocess.run(
            ["git", "-C", project_root, "pull", "--ff-only"],
            capture_output=True, text=True, timeout=timeout)
    except Exception as e:
        return {"ok": False, "restart_needed": False,
                "message": tr("Git güncellemesi başarısız: {e}").format(e=e)}
    out = (r.stdout + r.stderr).strip()
    if r.returncode != 0:
        return {"ok": False, "restart_needed": False,
                "message": tr("Git güncellemesi başarısız: {e}").format(
                    e=out[-500:] if out else r.returncode)}
    if "Already up to date" in out or "Zaten güncel" in out:
        return {"ok": True, "restart_needed": False, "message": tr("Zaten güncel.")}
    # requirements değişmiş olabilir — aynı Python ile tazele (best-effort).
    req = os.path.join(project_root, "requirements.txt")
    if os.path.isfile(req):
        try:
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "-r", req, "--quiet"],
                capture_output=True, text=True, timeout=timeout, cwd=project_root)
        except Exception as e:
            logger.warning(f"Bağımlılık tazeleme atlandı: {e}")
            return {"ok": True, "restart_needed": True,
                    "message": tr("Bağımlılıklar güncellenemedi (devam ediliyor): {e}").format(e=e)}
    return {"ok": True, "restart_needed": True, "message": tr("Güncelleme tamamlandı.")}
